"""Tests for native-script -> Roman display romanization (uroman)."""

from api.services.transliteration.romanize import to_roman


def test_hindi_romanization():
    # "क्या हाल है" -> "kya haal hai"-ish
    out = to_roman("क्या हाल है", "hi").lower()
    assert "haal" in out or "hal" in out


def test_urdu_romanization():
    out = to_roman("کیا حال ہے", "ur").lower()
    assert out.strip() and any(c.isalpha() for c in out)


def test_empty_and_whitespace_passthrough():
    assert to_roman("") == ""
    assert to_roman("   ") == "   "


def test_unknown_lang_does_not_crash():
    # No lang code -> uroman auto-detects; must not raise.
    assert to_roman("नमस्ते").strip()
