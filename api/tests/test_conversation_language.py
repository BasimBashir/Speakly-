"""Tests for the per-agent conversation-language profiles + overlay."""

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import conversation_language as cl
from api.services.configuration.ai_model_configuration import (
    _apply_conversation_language,
)


def test_supported_languages_includes_core():
    codes = {l["code"] for l in cl.supported_languages()}
    assert {"english", "urdu", "hindi"} <= codes


def test_get_profile_fallback_to_english():
    assert cl.get_profile(None).code == "english"
    assert cl.get_profile("klingon").code == "english"
    assert cl.get_profile("URDU").code == "urdu"  # case-insensitive


def test_english_profile_is_noop():
    p = cl.get_profile("english")
    assert p.reply_instruction is None
    assert p.stt_override is None and p.tts_override is None


def test_urdu_profile_has_overrides_and_instruction():
    p = cl.get_profile("urdu")
    assert "Urdu" in p.reply_instruction
    assert p.stt_override["provider"] == "indic_conformer"
    assert p.stt_override["language"] == "ur"
    assert p.tts_override["provider"] == "piper"


def test_overlay_applies_urdu_and_is_noop_for_english():
    base = EffectiveAIModelConfiguration()  # empty org config
    urdu = _apply_conversation_language(base, "urdu")
    assert urdu.stt is not None and urdu.stt.provider.value == "indic_conformer"
    assert urdu.tts is not None and urdu.tts.provider.value == "piper"

    # English / unknown leave the config untouched (same object semantics)
    assert _apply_conversation_language(base, "english") is base
    assert _apply_conversation_language(base, None) is base
