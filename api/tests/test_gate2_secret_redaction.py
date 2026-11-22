"""Gate 2 Correction A — secret-safe error handling regression tests.

Verifies that credential-bearing exception text never reaches logs or
persisted recording state, and that operational behavior (error categories,
the EGRESS_COMPLETE/failed_precondition retry branch) is preserved.

Deterministic and offline; the LiveKit API and environment config are mocked,
so no storage or provider service is contacted.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.livekit import post_call


def _secret_exception(text: str) -> RuntimeError:
    return RuntimeError(text)


class _Ctx:
    def __init__(self, lkapi):
        self._lkapi = lkapi

    async def __aenter__(self):
        return self._lkapi

    async def __aexit__(self, *args):
        return False


def _fake_livekit_api(lkapi) -> MagicMock:
    api = MagicMock()
    api.LiveKitAPI = lambda *a, **kw: _Ctx(lkapi)
    return api


def _env_config() -> SimpleNamespace:
    return SimpleNamespace(
        endpoint_url="https://s3.internal",
        bucket="rec",
        access_key_id="AKID",
        secret_access_key="SECRETKEY",
        region="us-east-1",
        force_path_style=True,
        public_base_url="https://s3.internal",
        key_prefix="SPX-VOICE-INBOUND",
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        configured=True,
        livekit_url="https://lk.example",
        livekit_api_key="k",
        livekit_api_secret="s",
    )


class _CaptureLogger:
    def __init__(self):
        self.messages = []

    def error(self, msg):
        self.messages.append(msg)

    def warning(self, msg):
        self.messages.append(msg)

    def info(self, msg):
        self.messages.append(msg)


def test_safe_error_text_is_category_only():
    samples = (
        RuntimeError("request failed Authorization: Bearer sk-live-1234567890abcdef"),
        ValueError('{"api_key": "sk-live-abc", "url": "https://user:pass@host/x"}'),
        RuntimeError("dsn postgresql://admin:secret123@db.internal:5432/app"),
        ValueError("X-Api-Key: 999 signed_url=https://s3.example/r?sig=ABCDEF"),
        OSError("bad token=abc123"),
    )
    for exc in samples:
        text = post_call._safe_error_text(exc)
        assert "<redacted>" in text
        assert type(exc).__name__ in text
        for secret in (
            "Bearer", "sk-live", "secret123", "admin:",
            "sig=ABCDEF", "token=abc123", "api_key",
        ):
            assert secret not in text


@pytest.mark.asyncio
async def test_start_recording_failure_redacts_log_and_persisted_state():
    logger = _CaptureLogger()
    lkapi = AsyncMock()
    lkapi.egress.start_room_composite_egress = AsyncMock(
        side_effect=_secret_exception(
            'connect failed Authorization: Bearer sk-live-1234567890abcdef '
            '{"api_key":"secret123"}'
        )
    )
    fake = _fake_livekit_api(lkapi)
    with (
        patch.object(post_call, "logger", logger),
        patch.object(post_call, "_recording_config_from_env", return_value=_env_config()),
        patch.object(post_call, "effective_livekit_settings", return_value=_settings()),
        patch.dict(sys.modules, {"livekit": MagicMock(api=fake), "livekit.api": fake}),
    ):
        state = await post_call.start_livekit_room_recording(
            room_name="room-1", workflow_run_id=42
        )
    assert state.status == "not_started"
    assert state.error is not None and "<redacted>" in state.error
    assert state.error.startswith("RuntimeError")
    assert any("failed to start room recording" in m for m in logger.messages)
    for message in logger.messages:
        for secret in ("Bearer", "sk-live", "secret123", "Authorization"):
            assert secret not in message


@pytest.mark.asyncio
async def test_stop_recording_egress_complete_branch_redacts_everything():
    state = post_call.LiveKitRecordingState(
        egress_id="EG1",
        recording_key="SPX-VOICE-INBOUND/42-r.mp3",
        recording_url="https://s3.internal/SPX-VOICE-INBOUND/42-r.mp3",
        status="started",
    )
    logger = _CaptureLogger()
    lkapi = AsyncMock()
    lkapi.egress.stop_egress = AsyncMock(
        side_effect=_secret_exception(
            "EGRESS_COMPLETE failed_precondition "
            "Authorization: Bearer sk-live-1234567890abcdef"
        )
    )
    list_resp = MagicMock()
    list_resp.items = []
    # The "failed to inspect completed" log site only fires when the follow-up
    # list call itself raises; make it raise a credential-bearing exception.
    lkapi.egress.list_egress = AsyncMock(
        side_effect=_secret_exception("list failed token=abc123")
    )
    fake = _fake_livekit_api(lkapi)
    with (
        patch.object(post_call, "logger", logger),
        patch.object(post_call, "effective_livekit_settings", return_value=_settings()),
        patch.dict(sys.modules, {"livekit": MagicMock(api=fake), "livekit.api": fake}),
    ):
        result = await post_call.stop_livekit_room_recording(state)
    assert result.status == "stop_failed"
    assert result.error is not None and "<redacted>" in result.error
    assert any("failed to inspect completed" in m for m in logger.messages)
    for message in logger.messages:
        for secret in ("Bearer", "sk-live", "EGRESS_COMPLETE", "failed_precondition", "token=abc123"):
            assert secret not in message


@pytest.mark.asyncio
async def test_stop_recording_plain_failure_redacts_without_list_branch():
    state = post_call.LiveKitRecordingState(
        egress_id="EG2", recording_key="k", recording_url="u", status="started",
    )
    logger = _CaptureLogger()
    lkapi = AsyncMock()
    lkapi.egress.stop_egress = AsyncMock(
        side_effect=_secret_exception(
            "signed_url=https://s3/x?sig=ABCDEF&X-Amz-Credential=AKIA123"
        )
    )
    fake = _fake_livekit_api(lkapi)
    with (
        patch.object(post_call, "logger", logger),
        patch.object(post_call, "effective_livekit_settings", return_value=_settings()),
        patch.dict(sys.modules, {"livekit": MagicMock(api=fake), "livekit.api": fake}),
    ):
        result = await post_call.stop_livekit_room_recording(state)
    assert result.status == "stop_failed"
    assert result.error is not None and "<redacted>" in result.error
    assert not any("failed to inspect completed" in m for m in logger.messages)
    for message in logger.messages:
        for secret in ("sig=ABCDEF", "AKIA123", "signed_url"):
            assert secret not in message
