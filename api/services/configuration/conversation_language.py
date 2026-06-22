"""Per-agent conversation-language profiles.

A "conversation language" is a single per-agent choice (English / Urdu / Hindi /
…) that orchestrates three things so the user doesn't hand-wire them:

  1. the reply-language instruction injected into the system prompt,
  2. the STT config (provider + language) for the agent's calls,
  3. the TTS config (provider + voice) for the agent's calls.

English is the default and a pure no-op (today's behavior). Each non-English
profile carries FULL STT/TTS config dicts (provider + model + language/voice +
base_url) so switching language switches providers — an Urdu agent and an English
agent coexist under one org Models config. Profiles reference only self-hosted
local providers.

Adding a language = one entry in ``PROFILES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_LANGUAGE = "english"

# Key stored in workflow_configurations.
CONVERSATION_LANGUAGE_KEY = "conversation_language"


@dataclass(frozen=True)
class ConversationLanguageProfile:
    """One selectable conversation language and the config it wires up."""

    code: str
    label: str
    # Appended to the system prompt; None = no instruction (English).
    reply_instruction: Optional[str]
    # Full STT/TTS config dicts (provider + fields) or None = use org default.
    stt_override: Optional[dict]
    tts_override: Optional[dict]


PROFILES: dict[str, ConversationLanguageProfile] = {
    "english": ConversationLanguageProfile(
        code="english",
        label="English",
        reply_instruction=None,
        stt_override=None,
        tts_override=None,
    ),
    "urdu": ConversationLanguageProfile(
        code="urdu",
        label="Urdu",
        # The Urdu voice is a MALE voice clone, so instruct masculine grammar.
        # Keep the Urdu simple/gentle and mixed with common English words (how
        # people actually speak in Pakistan) — heavy literary Arabic/Farsi
        # vocabulary also mispronounces on the TTS. Native script for the LLM.
        reply_instruction=(
            "Respond only in simple, gentle, everyday Urdu (Nastaliq script), "
            "speaking as a MALE — always use masculine Urdu grammar (e.g. "
            "'karta hoon', 'sakta hoon', not 'karti hoon'/'sakti hoon'). "
            "Use easy conversational words mixed with common English words the "
            "way people speak in Pakistan; avoid heavy or literary Arabic/Farsi "
            "vocabulary."
        ),
        stt_override={
            "provider": "indic_conformer",
            "model": "ai4bharat/indic-conformer-600m-multilingual",
            "language": "ur",
            "base_url": "http://indic-stt:8000/v1",
            "output_script": "native",
        },
        # Cleaner voice-cloned male Urdu via XTTS-Urdu. Piper remains selectable
        # as a low-latency fallback Voice provider.
        tts_override={
            "provider": "xtts_urdu",
            "model": "xtts-v2-urdu-ft",
            "base_url": "http://xtts-urdu:8000",
        },
    ),
    "hindi": ConversationLanguageProfile(
        code="hindi",
        label="Hindi",
        reply_instruction="Respond only in Hindi, using Devanagari script.",
        stt_override={
            "provider": "indic_conformer",
            "model": "ai4bharat/indic-conformer-600m-multilingual",
            "language": "hi",
            "base_url": "http://indic-stt:8000/v1",
            "output_script": "native",
        },
        tts_override={
            "provider": "speaches",
            "model": "kokoro",
            "voice": "hf_alpha",
            "base_url": "http://kokoro:8880/v1",
            "speed": 1.0,
        },
    ),
}


def get_profile(code: Optional[str]) -> ConversationLanguageProfile:
    """Return the profile for ``code``, falling back to English for
    unknown/missing values (regression-safe)."""
    if not code:
        return PROFILES[DEFAULT_LANGUAGE]
    return PROFILES.get(code.lower(), PROFILES[DEFAULT_LANGUAGE])


def supported_languages() -> list[dict]:
    """[{"code","label"}] for the agent dropdown."""
    return [{"code": p.code, "label": p.label} for p in PROFILES.values()]
