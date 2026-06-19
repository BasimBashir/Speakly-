"""Shared LLM resolution for KB-side LLM calls.

Resolves (provider, model, api_key, kwargs) from the effective AI model
configuration — org-level v2 config first, then legacy v1 user config — and
finally falls back to the Dograh MPS default tier when no LLM is configured.
"""

import os
import random
from typing import Optional


async def resolve_kb_llm(
    user_id: Optional[int],
    organization_id: Optional[int] = None,
) -> tuple[str, str, Optional[str], dict]:
    """Return (provider, model, api_key, kwargs) for KB-side LLM calls.

    Goes through the v2 resolver so it honours the org-level
    MODEL_CONFIGURATION_V2 a user may have migrated to; the resolver itself
    falls back to the legacy v1 user configuration when no v2 config exists.
    """
    if not user_id and not organization_id:
        return _dograh_default()

    # Imported lazily to avoid a heavy import at module load (mirrors the
    # pattern used in api/tasks/knowledge_base_processing.py).
    from api.services.configuration.ai_model_configuration import (
        get_resolved_ai_model_configuration,
    )

    resolved = await get_resolved_ai_model_configuration(
        user_id=user_id,
        organization_id=organization_id,
    )
    llm = resolved.effective.llm
    if llm is None:
        return _dograh_default()

    # provider is a ServiceProviders(str, Enum); normalise to its string value.
    provider = getattr(llm, "provider", "dograh")
    provider = getattr(provider, "value", provider)
    model = getattr(llm, "model", "default")
    api_key = getattr(llm, "api_key", None)
    if isinstance(api_key, list):
        api_key = random.choice(api_key) if api_key else None

    kwargs: dict = {}
    if provider == "azure":
        kwargs["endpoint"] = getattr(llm, "endpoint", "") or ""
    elif provider in ("openrouter", "speaches"):
        base_url = getattr(llm, "base_url", None)
        if base_url:
            kwargs["base_url"] = base_url

    return provider, model, api_key, kwargs


def _dograh_default() -> tuple[str, str, Optional[str], dict]:
    tier = os.environ.get("KB_DOC_CARD_MODEL_TIER", "default")
    return "dograh", tier, None, {}
