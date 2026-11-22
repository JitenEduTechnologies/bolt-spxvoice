import pytest
from api.services.livekit.language_packs import LANGUAGE_PACKS, get_language_pack


def test_supported_languages_present():
    expected_codes = {"en", "hi", "te", "hi-en", "te-en"}
    assert set(LANGUAGE_PACKS.keys()) == expected_codes


def test_language_pack_resolution():
    assert get_language_pack("en").code == "en"
    assert get_language_pack("en-US").code == "en"
    assert get_language_pack("en-IN").code == "en"
    assert get_language_pack("hi").code == "hi"
    assert get_language_pack("hi-IN").code == "hi"
    assert get_language_pack("te").code == "te"
    assert get_language_pack("te-IN").code == "te"
    assert get_language_pack("hi-en").code == "hi-en"
    assert get_language_pack("te-en").code == "te-en"
    # Fallback to English on unknown
    assert get_language_pack("unknown-lang").code == "en"
    assert get_language_pack(None).code == "en"


def test_language_pack_fields_complete():
    for code, pack in LANGUAGE_PACKS.items():
        assert pack.code == code
        assert pack.display_name.strip()
        assert pack.locale.strip()
        assert pack.default_voice in {"Puck", "Charon", "Kore", "Fenrir", "Aoede"}
        assert len(pack.greeting.strip()) > 10
        assert len(pack.silence_stage1.strip()) > 5
        assert len(pack.silence_stage2.strip()) > 5
        assert len(pack.silence_stage3.strip()) > 5
        assert len(pack.backchannel_keywords) >= 5
        assert len(pack.system_prompt_language_rule.strip()) > 20


def test_telugu_and_hindi_specific_content():
    te = get_language_pack("te")
    assert "Namaskaram" in te.greeting
    assert "avunu" in te.backchannel_keywords
    assert "sare" in te.backchannel_keywords

    hi = get_language_pack("hi")
    assert "Namaste" in hi.greeting
    assert "theek" in hi.backchannel_keywords
    assert "ji haan" in hi.backchannel_keywords


def test_code_mixed_hinglish_and_tenglish_instructions():
    hi_en = get_language_pack("hi-en")
    assert "Hinglish" in hi_en.system_prompt_language_rule

    te_en = get_language_pack("te-en")
    assert "Tenglish" in te_en.system_prompt_language_rule

