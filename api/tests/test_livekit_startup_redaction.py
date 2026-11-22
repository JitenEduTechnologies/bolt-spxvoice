"""LiveKit startup-failure secret redaction — focused regression tests.

Confirmed defect: ``worker._record_livekit_startup_failure`` persisted
``str(exc)`` verbatim into the API log, ``workflow_run.logs.livekit_events``,
and ``annotations.livekit_startup_error``. Provider/LiveKit client exceptions
may embed API keys, bearer tokens, authorization headers, or
credential-bearing URLs in their string representation (this is a property
of the underlying HTTP/SDK clients, not something this codebase controls).

A second, equivalent leak was found during the audit for this fix at
``LiveKitWorkflowAgent._speak_opening``'s ``opening_audio_cache_failed``
handler, which used the same unsafe ``_safe_error_text`` helper.

Both now go through a category-only redaction path (exception class name +
a stable internal error code; for startup failures, also the operational
stage). This file injects synthetic secret markers of every category named
in the fix request and asserts none of them survive into any log, persisted
event, or annotation. All provider/session objects are fakes; no network or
provider call is made.
"""
from __future__ import annotations

import inspect
import io
import logging
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.enums import WorkflowRunState
from api.services.livekit import worker

# --- Synthetic secret markers (all clearly fake; never real credentials) ---

GEMINI_KEY = "AIzaSyFAKE1234567890abcdefghijklmnopqr"
LIVEKIT_SECRET = "lksecret_FAKE_abcdef1234567890"
BEARER_TOKEN = "sk-live-FAKE1234567890abcdef"
AUTH_HEADER_VALUE = f"Bearer {BEARER_TOKEN}"
CRED_URL = f"https://user:{LIVEKIT_SECRET}@db.internal:5432/app"
GEMINI_QUERY_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    f"?key={GEMINI_KEY}"
)
JSON_BODY = f'{{"api_key": "{GEMINI_KEY}", "error": {{"message": "invalid request"}}}}'

ALL_MARKERS = (
    GEMINI_KEY, LIVEKIT_SECRET, BEARER_TOKEN, AUTH_HEADER_VALUE,
    CRED_URL, GEMINI_QUERY_URL, JSON_BODY,
)

ADVERSARIAL_EXCEPTIONS = [
    pytest.param(RuntimeError(f"auth failed, key={GEMINI_KEY}"), id="gemini_key"),
    pytest.param(RuntimeError(f"livekit handshake failed secret={LIVEKIT_SECRET}"), id="livekit_secret"),
    pytest.param(RuntimeError(f"request rejected Authorization: {AUTH_HEADER_VALUE}"), id="bearer_token"),
    pytest.param(ConnectionError(f"GET {GEMINI_QUERY_URL} -> 401"), id="credential_url_query_string"),
    pytest.param(ValueError(f"bad response body {JSON_BODY}"), id="json_body"),
    pytest.param(OSError(f"db connect failed dsn={CRED_URL}"), id="credential_bearing_dsn_url"),
    pytest.param(
        RuntimeError(
            "multiline provider exception:\n"
            f"  Authorization: {AUTH_HEADER_VALUE}\n"
            f"  url={GEMINI_QUERY_URL}\n"
            f"  body={JSON_BODY}"
        ),
        id="multiline_combined",
    ),
]


class _CaptureLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, msg, *a, **kw):
        self.messages.append(str(msg))

    def warning(self, msg, *a, **kw):
        self.messages.append(str(msg))

    def info(self, msg, *a, **kw):
        self.messages.append(str(msg))

    def exception(self, *a, **kw):
        raise AssertionError(
            "logger.exception() must never be used on this path: loguru "
            "attaches the raw exception and full traceback to the sink "
            "regardless of the message text passed in, which would leak "
            "secrets through a different door than the one being fixed."
        )


def _flatten(value) -> str:
    """Stringify a persisted payload (dict/list/str) for substring scanning."""
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _assert_no_markers(*texts: str) -> None:
    haystack = " ".join(texts)
    for marker in ALL_MARKERS:
        assert marker not in haystack, f"leaked marker {marker!r} in: {haystack!r}"


# ---------------------------------------------------------------------------
# _record_livekit_startup_failure (the confirmed defect)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ADVERSARIAL_EXCEPTIONS)
async def test_startup_failure_redacts_adversarial_secrets(exc, monkeypatch):
    log = _CaptureLogger()
    append_event = AsyncMock()
    update_run = AsyncMock()
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(worker, "_append_livekit_run_event", append_event)
    monkeypatch.setattr(worker.db_client, "update_workflow_run", update_run)

    await worker._record_livekit_startup_failure(
        99, exc, stage="session_start", provider="google_realtime", model="gemini-3.8-live"
    )

    # Nothing reached any log line.
    _assert_no_markers(*log.messages)

    # Nothing reached the persisted workflow-run event.
    event = append_event.await_args.args[1]
    _assert_no_markers(_flatten(event))
    assert event["type"] == "startup_failed"
    assert event["error_class"] == type(exc).__name__
    assert event["stage"] == "session_start"
    assert event["error_code"]  # always a non-empty stable code

    # Nothing reached the persisted annotation.
    _, kwargs = update_run.await_args
    _assert_no_markers(_flatten(kwargs["annotations"]))

    # Preserved behavior: disposition, state, correlation id, completion flag.
    assert update_run.await_args.args[0] == 99
    assert kwargs["is_completed"] is True
    assert kwargs["state"] == WorkflowRunState.COMPLETED.value
    assert kwargs["call_disposition"] == "FAILED"


@pytest.mark.asyncio
async def test_startup_failure_never_calls_logger_exception(monkeypatch):
    """Regression guard for the second leak path: logger.exception() embeds
    the raw exception + traceback regardless of the message string, so this
    function must only ever use logger.error()/.warning()/.info()."""
    log = _CaptureLogger()  # .exception() raises AssertionError if called
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(worker, "_append_livekit_run_event", AsyncMock())
    monkeypatch.setattr(worker.db_client, "update_workflow_run", AsyncMock())

    await worker._record_livekit_startup_failure(
        1, RuntimeError(f"key={GEMINI_KEY}"), stage="config_resolution"
    )
    assert log.messages  # logger.error was in fact called


@pytest.mark.asyncio
async def test_startup_failure_persist_error_path_also_redacts(monkeypatch):
    """If persisting the failure itself raises, the fallback warning must
    also stay category-only (it already used _safe_finalize_error; guard
    against regression)."""
    log = _CaptureLogger()
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(worker, "_append_livekit_run_event", AsyncMock())
    persist_exc = RuntimeError(f"db write failed dsn={CRED_URL}")
    monkeypatch.setattr(
        worker.db_client, "update_workflow_run", AsyncMock(side_effect=persist_exc)
    )

    await worker._record_livekit_startup_failure(1, RuntimeError("boom"))
    _assert_no_markers(*log.messages)


def test_startup_error_code_mapping_is_stable_and_specific():
    assert worker._startup_error_code(worker.InvalidCallDurationError("x")) == "INVALID_CALL_DURATION"
    assert worker._startup_error_code(ValueError("x")) == "INVALID_CONFIGURATION"
    assert worker._startup_error_code(TimeoutError("x")) == "TIMEOUT"
    assert worker._startup_error_code(ConnectionError("x")) == "CONNECTION_FAILED"
    assert worker._startup_error_code(OSError("x")) == "CONNECTION_FAILED"
    assert worker._startup_error_code(PermissionError("x")) == "AUTH_FAILED"
    # Unknown/provider exception types fall through to the generic code —
    # never contribute their own message or a type-specific guess.
    assert worker._startup_error_code(KeyError("x")) == "STARTUP_FAILED"
    assert worker._startup_error_code(RuntimeError("x")) == "STARTUP_FAILED"


def test_startup_failure_cannot_reach_browser_feedback_by_construction():
    """_record_livekit_startup_failure takes no session/room/ctx argument,
    so it has no mechanism to call _publish_feedback or publish_data at all
    — it can only log and persist to the DB. This pins that shape so a
    future change can't silently add a browser-feedback call carrying the
    raw exception."""
    params = set(inspect.signature(worker._record_livekit_startup_failure).parameters)
    assert params == {"workflow_run_id", "exc", "stage", "provider", "model", "user_config"}


# ---------------------------------------------------------------------------
# LiveKitWorkflowAgent._speak_opening's opening_audio_cache_failed handler
# (second instance of the same defect, found during the call-site audit)
# ---------------------------------------------------------------------------


def _opening_cache_fake_agent(workflow_run_id: int = 7) -> SimpleNamespace:
    agent = SimpleNamespace(
        _uses_realtime=True,
        _realtime_exact_speech_uses_tts=True,
        _tts_api_key="offline-placeholder",
        _opening_model="gemini-3.8-live",
        _tts_voice="Kore",
        _tts_language="en",
        _workflow_run_id=workflow_run_id,
    )
    agent._speak_opening = MethodType(worker.LiveKitWorkflowAgent._speak_opening, agent)
    # Reached only because the mocked audio-cache call always fails in this
    # test, pushing _speak_opening to its no-cached-audio fallback branch.
    agent._speak_text = AsyncMock(return_value=None)
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ADVERSARIAL_EXCEPTIONS)
async def test_opening_audio_cache_failure_redacts_adversarial_secrets(exc, monkeypatch):
    log = _CaptureLogger()
    append_event = AsyncMock()
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(worker, "_append_livekit_run_event", append_event)
    monkeypatch.setattr(
        worker, "_live_opening_audio_path", AsyncMock(side_effect=exc)
    )

    agent = _opening_cache_fake_agent()
    await agent._speak_opening("Hello there.")

    _assert_no_markers(*log.messages)
    event = append_event.await_args.args[1]
    _assert_no_markers(_flatten(event))
    assert event["type"] == "opening_audio_cache_failed"
    assert event["fallback"] == "gemini_tts"


# ---------------------------------------------------------------------------
# P1 fix: the LiveKit Agents SDK job runner logs any exception that escapes
# entrypoint() via logger.error(..., exc_info=task.exception()), which
# renders the exception's own str()/traceback regardless of anything this
# module already sanitized. entrypoint() must never let the original
# exception escape -- it must raise LiveKitStartupError (category-only, via
# `from None`) instead, at every exit path, including failures before a
# workflow run exists (e.g. ctx.connect() itself failing, which
# _record_livekit_startup_failure cannot even be called for -- there is no
# workflow_run_id yet).
# ---------------------------------------------------------------------------


def _minimal_entrypoint_ctx(*, connect_exc: Exception | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        job=SimpleNamespace(id="job-1"),
        room=SimpleNamespace(name="room-1"),
        connect=AsyncMock(side_effect=connect_exc) if connect_exc else AsyncMock(),
        wait_for_participant=AsyncMock(return_value=SimpleNamespace()),
        delete_room=AsyncMock(),
        shutdown=Mock(),
        add_shutdown_callback=Mock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ADVERSARIAL_EXCEPTIONS)
async def test_pre_run_resolution_failure_escapes_as_sanitized_error(exc, monkeypatch):
    """New outer safety net: a failure before any workflow_run_id exists
    (ctx.connect() here) must still escape entrypoint() as a sanitized
    LiveKitStartupError, never as the original exception -- even though
    _record_livekit_startup_failure cannot be called here at all."""
    log = _CaptureLogger()
    monkeypatch.setattr(worker, "logger", log)
    monkeypatch.setattr(worker, "_metadata_from_job", lambda c: {"call_type": "web"})
    ctx = _minimal_entrypoint_ctx(connect_exc=exc)

    with pytest.raises(worker.LiveKitStartupError) as excinfo:
        await worker.entrypoint(ctx)

    escaped = excinfo.value
    _assert_no_markers(str(escaped))
    _assert_no_markers(*log.messages)
    assert escaped.__cause__ is None
    assert escaped.__suppress_context__ is True
    assert "pre_run_resolution" in str(escaped)


def _config_resolution_harness(monkeypatch, exc: Exception) -> tuple[SimpleNamespace, AsyncMock, AsyncMock]:
    """Minimal standalone harness reaching entrypoint()'s first try/except
    (the config_resolution stage), failing inside it via a mocked
    resolve_effective_config. Built independently of
    test_product_mode_mapping.py's _patch_worker_entrypoint so this file has
    no cross-file test dependency."""
    ctx = _minimal_entrypoint_ctx()
    definition = SimpleNamespace(
        workflow_configurations={"product_mode": "standard", "max_call_duration_seconds": 60},
        workflow_json={},
    )
    append_event = AsyncMock()
    update_run = AsyncMock()
    db = SimpleNamespace(
        update_workflow_run=update_run,
        get_workflow_run=AsyncMock(return_value=SimpleNamespace(definition=definition)),
        get_workflow=AsyncMock(return_value=SimpleNamespace(id=1)),
        get_user_configurations=AsyncMock(return_value=SimpleNamespace()),
        has_active_recordings=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(worker, "db_client", db)
    monkeypatch.setattr(worker, "_append_livekit_run_event", append_event)
    monkeypatch.setattr(worker, "_metadata_from_job", lambda c: {"call_type": "web"})
    monkeypatch.setattr(worker, "_metadata_from_room", lambda c: {})
    monkeypatch.setattr(worker, "_participant_context", lambda p: {})
    monkeypatch.setattr(
        worker, "_resolve_or_create_workflow_run", AsyncMock(return_value=(1, 2, 3, 4, {}))
    )
    monkeypatch.setattr(worker, "resolve_effective_config", Mock(side_effect=exc))
    return ctx, append_event, update_run


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ADVERSARIAL_EXCEPTIONS)
async def test_config_resolution_failure_escapes_as_sanitized_error(exc, monkeypatch):
    """The already-fixed config_resolution raise point, exercised through
    the real entrypoint() end-to-end (not a hand-built exception): the
    escaped exception, the real logger.error call, and the real
    _record_livekit_startup_failure persistence must all be marker-free."""
    log = _CaptureLogger()
    monkeypatch.setattr(worker, "logger", log)
    ctx, append_event, update_run = _config_resolution_harness(monkeypatch, exc)

    with pytest.raises(worker.LiveKitStartupError) as excinfo:
        await worker.entrypoint(ctx)

    escaped = excinfo.value
    _assert_no_markers(str(escaped))
    _assert_no_markers(*log.messages)
    assert escaped.__cause__ is None
    assert escaped.__suppress_context__ is True
    assert "config_resolution" in str(escaped)

    # The real _record_livekit_startup_failure ran too (not mocked out) --
    # its persisted event/annotation must independently be marker-free.
    event = append_event.await_args.args[1]
    _assert_no_markers(_flatten(event))
    _, kwargs = update_run.await_args
    _assert_no_markers(_flatten(kwargs["annotations"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ADVERSARIAL_EXCEPTIONS)
async def test_escaped_exception_survives_sdk_exc_info_rendering_without_markers(
    exc, monkeypatch
):
    """Direct proof the P1 fix closes the actual gap: simulates exactly what
    livekit-agents' own job runner does with an uncaught exception from
    entrypoint() --
    ``logger.error("unhandled exception while running the job task",
    exc_info=task.exception())`` -- using Python's real logging module
    (not a fake), against the real exception entrypoint() actually raises.
    This is the call site this module cannot patch or control; the only
    available fix is ensuring the exception object itself carries nothing
    renderable."""
    monkeypatch.setattr(worker, "logger", _CaptureLogger())
    monkeypatch.setattr(worker, "_metadata_from_job", lambda c: {"call_type": "web"})
    ctx = _minimal_entrypoint_ctx(connect_exc=exc)

    with pytest.raises(worker.LiveKitStartupError) as excinfo:
        await worker.entrypoint(ctx)
    escaped = excinfo.value

    stream = io.StringIO()
    sdk_logger = logging.getLogger(f"simulated_livekit_sdk_runner_{id(exc)}")
    sdk_logger.handlers.clear()
    sdk_logger.propagate = False
    sdk_logger.addHandler(logging.StreamHandler(stream))
    sdk_logger.setLevel(logging.ERROR)

    sdk_logger.error("unhandled exception while running the job task", exc_info=escaped)
    rendered = stream.getvalue()

    _assert_no_markers(rendered)
    # Sanity: this exercised real traceback formatting, not a no-op --
    # the sanitized type name and "During handling..." chain-suppression
    # both end up absent/present exactly as expected.
    assert "LiveKitStartupError" in rendered
    assert "During handling of the above exception" not in rendered


def test_startup_sanitized_message_consistent_across_all_three_stages():
    """_startup_sanitized_message is the single mechanism used at all three
    entrypoint() exit points (pre_run_resolution, config_resolution,
    session_start). Confirming it is marker-free for every adversarial
    exception at every stage name gives confidence in the session_start
    call site too, without needing a third full end-to-end harness for a
    code path that uses the exact same shared helper."""
    for exc in (p.values[0] for p in ADVERSARIAL_EXCEPTIONS):
        for stage in ("pre_run_resolution", "config_resolution", "session_start"):
            message = worker._startup_sanitized_message(exc, stage=stage)
            _assert_no_markers(message)
            assert stage in message
            assert type(exc).__name__ in message


def test_livekit_startup_error_suppresses_cause_and_context():
    try:
        try:
            raise RuntimeError(f"key={GEMINI_KEY}")
        except RuntimeError as original:
            raise worker.LiveKitStartupError("RuntimeError: STARTUP_FAILED (stage=test)") from None
    except worker.LiveKitStartupError as escaped:
        assert escaped.__cause__ is None
        assert escaped.__suppress_context__ is True
        # __context__ is still technically set by Python's own exception
        # chaining machinery regardless of `from None` -- __suppress_context__
        # is what makes standard traceback rendering skip it (proven above
        # in the exc_info test). This assertion documents that fact rather
        # than claiming the attribute itself is cleared.
        assert escaped.__context__ is not None


@pytest.mark.asyncio
async def test_startup_failure_never_invokes_publish_feedback_behaviorally(monkeypatch):
    """Behavioral complement to the signature-based test above: patches the
    real _publish_feedback method on the agent class and asserts it is
    never invoked during a startup failure, rather than relying only on
    inspecting _record_livekit_startup_failure's parameter list."""
    publish_feedback = AsyncMock()
    monkeypatch.setattr(worker.LiveKitWorkflowAgent, "_publish_feedback", publish_feedback)
    monkeypatch.setattr(worker, "logger", _CaptureLogger())
    monkeypatch.setattr(worker, "_metadata_from_job", lambda c: {"call_type": "web"})
    ctx = _minimal_entrypoint_ctx(connect_exc=RuntimeError(f"key={GEMINI_KEY}"))

    with pytest.raises(worker.LiveKitStartupError):
        await worker.entrypoint(ctx)

    publish_feedback.assert_not_awaited()
    publish_feedback.assert_not_called()
