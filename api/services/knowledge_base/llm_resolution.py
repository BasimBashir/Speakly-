"""Shared LLM resolution for KB-side LLM calls.

Resolves (provider, model, api_key, kwargs) from the document creator's
UserConfiguration, falling back to the Dograh MPS default tier when no
LLM is configured.
"""

import os
import random
from typing import Optional

from api.db import db_client


async def resolve_kb_llm(
    user_id: Optional[int],
) -> tuple[str, str, Optional[str], dict]:
    """Return (provider, model, api_key, kwargs) for KB-side LLM calls."""
    if not user_id:
        return _dograh_default()

    user_configuration = await db_client.get_user_configurations(user_id)
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
    elif provider in ("openrouter", "speaches") and llm_config.get("base_url"):
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


def _dograh_default() -> tuple[str, str, Optional[str], dict]:
    tier = os.environ.get("KB_DOC_CARD_MODEL_TIER", "default")
    return "dograh", tier, None, {}
