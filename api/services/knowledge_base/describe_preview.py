"""Service that drafts a description for an uploaded document.

Flow: file hash -> Redis cache lookup -> MPS parse on miss -> two-step LLM
call (S2: structured profile -> narrative prose) -> short description.
The MPS-parse payload is cached so the background worker can reuse it
instead of re-parsing during Upload & Process.
"""

import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional

from loguru import logger

from api.services.knowledge_base.describe_prompts import (
    SYSTEM_PROMPT,
    build_describe_prompt,
    build_describe_prompt_step2,
)
from api.services.knowledge_base.llm_resolution import resolve_kb_llm
from api.services.knowledge_base.parse_cache import (
    get_cached_parse,
    set_cached_parse,
)
from api.services.mps_service_key_client import mps_service_key_client
from api.services.pipecat.service_factory import create_llm_service_from_provider
from pipecat.processors.aggregators.llm_context import LLMContext

MAX_DESCRIPTION_CHARS = 600
PREVIEW_RETRIEVAL_MODE = "full_document"  # Cheaper than chunked; we only need text.


@dataclass
class DescribePreviewResult:
    description: str
    from_cache: bool


class DescribePreviewError(Exception):
    """Raised when the preview flow fails. `code` is one of:
        'parse_failed'  -- MPS could not extract text
        'llm_failed'    -- LLM call or post-processing failed
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _sha256_of_file(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def _document_text_from_payload(payload: dict) -> str:
    full_text = payload.get("full_text")
    if full_text:
        return full_text
    chunks = payload.get("chunks") or []
    return "\n\n".join(c.get("chunk_text", "") for c in chunks)


def _post_process(raw: str) -> str:
    text = (raw or "").strip()
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS].rstrip()
    return text


async def _run_llm(llm, user_prompt: str) -> str:
    context = LLMContext()
    context.set_messages([{"role": "user", "content": user_prompt}])
    raw = await llm.run_inference(context, system_instruction=SYSTEM_PROMPT) or ""
    return raw.strip()


async def generate_description_preview(
    *,
    file_path: str,
    filename: str,
    mime_type: str,
    doc_type: Optional[str],
    intended_use: Optional[Iterable[str]],
    user_id: Optional[int],
) -> DescribePreviewResult:
    file_hash = _sha256_of_file(file_path)

    cached = await get_cached_parse(file_hash)
    if cached is not None:
        logger.info(f"describe-preview cache HIT for {file_hash[:12]}...")
        payload = cached
        from_cache = True
    else:
        logger.info(f"describe-preview cache MISS for {file_hash[:12]}...")
        try:
            payload = await mps_service_key_client.process_document(
                file_path=file_path,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
                retrieval_mode=PREVIEW_RETRIEVAL_MODE,
            )
        except Exception as exc:
            logger.warning(f"describe-preview MPS parse failed: {exc}")
            raise DescribePreviewError("parse_failed", str(exc)) from exc
        await set_cached_parse(file_hash, payload)
        from_cache = False

    document_text = _document_text_from_payload(payload)
    if not document_text.strip():
        raise DescribePreviewError("parse_failed", "MPS returned no text")

    provider, model, api_key, kwargs = await resolve_kb_llm(user_id)
    intended_list = list(intended_use) if intended_use else None

    try:
        llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
        # S2 step 1: profile.
        profile_prompt = build_describe_prompt(document_text, doc_type, intended_list)
        profile_json = await _run_llm(llm, profile_prompt)
        # S2 step 2: prose.
        narrative_prompt = build_describe_prompt_step2(profile_json)
        raw = await _run_llm(llm, narrative_prompt)
    except DescribePreviewError:
        raise
    except Exception as exc:
        logger.warning(f"describe-preview LLM call failed: {exc}")
        raise DescribePreviewError("llm_failed", str(exc)) from exc

    description = _post_process(raw)
    if not description:
        raise DescribePreviewError("llm_failed", "LLM returned empty description")

    return DescribePreviewResult(description=description, from_cache=from_cache)
