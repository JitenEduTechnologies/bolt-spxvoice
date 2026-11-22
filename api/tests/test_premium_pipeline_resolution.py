"""Premium (Deepgram STT -> DeepSeek LLM -> Cartesia TTS) resolution tests.

No provider calls: only synthetic dummy keys are used and nothing here opens a
network connection. These tests prove the Premium configuration RESOLVES to the
expected adapters and that failure paths are explicit and key-safe. They do not
prove the Premium pipeline works live (transcription, LLM reply, TTS audio,
barge-in, latency, cleanup) -- that needs disposable keys and an authorized
Test Audio session.
"""

import inspect
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.registry import (
    CartesiaTTSConfiguration,
    DeepgramSTTConfiguration,
    DeepSeekLLMConfiguration,
    ServiceProviders,
)
from api.services.livekit import worker
from api.services.livekit.runtime_config import (
    LIVEKIT_SUPPORTED_PROVIDERS,
    livekit_supports_provider,
)
from api.services.pipecat import service_factory
from api.services.pipecat.audio_config import create_audio_config

SYNTH_DEEPGRAM = "SYNTHETIC_DEEPGRAM_KEY_7c1d0a52"
SYNTH_DEEPSEEK = "SYNTHETIC_DEEPSEEK_KEY_91be44f0"
SYNTH_CARTESIA = "SYNTHETIC_CARTESIA_KEY_3a8f6e17"
ALL_SYNTH = (SYNTH_DEEPGRAM, SYNTH_DEEPSEEK, SYNTH_CARTESIA)

PROVIDER_ENV_NAMES = (
    "DEEPGRAM_API_KEY",
    "DEEPSEEK_API_KEY",
    "CARTESIA_API_KEY",
)


def _premium_config() -> UserConfiguration:
    return UserConfiguration(
        is_realtime=False,
        stt=DeepgramSTTConfiguration(
            provider=ServiceProviders.DEEPGRAM,
            api_key=SYNTH_DEEPGRAM,
            model="nova-3-general",
        ),
        llm=DeepSeekLLMConfiguration(
            provider=ServiceProviders.DEEPSEEK,
            api_key=SYNTH_DEEPSEEK,
            model="deepseek-chat",
        ),
        tts=CartesiaTTSConfiguration(
            provider=ServiceProviders.CARTESIA,
            api_key=SYNTH_CARTESIA,
            model="sonic-3",
        ),
    )


@pytest.fixture(autouse=True)
def _no_ambient_provider_keys(monkeypatch):
    for name in PROVIDER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _assert_no_key_leak(text: str) -> None:
    for secret in ALL_SYNTH:
        assert secret not in text


# ---------------------------------------------------------------------------
# Configuration resolves to Deepgram -> DeepSeek -> Cartesia
# ---------------------------------------------------------------------------


def test_premium_config_resolves_expected_providers():
    cfg = _premium_config()
    assert cfg.is_realtime is False
    assert cfg.stt.provider == ServiceProviders.DEEPGRAM
    assert cfg.llm.provider == ServiceProviders.DEEPSEEK
    assert cfg.tts.provider == ServiceProviders.CARTESIA
    assert cfg.llm.base_url == "https://api.deepseek.com"


def test_premium_config_round_trips_through_model_dump():
    cfg = _premium_config()
    again = UserConfiguration.model_validate(cfg.model_dump())
    assert again.stt.provider == ServiceProviders.DEEPGRAM
    assert again.llm.provider == ServiceProviders.DEEPSEEK
    assert again.tts.provider == ServiceProviders.CARTESIA


# ---------------------------------------------------------------------------
# LiveKit runtime path
# ---------------------------------------------------------------------------


def test_livekit_supported_provider_sets_include_premium():
    assert "deepgram" in LIVEKIT_SUPPORTED_PROVIDERS["stt"]
    assert "deepseek" in LIVEKIT_SUPPORTED_PROVIDERS["llm"]
    assert "cartesia" in LIVEKIT_SUPPORTED_PROVIDERS["tts"]
    assert livekit_supports_provider("stt", "deepgram")
    assert livekit_supports_provider("llm", "deepseek")
    assert livekit_supports_provider("tts", "cartesia")


def test_livekit_create_session_builds_premium_adapters(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(worker, "AgentSession", FakeSession)

    worker._create_session(_premium_config(), vad="vad")

    stt, llm, tts = captured["stt"], captured["llm"], captured["tts"]
    assert type(stt).__module__.startswith("livekit.plugins.deepgram")
    assert type(tts).__module__.startswith("livekit.plugins.cartesia")
    # DeepSeek is served through the OpenAI-compatible LLM plugin.
    assert type(llm).__module__.startswith("livekit.plugins.openai")
    assert "deepseek" in str(llm.model).lower()


def test_livekit_deepseek_uses_configured_base_url(monkeypatch):
    captured = {}

    class FakeLLM:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeSession:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(worker.openai, "LLM", FakeLLM)
    monkeypatch.setattr(worker, "AgentSession", FakeSession)

    worker._create_session(_premium_config(), vad="vad")

    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["model"] == "deepseek-chat"
    assert captured["api_key"] == SYNTH_DEEPSEEK


@pytest.mark.parametrize(
    "missing, expected_fragment",
    [
        ("llm", "DeepSeek LLM requires api_key"),
        ("stt", "Deepgram STT requires api_key"),
        ("tts", "Cartesia TTS requires api_key"),
    ],
)
def test_livekit_missing_key_raises_explicit_safe_error(
    monkeypatch, missing, expected_fragment
):
    class FakeSession:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(worker, "AgentSession", FakeSession)

    cfg = SimpleNamespace(
        is_realtime=False,
        realtime=None,
        llm=SimpleNamespace(
            provider="deepseek",
            model="deepseek-chat",
            api_key=SYNTH_DEEPSEEK if missing != "llm" else None,
            base_url="https://api.deepseek.com",
        ),
        stt=SimpleNamespace(
            provider="deepgram",
            model="nova-3-general",
            language="en-US",
            api_key=SYNTH_DEEPGRAM if missing != "stt" else None,
        ),
        tts=SimpleNamespace(
            provider="cartesia",
            model="sonic-3",
            voice="v",
            speed=None,
            volume=None,
            api_key=SYNTH_CARTESIA if missing != "tts" else None,
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        worker._create_session(cfg, vad="vad")

    message = str(exc_info.value)
    assert expected_fragment in message
    _assert_no_key_leak(message)


def test_livekit_unsupported_provider_is_explicit(monkeypatch):
    class FakeSession:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(worker, "AgentSession", FakeSession)
    cfg = _premium_config()
    cfg.llm = SimpleNamespace(provider="no_such_llm", model="m", api_key="x")
    with pytest.raises(ValueError, match="LLM provider is unsupported"):
        worker._create_session(cfg, vad="vad")


def test_livekit_premium_env_fallback_keys(monkeypatch):
    class FakeSession:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(worker, "AgentSession", FakeSession)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SYNTH_DEEPSEEK)
    monkeypatch.setenv("DEEPGRAM_API_KEY", SYNTH_DEEPGRAM)
    monkeypatch.setenv("CARTESIA_API_KEY", SYNTH_CARTESIA)
    cfg = SimpleNamespace(
        is_realtime=False,
        realtime=None,
        llm=SimpleNamespace(
            provider="deepseek", model="deepseek-chat", api_key=None, base_url=None
        ),
        stt=SimpleNamespace(
            provider="deepgram", model="nova-3-general", language="en-US", api_key=None
        ),
        tts=SimpleNamespace(
            provider="cartesia",
            model="sonic-3",
            voice="v",
            speed=None,
            volume=None,
            api_key=None,
        ),
    )
    worker._create_session(cfg, vad="vad")


# ---------------------------------------------------------------------------
# Pipecat runtime path
# ---------------------------------------------------------------------------


def test_pipecat_deepgram_stt_adapter_constructs_without_network():
    audio = create_audio_config("smallwebrtc")
    stt = service_factory.create_stt_service(_premium_config(), audio)
    assert type(stt).__name__ == "DeepgramSTTService"
    assert type(stt).__module__.startswith("pipecat.services.deepgram")


def test_pipecat_cartesia_tts_adapter_constructs_without_network():
    audio = create_audio_config("smallwebrtc")
    tts = service_factory.create_tts_service(_premium_config(), audio)
    assert type(tts).__name__ == "CartesiaTTSService"
    assert type(tts).__module__.startswith("pipecat.services.cartesia")


def test_pipecat_deepseek_llm_adapter_resolves():
    from pipecat.services.deepseek.llm import DeepSeekLLMService

    llm = service_factory.create_llm_service(_premium_config())
    assert isinstance(llm, DeepSeekLLMService)
    # Same defaults/endpoint as the LiveKit worker (see test above).
    assert llm._settings.model == "deepseek-chat"
    assert str(llm._client.base_url).rstrip("/") == "https://api.deepseek.com"
    assert llm._client.api_key == SYNTH_DEEPSEEK
    assert llm._settings.temperature == 0.1


def test_pipecat_deepseek_honours_configured_base_url_and_model(monkeypatch):
    import socket

    cfg = _premium_config()
    cfg.llm.base_url = "https://proxy.example.test"
    cfg.llm.model = "deepseek-reasoner"
    # A custom endpoint carrying the stored key is refused unless the operator
    # approved it (P1-B key-exfiltration guard)...
    with pytest.raises(HTTPException) as exc_info:
        service_factory.create_llm_service(cfg)
    assert exc_info.value.status_code == 400
    _assert_no_key_leak(str(exc_info.value.detail))
    # ...and honoured once approved (public address; DNS mocked, no network).
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://proxy.example.test")
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: public)
    llm = service_factory.create_llm_service(cfg)
    assert str(llm._client.base_url).rstrip("/") == "https://proxy.example.test"
    assert llm._settings.model == "deepseek-reasoner"


def test_pipecat_deepseek_makes_no_network_call_at_construction(monkeypatch):
    import socket

    def _blocked(*a, **k):
        raise AssertionError("network access attempted while constructing DeepSeek LLM")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    assert service_factory.create_llm_service(_premium_config()) is not None


@pytest.mark.parametrize("missing", [None, "", "   ", [], [""]])
def test_pipecat_deepseek_missing_key_is_explicit_and_key_safe(missing):
    with pytest.raises(HTTPException) as exc_info:
        service_factory.create_llm_service_from_provider(
            "deepseek", "deepseek-chat", missing
        )
    assert exc_info.value.status_code == 400
    detail = str(exc_info.value.detail).lower()
    assert "deepseek" in detail and "api key" in detail
    _assert_no_key_leak(str(exc_info.value.detail))


def test_text_chat_runner_resolves_deepseek_without_http_400():
    from api.services.workflow import text_chat_runner

    # The runner binds the same factory function the tests exercise.
    assert text_chat_runner.create_llm_service is service_factory.create_llm_service
    llm = text_chat_runner.create_llm_service(_premium_config())
    assert type(llm).__name__ == "DeepSeekLLMService"


def test_premium_chain_resolves_through_pipecat_factories():
    """Deepgram STT -> DeepSeek LLM -> Cartesia TTS via the functions the
    Pipecat runtime/Text Chat call, with synthetic keys and no network."""
    cfg = _premium_config()
    audio = create_audio_config("smallwebrtc")
    stt = service_factory.create_stt_service(cfg, audio)
    llm = service_factory.create_llm_service(cfg)
    tts = service_factory.create_tts_service(cfg, audio)
    assert [type(s).__name__ for s in (stt, llm, tts)] == [
        "DeepgramSTTService",
        "DeepSeekLLMService",
        "CartesiaTTSService",
    ]

def test_pipecat_stt_unknown_provider_error_is_key_safe():
    audio = create_audio_config("smallwebrtc")
    cfg = SimpleNamespace(
        stt=SimpleNamespace(provider="no_such_stt", model="m", api_key=SYNTH_DEEPGRAM)
    )
    with pytest.raises(HTTPException) as exc_info:
        service_factory.create_stt_service(cfg, audio)
    assert exc_info.value.status_code == 400
    _assert_no_key_leak(str(exc_info.value.detail))


def test_pipecat_tts_unknown_provider_error_is_key_safe():
    audio = create_audio_config("smallwebrtc")
    cfg = SimpleNamespace(
        tts=SimpleNamespace(provider="no_such_tts", model="m", api_key=SYNTH_CARTESIA)
    )
    with pytest.raises(HTTPException) as exc_info:
        service_factory.create_tts_service(cfg, audio)
    assert exc_info.value.status_code == 400
    _assert_no_key_leak(str(exc_info.value.detail))


def test_pipecat_llm_unknown_provider_error_is_key_safe():
    with pytest.raises(HTTPException) as exc_info:
        service_factory.create_llm_service_from_provider(
            "no_such_llm", "m", SYNTH_DEEPSEEK
        )
    assert exc_info.value.status_code == 400
    _assert_no_key_leak(str(exc_info.value.detail))


# ---------------------------------------------------------------------------
# Import/start requires no provider key
# ---------------------------------------------------------------------------


def test_importing_runtime_modules_needs_no_provider_key():
    for name in PROVIDER_ENV_NAMES:
        assert name not in os.environ
    for module in (
        "api.services.livekit.worker",
        "api.services.pipecat.service_factory",
        "api.services.workflow.text_chat_runner",
        "api.routes.main",
    ):
        __import__(module)
        assert module in sys.modules


def test_user_configuration_without_provider_keys_validates():
    cfg = UserConfiguration()
    assert cfg.stt is None and cfg.llm is None and cfg.tts is None


# ---------------------------------------------------------------------------
# Cancellation / interruption / cleanup hooks are registered (real hooks)
# ---------------------------------------------------------------------------


def test_text_chat_runner_handles_end_and_cancel_frames():
    from api.services.workflow import text_chat_runner

    src = inspect.getsource(text_chat_runner._TextChatCaptureProcessor)
    assert "BotStoppedSpeakingFrame" in src
    assert "isinstance(frame, EndFrame)" in src
    assert "isinstance(frame, CancelFrame)" in src
    assert callable(text_chat_runner.execute_text_chat_pending_turn)


def test_livekit_agent_exposes_interruption_controls():
    src = inspect.getsource(worker.LiveKitWorkflowAgent)
    assert "interruption_config" in src
    assert "min_duration_seconds" in src
    assert "min_words_for_barge_in" in src
    assert "backchannel_filter_enabled" in src
    assert "allow_interruptions" in inspect.getsource(
        worker.LiveKitWorkflowAgent._speak_text
    )


def test_livekit_entrypoint_registers_cleanup_and_failure_recording():
    assert "_run_entrypoint" in inspect.getsource(worker.entrypoint)
    entry_src = inspect.getsource(worker.entrypoint)
    assert "LiveKitStartupError" in entry_src
    run_src = inspect.getsource(worker._run_entrypoint)
    assert "_record_livekit_startup_failure" in run_src
    assert "add_shutdown_callback" in run_src
    assert "interruption_config" in run_src
    assert callable(worker._set_finalization_state)
    assert callable(worker._cleanup_rejected_startup)
    assert callable(worker._register_caller_disconnect_handler)


def test_livekit_session_close_handler_registered():
    session_src = inspect.getsource(worker)
    assert '@session.on("close")' in session_src


def test_pipecat_event_handlers_register_disconnect_cleanup():
    from api.services.pipecat import event_handlers

    src = inspect.getsource(event_handlers)
    assert "on_client_disconnected" in src or "on_pipeline_finished" in src
