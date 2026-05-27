"""DocCard extraction service.

Runs after chunks/full_text are persisted. Calls the LLM via the
model-agnostic create_llm_service_from_provider factory. Resolves the
provider from the document creator's UserConfiguration; falls back to
the Dograh MPS default tier.
"""

import os
import random
from typing import Optional

from loguru import logger
from pydantic import ValidationError

from opentelemetry import trace

from api.db import db_client
from pipecat.processors.aggregators.llm_context import LLMContext

from api.schemas.doc_card import DocCard
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.knowledge_base.extraction_input import build_extraction_input
from api.services.pipecat.service_factory import create_llm_service_from_provider
from api.services.pipecat.tracing_config import ensure_tracing

EXTRACTION_BUDGET_CHARS = int(os.environ.get("KB_DOC_CARD_BUDGET_CHARS", "400000"))


SYSTEM_PROMPT = (
    "You extract structured DocCards from business documents to power "
    "voice AI agents during calls. Stay grounded in the document — do not "
    "invent facts. Extract content in the document's source language, except "
    "the 'topics' field which is always lowercased English keywords."
)

USER_PROMPT_TEMPLATE = """\
Document type: {doc_type}
Intended use: {intended_use}
User's description of this doc:
{user_description}

Extract the DocCard as JSON matching this exact schema:
{{
  "title": "string",
  "summary_150_words": "string (~150 words)",
  "key_facts": ["string", ...],
  "entities": {{ "people": [...], "organizations": [...], "products": [...], "locations": [...], "dates": [...] }},
  "numbers_and_pricing": ["string", ...],
  "faqs": [{{"q": "...", "a": "..."}}, ...],
  "suggested_agent_uses": ["string", ...],
  "topics": ["lowercase-english-keyword", ...]
}}

Return ONLY the JSON object. No prose before or after.

<document>
{document}
</document>"""


async def extract_doc_card_for_document(document_id: int) -> Optional[DocCard]:
    """Extract the DocCard for a document and persist it.

    Returns the DocCard on success, None on skip (no description / no text).
    Raises on unrecoverable errors after one repair attempt.
    """
    document = await db_client.get_document_by_id(document_id)
    if not document:
        logger.error(f"DocCard extraction: document {document_id} not found")
        return None

    if not document.user_description:
        logger.info(
            f"DocCard extraction: skipping doc {document_id} — no user_description (legacy doc)"
        )
        return None

    provider, model, api_key, kwargs = await _resolve_extraction_llm(document.created_by)
    if provider != "dograh" and not api_key:
        await db_client.update_document_status(
            document_id,
            "completed",
            error_message="LLM provider not configured for extraction. Set your API key in Model Configurations.",
        )
        return None

    if document.retrieval_mode == "full_document" and document.full_text:
        full_text = document.full_text
        chunks: list[dict] = []
    else:
        full_text = None
        raw_chunks = await db_client.get_chunks_for_document(
            document_id=document_id, organization_id=document.organization_id
        )
        chunks = [
            {"chunk_text": c.chunk_text, "chunk_index": c.chunk_index} for c in raw_chunks
        ]

    extraction_input = build_extraction_input(
        full_text=full_text, chunks=chunks, budget_chars=EXTRACTION_BUDGET_CHARS
    )

    if not extraction_input.strip():
        await db_client.update_document_status(
            document_id,
            "completed",
            error_message="no_text_content",
        )
        return None

    user_prompt = USER_PROMPT_TEMPLATE.format(
        doc_type=document.doc_type or "other",
        intended_use=", ".join(document.intended_use or []) or "unspecified",
        user_description=document.user_description,
        document=extraction_input,
    )

    if ensure_tracing():
        tracer = trace.get_tracer("knowledge_base")
        with tracer.start_as_current_span("kb.doc_card_extraction") as span:
            span.set_attribute("doc_id", document_id)
            span.set_attribute("doc_type", document.doc_type or "other")
            span.set_attribute("model_provider", provider)
            span.set_attribute("model_id", model)
            span.set_attribute(
                "extraction_mode",
                "full_text" if full_text else "stitched_sample",
            )
            span.set_attribute("input_chars", len(extraction_input))
            llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
            try:
                card = await _call_and_validate(llm, user_prompt, repair_allowed=True)
                span.set_attribute("final_status", "success")
            except Exception as e:
                span.set_attribute("final_status", "failed")
                span.record_exception(e)
                raise
    else:
        llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)
        card = await _call_and_validate(llm, user_prompt, repair_allowed=True)

    await db_client.update_doc_card(
        document_id=document_id,
        doc_card=card.model_dump(),
        topics=card.topics,
    )

    from api.enums import PostHogEvent
    from api.services.posthog_client import capture_event

    capture_event(
        distinct_id=str(document.created_by) if document.created_by else f"org_{document.organization_id}",
        event=PostHogEvent.KNOWLEDGE_BASE_DOC_CARD_GENERATED,
        properties={
            "document_id": document.id,
            "doc_type": document.doc_type,
            "organization_id": document.organization_id,
            "model_provider": provider,
            "model_id": model,
        },
    )

    logger.info(f"DocCard extracted for document {document_id}")
    return card


def _build_repair_prompt(error: Exception) -> str:
    return (
        "Your previous response failed validation with this error:\n"
        f"{error}\n\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "title": "string",\n'
        '  "summary_150_words": "string (~150 words)",\n'
        '  "key_facts": ["string", ...],\n'
        '  "entities": { "people": [...], "organizations": [...], "products": [...], "locations": [...], "dates": [...] },\n'
        '  "numbers_and_pricing": ["string", ...],\n'
        '  "faqs": [{"q": "...", "a": "..."}, ...],\n'
        '  "suggested_agent_uses": ["string", ...],\n'
        '  "topics": ["lowercase-english-keyword", ...]\n'
        "}\n\n"
        "Use the same document content you analyzed before. Return ONLY the JSON object."
    )


async def _call_and_validate(
    llm, user_prompt: str, *, repair_allowed: bool
) -> DocCard:
    context = LLMContext()
    context.set_messages([{"role": "user", "content": user_prompt}])
    raw = await llm.run_inference(context, system_instruction=SYSTEM_PROMPT) or ""
    try:
        parsed = parse_llm_json(raw)
        return DocCard.model_validate(parsed)
    except (ValueError, ValidationError) as e:
        if not repair_allowed:
            logger.error(f"DocCard extraction validation failed after repair: {e}")
            raise RuntimeError("DocCard extraction failed after repair attempt") from e
        logger.warning(f"DocCard extraction first attempt failed, retrying: {e}")
        repair_prompt = _build_repair_prompt(error=e)
        return await _call_and_validate(llm, repair_prompt, repair_allowed=False)


async def _resolve_extraction_llm(
    created_by_user_id: Optional[int],
) -> tuple[str, str, Optional[str], dict]:
    """Resolve (provider, model, api_key, kwargs) for the extraction call.

    Mirrors resolve_user_llm_config in api/services/workflow/qa/llm_config.py.
    Falls back to Dograh MPS default tier when user config is absent.
    """
    if not created_by_user_id:
        return _dograh_default()

    user_configuration = await db_client.get_user_configurations(created_by_user_id)
    llm_config = user_configuration.model_dump(exclude_none=True).get("llm")
    if not llm_config:
        return _dograh_default()

    provider = llm_config.get("provider", "dograh")
    model = llm_config.get("model", "default")
    api_key = llm_config.get("api_key")
    if isinstance(api_key, list):
        api_key = random.choice(api_key)

    kwargs: dict = {}
    if provider == "azure":
        kwargs["endpoint"] = llm_config.get("endpoint", "")
    elif provider == "openrouter" and llm_config.get("base_url"):
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


def _dograh_default() -> tuple[str, str, Optional[str], dict]:
    tier = os.environ.get("KB_DOC_CARD_MODEL_TIER", "default")
    return "dograh", tier, None, {}
