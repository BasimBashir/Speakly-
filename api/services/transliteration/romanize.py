"""Romanize native Indic-script text (Hindi/Urdu/…) to Latin for display.

Used to show transcripts / chat text in Roman ("Kya haal hai") when a workflow's
STT ``output_script`` is set to ``"roman"``. Native script is what STT, the LLM,
and TTS use end-to-end; this transform is **display-only and off the real-time
audio path**.

Implementation: uroman (pure-Python, Apache-2.0; no torch/fairseq; works on
Python 3.13). The original AI4Bharat IndicXlit plan was dropped because its
``fairseq`` dependency does not import on Python 3.13. The LLM (Qwen3) reads
Roman input on its own, so no Roman→native engine is needed — only this one
native→Roman direction for display.

Failures degrade to returning the input unchanged so a romanization hiccup never
breaks a turn.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from loguru import logger

# Map our ISO 639-1 language codes to uroman's ISO 639-3 codes.
_LCODE = {"hi": "hin", "ur": "urd"}


@lru_cache(maxsize=1)
def _engine():
    from uroman import Uroman

    return Uroman()


def to_roman(text: str, lang: Optional[str] = None) -> str:
    """Return ``text`` romanized to Latin. Returns input unchanged on any error.

    ``lang`` is an ISO 639-1 code (e.g. ``"hi"``, ``"ur"``); when known it is
    passed to uroman for script-specific accuracy, otherwise uroman auto-detects.
    """
    if not text or not text.strip():
        return text
    try:
        lcode = _LCODE.get((lang or "").lower())
        engine = _engine()
        if lcode:
            return engine.romanize_string(text, lcode=lcode)
        return engine.romanize_string(text)
    except Exception as exc:  # noqa: BLE001 — never break a turn on romanization
        logger.warning(f"romanize failed (lang={lang}): {exc}")
        return text
