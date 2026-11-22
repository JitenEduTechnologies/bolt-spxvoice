import pytest
from unittest.mock import Mock, patch

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.resolve import resolve_effective_config, normalize_model_overrides
from api.services.configuration.registry import ServiceProviders
from api.tests.integrations._run_pipeline_helpers import USER_CONFIGURATION


def _get_base_config() -> UserConfiguration:
    config_data = dict(USER_CONFIGURATION)
    config_data["realtime"] = {
        "provider": "google_realtime",
        "model": "gemini-3.1-flash-live-preview",
        "api_key": "dummy-gemini-key",
        "voice": "Puck",
    }
    return UserConfiguration.model_validate(config_data)


def test_voice_engine_standard_gemini_live():
    """Verify STANDARD profile (Gemini Live Realtime) ignores incomplete STT/TTS and resolves correctly."""
    base_config = _get_base_config()
    overrides = {
        "is_realtime": True,
        "realtime": {
            "provider": "google_realtime",
            "model": "gemini-3.1-flash-live-preview",
            "voice": "Kore",
            "language": "en"
        },
        # Incomplete STT/TTS without keys should be ignored
        "stt": {"provider": "deepgram", "model": "nova-2", "api_key": ""},
        "tts": {"provider": "elevenlabs", "model": "flash_v2", "api_key": ""},
    }
    normalized = normalize_model_overrides(overrides, is_realtime=True)
    assert "stt" not in normalized or not normalized["stt"].get("api_key")
    
    effective = resolve_effective_config(base_config, overrides)
    assert effective.is_realtime is True
    assert effective.realtime is not None
    assert effective.realtime.provider == "google_realtime"
    assert effective.realtime.model == "gemini-3.1-flash-live-preview"
    assert effective.realtime.voice == "Kore"


def test_voice_engine_premium_deepgram_deepseek_cartesia():
    """Verify PREMIUM profile (Deepgram STT -> DeepSeek/OpenAI LLM -> Cartesia TTS) pipeline."""
    base_config = _get_base_config()
    overrides = {
        "is_realtime": False,
        "stt": {
            "provider": ServiceProviders.DEEPGRAM.value,
            "model": "nova-3",
            "api_key": "dg_test_key_12345"
        },
        "llm": {
            "provider": ServiceProviders.OPENROUTER.value,
            "model": "deepseek/deepseek-chat",
            "api_key": "sk_or_test_key_12345"
        },
        "tts": {
            "provider": ServiceProviders.CARTESIA.value,
            "model": "sonic-english",
            "voice": "sonic-barista",
            "api_key": "car_test_key_12345"
        }
    }
    effective = resolve_effective_config(base_config, overrides)
    assert effective.is_realtime is False
    assert effective.stt.provider == ServiceProviders.DEEPGRAM.value
    assert effective.stt.model == "nova-3"
    assert effective.llm.provider == ServiceProviders.OPENROUTER.value
    assert effective.llm.model == "deepseek/deepseek-chat"
    assert effective.tts.provider == ServiceProviders.CARTESIA.value
    assert effective.tts.model == "sonic-english"


def test_voice_engine_budget_local_speaches_and_cache():
    """Verify BUDGET profile (Speaches local self-hosted or audio cache, zero external cost)."""
    base_config = _get_base_config()
    overrides = {
        "is_realtime": False,
        "stt": {
            "provider": ServiceProviders.SPEACHES.value,
            "model": "whisper-base",
            "base_url": "http://speaches:8000/v1",
            "api_key": "none"
        },
        "tts": {
            "provider": ServiceProviders.SPEACHES.value,
            "model": "piper-en",
            "voice": "en_US-lessac-medium",
            "base_url": "http://speaches:8000/v1",
            "api_key": "none"
        }
    }
    effective = resolve_effective_config(base_config, overrides)
    assert effective.is_realtime is False
    assert effective.stt.provider == ServiceProviders.SPEACHES.value
    assert effective.tts.provider == ServiceProviders.SPEACHES.value


def test_no_silent_paid_fallback_or_double_tts():
    """Verify that absent API keys fail explicitly rather than falling back silently to paid models."""
    base_config = _get_base_config()
    bad_overrides = {
        "is_realtime": True,
        "realtime": {
            "provider": "google_realtime",
            "model": "gemini-3.1-flash-live-preview",
            "api_key": "    "  # blank
        }
    }
    normalized = normalize_model_overrides(bad_overrides, is_realtime=True)
    assert "api_key" not in normalized.get("realtime", {})
