"""Best-effort warmup pings for LLM / STT / TTS / embeddings.

Fires lightweight no-op-ish requests against each configured service so the
first user-facing interaction doesn't pay the cold-start tax (kv cache
allocation, CUDA graph capture for new prompt shapes, model unpickling on
lazy-loaded servers, HTTP keep-alive establishment).

Wired into the pipeline start path via `asyncio.create_task(...)` so it
runs concurrently with the rest of pipeline setup and the agent's greeting
generation. By the time the user types/speaks their first message, all
paths are warm.

Each warmup is independently try/except'd — a single failed warmup never
breaks the session. Disable globally with `KB_WARMUP_ENABLED=false`.
"""

import asyncio
import os
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext

WARMUP_ENABLED = os.environ.get("KB_WARMUP_ENABLED", "true").lower() != "false"

# Single short prompt that exercises prefill + decode on the LLM with a
# typical voice-agent shape. Keeping it tiny keeps the warmup under 1s on
# even slow local stacks.
_LLM_WARMUP_SYSTEM = "You are a warmup helper. Reply with exactly: ok"
_LLM_WARMUP_USER = "ping"


async def _warmup_llm(llm: Any) -> None:
    """Single small inference to warm the LLM service connection + KV cache.

    Uses the same `run_inference` API as the QA system and DocCard extraction,
    so this is the exact code path the application will hit later.
    """
    try:
        context = LLMContext()
        context.set_messages([{"role": "user", "content": _LLM_WARMUP_USER}])
        await llm.run_inference(context, system_instruction=_LLM_WARMUP_SYSTEM)
        logger.debug("LLM warmup completed")
    except Exception as e:
        # Don't propagate — the real call will surface any genuine config issue.
        logger.debug(f"LLM warmup skipped: {e}")


async def _warmup_embeddings(organization_id: int, created_by_user_id: int | None) -> None:
    """Embed a 1-token query to warm the embeddings endpoint.

    Uses the same resolution path as the rest of the KB pipeline so a
    misconfigured provider/api_key here means the real retrieval call would
    also fail — we just want to find that out earlier (asynchronously) and
    have the model loaded for the user's first KB hit.
    """
    try:
        from api.db import db_client
        from api.services.gen_ai import OpenAIEmbeddingService

        if created_by_user_id is None:
            return

        user_config = await db_client.get_user_configurations(created_by_user_id)
        if not user_config.embeddings:
            return

        api_key = user_config.embeddings.api_key
        if isinstance(api_key, list):
            import random

            api_key = random.choice(api_key)

        service = OpenAIEmbeddingService(
            db_client=db_client,
            api_key=api_key,
            model_id=user_config.embeddings.model,
            base_url=getattr(user_config.embeddings, "base_url", None),
        )
        await service.embed_texts(["warmup"])
        logger.debug("Embeddings warmup completed")
    except Exception as e:
        logger.debug(f"Embeddings warmup skipped: {e}")


def schedule_pipeline_warmup(
    *,
    llm: Any | None,
    inference_llm: Any | None,
    organization_id: int,
    created_by_user_id: int | None,
) -> asyncio.Task | None:
    """Kick off warmup pings in the background and return the task.

    The caller does NOT need to await this — pipeline setup continues
    concurrently. Returning the task lets the caller optionally await it for
    diagnostics (or cancel during teardown).
    """
    if not WARMUP_ENABLED:
        return None

    async def _run_all() -> None:
        tasks: list[asyncio.Task] = []

        # LLM path. For realtime pipelines, inference_llm is the side-channel
        # text LLM used for variable extraction etc.; warm both if distinct.
        seen_llms: set[int] = set()
        for candidate in (llm, inference_llm):
            if candidate is None:
                continue
            if id(candidate) in seen_llms:
                continue
            seen_llms.add(id(candidate))
            tasks.append(asyncio.create_task(_warmup_llm(candidate)))

        # Embeddings path (KB retrieval). Only relevant for chunked-mode docs;
        # full_document mode never hits this. Warmup is cheap either way.
        tasks.append(
            asyncio.create_task(
                _warmup_embeddings(organization_id, created_by_user_id)
            )
        )

        if tasks:
            # gather with return_exceptions so one failure doesn't cancel siblings
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(
                f"Pipeline warmup finished for org={organization_id}, "
                f"user={created_by_user_id} ({len(tasks)} services pinged)"
            )

    return asyncio.create_task(_run_all())
