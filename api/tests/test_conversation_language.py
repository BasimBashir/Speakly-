"""Tests for the per-agent conversation-language profiles + overlay."""

from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.services.configuration import conversation_language as cl
from api.services.configuration.ai_model_configuration import (
    _apply_conversation_language,
)


def test_supported_languages_includes_english():
    codes = {l["code"] for l in cl.supported_languages()}
    assert "english" in codes


def test_get_profile_fallback_to_english():
    assert cl.get_profile(None).code == "english"
    assert cl.get_profile("klingon").code == "english"
    assert cl.get_profile("ENGLISH").code == "english"  # case-insensitive


def test_removed_urdu_hindi_fall_back_to_english():
    # Workflows saved while Urdu/Hindi existed must still run, as English.
    assert cl.get_profile("urdu").code == "english"
    assert cl.get_profile("hindi").code == "english"


def test_english_profile_is_noop():
    p = cl.get_profile("english")
    assert p.reply_instruction is None
    assert p.stt_override is None and p.tts_override is None


def test_overlay_is_noop_for_english_and_removed_languages():
    base = EffectiveAIModelConfiguration()  # empty org config
    assert _apply_conversation_language(base, "english") is base
    assert _apply_conversation_language(base, None) is base
    assert _apply_conversation_language(base, "urdu") is base
