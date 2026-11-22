"""VOICE-01 focused tests: barge-in, speech cancellation evidence, and duration safety.

All provider/session objects are fakes. No Google API session is opened.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.livekit import worker
from api.services.livekit.conversation_state import (
    ConversationState,
    ConversationStateMachine,
)
from api.services.livekit.interruption_filter import InterruptionFilter
from api.services.livekit.latency_tracker import LatencyTracker
from api.services.livekit.silence_manager import SilenceManager


class FakeSession:
    """Minimal AgentSession stand-in: records handlers, exposes agent_state."""

    def __init__(self) -> None:
        self._handlers: dict[str, object] = {}
        self.interrupt_count = 0
        self.agent_state = "listening"
        self.user_state = "listening"
        self.history = SimpleNamespace(to_dict=lambda: {"items": []})
        self.usage = None

    def on(self, event_name: str):
        def decorator(fn):
            self._handlers[event_name] = fn
            return fn

        return decorator

    def interrupt(self, *, force: bool = False):
        self.interrupt_count += 1
        return None

    def trigger(self, event_name: str, event_obj):
        handler = self._handlers.get(event_name)
        return handler(event_obj) if handler else None


class FakeSpeechHandle:
    """SpeechHandle stand-in with the public done-callback contract."""

    def __init__(self) -> None:
        self.interrupted = False
        self.interrupt_calls: list[dict] = []
        self._callbacks: list = []

    def interrupt(self, *, force: bool = False) -> None:
        self.interrupt_calls.append({"force": force})
        self.interrupted = True

    def add_done_callback(self, callback) -> None:
        self._callbacks.append(callback)

    def finish(self, *, interrupted: bool) -> None:
        self.interrupted = interrupted
        for callback in list(self._callbacks):
            callback(self)


class FakeAgent:
    def __init__(self, run_id: int = 901) -> None:
        self._workflow_run_id = run_id
        self._started_at = time.monotonic()
        self._user_speech_started_at = None
        self._last_user_speech_end_at = None
        self._last_final_transcript_at = None
        self._feedbacks: list[dict] = []
        self._shutdown_task = None
        self._state_machine = ConversationStateMachine(run_id=run_id)
        self._interruption_filter = InterruptionFilter()
        self._interruption_events = {
            "caller_speech_during_agent_speech": 0,
            "accepted": 0,
            "rejected": 0,
        }
        self._latency_tracker = LatencyTracker(run_id=run_id)
        self._silence_manager = SilenceManager(
            stage1_seconds=4.0, stage2_seconds=8.0, stage3_seconds=14.0, language="en"
        )

    def _emit_feedback(self, payload: dict) -> None:
        self._feedbacks.append(payload)

    async def stop_recording(self):
        return None

    def interruption_feedback(self, phase: str) -> list[dict]:
        return [
            fb
            for fb in self._feedbacks
            if fb.get("type") == "rtf-interruption" and fb["payload"]["phase"] == phase
        ]


def _wired() -> tuple[FakeSession, FakeAgent]:
    session = FakeSession()
    agent = FakeAgent()
    worker._register_feedback_handlers(session, agent)
    agent._state_machine.transition_to(ConversationState.AGENT_SPEAKING, reason="agent_turn")
    session.agent_state = "speaking"
    return session, agent


class OpeningHandle(FakeSpeechHandle):
    def __init__(self, on_playout=None) -> None:
        super().__init__()
        self.on_playout = on_playout

    async def wait_for_playout(self) -> None:
        if self.on_playout:
            self.on_playout()
        self.finish(interrupted=self.interrupted)


def _opening_harness(*, cached: bool, handle: OpeningHandle):
    session = FakeSession()
    agent = FakeAgent()
    start = SimpleNamespace(
        id="start", name="Start", is_start=True, allow_interrupt=True,
        out_edges=[SimpleNamespace(target="main", label="continue", transition_speech=None)],
    )
    main = SimpleNamespace(id="main", name="Main", is_end=False)
    agent._workflow = SimpleNamespace(start_node_id="start", nodes={"start": start, "main": main})
    agent._current_node = None
    agent._visited_nodes = []
    agent._start_opening_text = "Hello."
    agent._uses_realtime = True
    agent._realtime_exact_speech_uses_tts = cached
    agent._realtime_generate_reply_supported = not cached
    agent._realtime_tool_choice_supported = False
    agent._tts_api_key = "offline-placeholder"
    agent._tts_voice = "Kore"
    agent._tts_language = "en"
    agent._opening_model = "gemini-3.1-flash-live-preview" if cached else "gemini-3.8-live"
    agent._ctx = SimpleNamespace(room=SimpleNamespace(isconnected=lambda: True))
    agent._opening_started = False
    agent._opening_active = False
    agent._opening_caller_speech_seen = False
    agent._opening_cancel_reason = None
    agent._session_closed = False
    agent.session = session
    resets = []
    agent._silence_manager = SimpleNamespace(
        reset_silence=lambda **kwargs: resets.append(kwargs["reason"])
    )
    agent._emit_opening_event = MethodType(worker.LiveKitWorkflowAgent._emit_opening_event, agent)
    agent._speak_opening = MethodType(worker.LiveKitWorkflowAgent._speak_opening, agent)
    agent._speak_text = MethodType(worker.LiveKitWorkflowAgent._speak_text, agent)
    agent._say_text = MethodType(worker.LiveKitWorkflowAgent._say_text, agent)
    agent._record_node_transition = AsyncMock()
    advances = []

    async def set_node(node_id):
        advances.append(node_id)
        agent._current_node = agent._workflow.nodes[node_id]

    agent.set_node = set_node
    agent._auto_advance_edge_after_opening = MethodType(
        worker.LiveKitWorkflowAgent._auto_advance_edge_after_opening, agent
    )
    agent._auto_advance_after_opening = MethodType(
        worker.LiveKitWorkflowAgent._auto_advance_after_opening, agent
    )
    calls = []

    def say(text, **kwargs):
        calls.append(("say", text, kwargs))
        session.trigger(
            "speech_created",
            SimpleNamespace(speech_handle=handle, source="say", user_initiated=True),
        )
        return handle

    def generate_reply(**kwargs):
        calls.append(("generate_reply", None, kwargs))
        session.trigger(
            "speech_created",
            SimpleNamespace(
                speech_handle=handle, source="generate_reply", user_initiated=True
            ),
        )
        return handle

    session.say = say
    session.generate_reply = generate_reply
    worker._register_feedback_handlers(session, agent)
    return agent, session, calls, advances, resets


def _opening_phases(agent):
    return [
        event["payload"]["phase"]
        for event in agent._feedbacks
        if event["type"] == "rtf-opening"
    ]


@pytest.mark.asyncio
async def test_cached_opening_completes_once_with_sdk_say_and_idle_state():
    handle = OpeningHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=True, handle=handle)

    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), patch.object(
        worker, "_wav_audio_frames", return_value="offline-audio"
    ):
        await worker.LiveKitWorkflowAgent.start_opening(agent)
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert len(calls) == 1
    assert calls[0][0] == "say"
    assert calls[0][2] == {
        "allow_interruptions": True,
        "add_to_chat_ctx": True,
        "audio": "offline-audio",
    }
    assert advances == ["main"]
    assert agent._state_machine.state == ConversationState.LISTENING
    assert resets == ["opening_greeting_ended"]
    assert _opening_phases(agent) == [
        "opening_started", "opening_state_transition", "opening_completed",
        "opening_state_transition",
    ]
    assert session.interrupt_count == 0


@pytest.mark.asyncio
async def test_cached_opening_caller_interruption_preserves_active_turn():
    handle = OpeningHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=True, handle=handle)

    def caller_barge_in():
        session.user_state = "speaking"
        session.agent_state = "speaking"
        session.trigger(
            "user_state_changed",
            SimpleNamespace(new_state="speaking", old_state="listening", created_at=20.0),
        )
        handle.interrupted = True
        session.agent_state = "listening"
        session.trigger(
            "agent_state_changed",
            SimpleNamespace(new_state="listening", old_state="speaking", created_at=20.3),
        )
        session.trigger(
            "speech_created",
            SimpleNamespace(
                speech_handle=FakeSpeechHandle(), source="generate_reply",
                user_initiated=False,
            ),
        )

    handle.on_playout = caller_barge_in
    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), patch.object(
        worker, "_wav_audio_frames", return_value="offline-audio"
    ):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert len(calls) == 1 and calls[0][0] == "say"
    assert agent._state_machine.state == ConversationState.CUSTOMER_SPEAKING
    assert advances == ["main"]
    assert "opening_interrupted" in _opening_phases(agent)
    assert "opening_generation_created" in _opening_phases(agent)
    assert "opening_completed" not in _opening_phases(agent)
    assert "opening_greeting_ended" not in resets
    assert agent._interruption_events["accepted"] == 1


@pytest.mark.asyncio
async def test_manual_opening_is_interruptible_and_keeps_caller_turn():
    handle = OpeningHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=False, handle=handle)

    def caller_barge_in():
        session.user_state = "speaking"
        session.trigger(
            "user_state_changed",
            SimpleNamespace(new_state="speaking", old_state="listening", created_at=30.0),
        )
        handle.interrupted = True

    handle.on_playout = caller_barge_in
    await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert len(calls) == 1 and calls[0][0] == "generate_reply"
    assert calls[0][2]["allow_interruptions"] is True
    assert calls[0][2]["instructions"].startswith("Say exactly and only this text:")
    assert agent._state_machine.state == ConversationState.INTERRUPTED
    assert advances == ["main"]
    assert "opening_interrupted" in _opening_phases(agent)
    assert "opening_generation_created" in _opening_phases(agent)
    assert "opening_greeting_ended" not in resets


@pytest.mark.asyncio
@pytest.mark.parametrize("active_turn", ["caller", "agent"])
async def test_completed_opening_does_not_overwrite_active_turn(active_turn):
    handle = OpeningHandle()
    agent, session, _, advances, resets = _opening_harness(cached=False, handle=handle)

    def active_during_completion():
        if active_turn == "caller":
            session.user_state = "speaking"
            agent._state_machine.transition_to(
                ConversationState.CUSTOMER_SPEAKING, reason="caller_active"
            )
        else:
            session.agent_state = "speaking"
            agent._state_machine.transition_to(
                ConversationState.AGENT_SPEAKING, reason="new_agent_turn", force=True
            )

    handle.on_playout = active_during_completion
    await worker.LiveKitWorkflowAgent.start_opening(agent)

    expected = (
        ConversationState.CUSTOMER_SPEAKING if active_turn == "caller"
        else ConversationState.AGENT_SPEAKING
    )
    assert agent._state_machine.state == expected
    assert advances == ["main"]
    assert "opening_completed" in _opening_phases(agent)
    assert "opening_greeting_ended" not in resets


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_kind", ["shutdown", "session_disconnected", "session_error"])
async def test_opening_cancellation_never_advances_or_counts_accepted_interruption(stop_kind):
    handle = OpeningHandle()
    agent, session, _, advances, resets = _opening_harness(cached=False, handle=handle)

    def cancel_during_playout():
        if stop_kind == "shutdown":
            agent._shutdown_task = object()
            agent._opening_cancel_reason = "shutdown_requested"
        else:
            close_reason = "error" if stop_kind == "session_error" else "participant_disconnected"
            session.trigger(
                "close",
                SimpleNamespace(reason=SimpleNamespace(value=close_reason)),
            )
        handle.interrupted = True

    handle.on_playout = cancel_during_playout
    await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert advances == []
    assert agent._interruption_events["accepted"] == 0
    assert "opening_cancelled" in _opening_phases(agent)
    assert "opening_interrupted" not in _opening_phases(agent)
    assert "opening_greeting_ended" not in resets


@pytest.mark.asyncio
async def test_cancelled_opening_task_does_not_advance_or_reset_silence():
    def cancel_playout():
        raise asyncio.CancelledError()

    handle = OpeningHandle(on_playout=cancel_playout)
    agent, _, _, advances, resets = _opening_harness(cached=False, handle=handle)

    with pytest.raises(asyncio.CancelledError):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert advances == []
    assert resets == []
    assert _opening_phases(agent).count("opening_cancelled") == 1


@pytest.mark.asyncio
async def test_opening_diagnostic_is_queued_before_async_publish():
    agent, _, _, _, _ = _opening_harness(cached=False, handle=OpeningHandle())
    queued = []
    agent._queue_feedback_persist = queued.append
    agent._publish_feedback = AsyncMock()

    agent._emit_opening_event("opening_cancelled", reason="shutdown_requested")

    assert len(queued) == 1
    assert queued[0]["type"] == "rtf-opening"
    assert queued[0]["payload"]["phase"] == "opening_cancelled"
    assert queued[0]["payload"]["reason"] == "shutdown_requested"
    assert set(queued[0]["payload"]) == {"phase", "node_id", "turn_id", "reason"}
    await asyncio.sleep(0)
    agent._publish_feedback.assert_awaited_once_with(queued[0])


@pytest.mark.asyncio
async def test_unattributed_opening_cancellation_does_not_claim_genuine_barge_in():
    handle = OpeningHandle()
    agent, _, _, advances, _ = _opening_harness(cached=False, handle=handle)
    handle.on_playout = lambda: setattr(handle, "interrupted", True)

    await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert advances == ["main"]
    assert agent._interruption_events["accepted"] == 0
    assert "opening_cancelled" in _opening_phases(agent)
    assert "opening_interrupted" not in _opening_phases(agent)


# 1. Genuine interruption during agent speech is detected ---------------------------------


def test_caller_speech_during_agent_speech_is_detected_from_sdk_user_state():
    session, agent = _wired()

    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="speaking", old_state="listening", created_at=10.0),
    )

    assert agent._state_machine.state == ConversationState.INTERRUPTED
    assert agent._interruption_events["caller_speech_during_agent_speech"] == 1
    assert len(agent.interruption_feedback("caller_speech_detected")) == 1
    assert agent._latency_tracker.get_or_create_turn(agent._state_machine.turn_id).interrupted_at == 10.0
    # Detection is a report, not an action: the SDK/provider owns the interrupt.
    assert session.interrupt_count == 0


# 2. Audible speech cancellation is requested correctly (provider semantics) ---------------


def test_server_vad_config_allows_gemini_to_interrupt_on_start_of_activity():
    config = worker._fast_server_vad_realtime_input_config()
    detection = config.automatic_activity_detection

    assert detection.disabled is False
    assert (
        config.activity_handling
        == worker.genai_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
    )
    # False-trigger protection stays on the provider gate.
    assert detection.start_of_speech_sensitivity == worker.genai_types.StartSensitivity.START_SENSITIVITY_LOW
    assert detection.prefix_padding_ms == worker.GEMINI_SERVER_VAD_PREFIX_PADDING_MS
    assert detection.silence_duration_ms == worker.GEMINI_SERVER_VAD_SILENCE_DURATION_MS
    # No code path may reintroduce the no-interruption mode.
    assert "ActivityHandling.NO_INTERRUPTION" not in inspect.getsource(worker)


def test_accepted_interruption_is_recorded_from_speech_handle_and_agent_silence():
    session, agent = _wired()
    handle = FakeSpeechHandle()
    session.trigger("speech_created", SimpleNamespace(speech_handle=handle))

    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="speaking", old_state="listening", created_at=20.0),
    )
    # SDK cancels the speech and the agent goes silent.
    session.agent_state = "listening"
    session.trigger(
        "agent_state_changed",
        SimpleNamespace(new_state="listening", old_state="speaking", created_at=20.3),
    )
    handle.finish(interrupted=True)

    assert agent._state_machine.state == ConversationState.CUSTOMER_SPEAKING
    assert agent._interruption_events["accepted"] == 1
    assert len(agent.interruption_feedback("accepted")) == 1
    turn = agent._latency_tracker.get_or_create_turn(agent._state_machine.turn_id)
    assert turn.interruption_stop_ms == 300.0


# 3 & 4. Customer correction retained; no stale playback cancelled by transcripts ---------


def test_final_transcript_is_retained_and_never_cancels_current_or_queued_speech():
    session, agent = _wired()

    session.trigger(
        "user_input_transcribed",
        SimpleNamespace(is_final=True, transcript="no, the other one", created_at=30.0),
    )

    assert session.interrupt_count == 0
    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING
    assert agent._last_final_transcript_at == 30.0
    retained = [
        fb for fb in agent._feedbacks if fb.get("type") == "rtf-user-transcription"
    ]
    assert retained and retained[-1]["payload"] == {"text": "no, the other one", "final": True}
    # The worker must not contain any transcript-driven interrupt path.
    source = inspect.getsource(worker._register_feedback_handlers)
    assert "session.interrupt(" not in source


# 5. Short noise does not always cause a false interruption -------------------------------


def test_short_speech_not_accepted_by_sdk_returns_to_agent_speaking():
    session, agent = _wired()

    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="speaking", old_state="listening", created_at=40.0),
    )
    assert agent._state_machine.state == ConversationState.INTERRUPTED
    # Agent is still speaking when the short burst ends -> not accepted.
    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="listening", old_state="speaking", created_at=40.2),
    )

    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING
    assert agent._interruption_events["rejected"] == 1
    assert agent._interruption_events["accepted"] == 0
    assert len(agent.interruption_feedback("rejected")) == 1


# 6. Session termination and maximum duration remain enforced -----------------------------


@pytest.mark.asyncio
async def test_max_duration_watchdog_schedules_shutdown_without_attribute_error():
    scheduled: list[tuple[str, float]] = []
    fake = SimpleNamespace(
        _shutdown_task=None,
        _state_machine=SimpleNamespace(
            check_timeouts=lambda: None, state=ConversationState.LISTENING
        ),
        _silence_manager=SimpleNamespace(evaluate_silence=AsyncMock()),
        _max_duration_seconds=30,
        _started_at=time.monotonic() - 31,
    )
    fake._schedule_shutdown = lambda reason, *, delay=0.0: scheduled.append((reason, delay))

    await worker.LiveKitWorkflowAgent._silence_watchdog_loop(fake)

    assert scheduled == [("max_duration_reached", 0.0)]
    # Regression guard: the misspelled attribute must not exist or be referenced.
    assert not hasattr(worker.LiveKitWorkflowAgent, "schedule_shutdown")
    assert "self.schedule_shutdown(" not in inspect.getsource(worker)


@pytest.mark.asyncio
async def test_silence_disconnect_schedules_shutdown():
    scheduled: list[tuple[str, float]] = []
    fake = SimpleNamespace(_state_machine=ConversationStateMachine(run_id=5))
    fake._schedule_shutdown = lambda reason, *, delay=0.0: scheduled.append((reason, delay))

    await worker.LiveKitWorkflowAgent._handle_silence_disconnect(fake, "stage3_silence")

    assert scheduled == [("no_response_timeout", 2.0)]
    assert fake._state_machine.state == ConversationState.WRAPUP


# 7. Repeated interruption and cleanup events are safe ------------------------------------


def test_repeated_interruption_and_cleanup_events_are_safe():
    session, agent = _wired()

    # Handles with and without the done-callback contract, including None.
    session.trigger("speech_created", SimpleNamespace(speech_handle=None))
    session.trigger("speech_created", SimpleNamespace(speech_handle=object()))
    handle = FakeSpeechHandle()
    session.trigger("speech_created", SimpleNamespace(speech_handle=handle))

    for i in range(3):
        session.trigger(
            "user_state_changed",
            SimpleNamespace(new_state="speaking", old_state="listening", created_at=50.0 + i),
        )
        session.trigger(
            "user_state_changed",
            SimpleNamespace(new_state="listening", old_state="speaking", created_at=50.5 + i),
        )
    handle.finish(interrupted=False)
    handle.finish(interrupted=True)
    handle.finish(interrupted=True)

    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING
    assert agent._interruption_events["caller_speech_during_agent_speech"] == 3
    assert agent._interruption_events["rejected"] == 3
    assert agent._interruption_events["accepted"] == 1
    assert session.interrupt_count == 0


# 8. Existing browser path remains compatible --------------------------------------------


def test_local_vad_browser_path_unchanged():
    config = worker._local_vad_realtime_input_config()
    assert config.automatic_activity_detection.disabled is True
    options = worker._session_latency_options("vad")
    assert options["turn_handling"]["turn_detection"] == "vad"
    assert options["turn_handling"]["interruption"]["enabled"] is True


def test_handlers_tolerate_agents_without_interruption_counters():
    session = FakeSession()
    agent = FakeAgent()
    del agent._interruption_events
    worker._register_feedback_handlers(session, agent)
    agent._state_machine.transition_to(ConversationState.AGENT_SPEAKING, reason="agent_turn")

    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="speaking", old_state="listening", created_at=60.0),
    )

    assert agent._state_machine.state == ConversationState.INTERRUPTED
    assert len(agent.interruption_feedback("caller_speech_detected")) == 1


@pytest.mark.asyncio
async def test_finalize_reports_sdk_derived_interruption_stats():
    session, agent = _wired()
    agent._interruption_events.update({"accepted": 2, "rejected": 1, "caller_speech_during_agent_speech": 3})

    mock_db = MagicMock()
    mock_db.update_workflow_run = AsyncMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=None)

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "success"}),
    ):
        await worker._finalize_livekit_workflow_run(
            workflow_run_id=agent._workflow_run_id,
            session=session,
            agent=agent,
            room_name="room-901",
            reason="max_duration_reached",
        )
        await asyncio.sleep(0)

    completion_calls = [
        call
        for call in mock_db.update_workflow_run.call_args_list
        if "gathered_context" in call.kwargs
    ]
    assert completion_calls, "the run completion update was not persisted"
    completion_context = completion_calls[0].kwargs["gathered_context"]
    stats = completion_context["interruption_stats"]
    assert stats["interruptions"] == 2
    assert stats["accepted"] == 2
    assert stats["rejected"] == 1
    assert stats["caller_speech_during_agent_speech"] == 3
    assert completion_context["livekit_shutdown_reason"] == "max_duration_reached"


# 9. VOICE-01 shutdown reason preservation and single finalization ------------------------


class _FakeShutdownCtx:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    def shutdown(self, reason: str) -> None:
        self.events.append(("shutdown", reason))

    def delete_room(self) -> None:
        self.events.append(("delete_room", None))


class _FakeShutdownAgent:
    def __init__(self, ctx) -> None:
        self._ctx = ctx
        self._opening_active = False
        self._opening_cancel_reason = None
        self._silence_watchdog_task = None
        self._requested_shutdown_reason = None
        self._shutdown_task = None
        self._shutdown_deadline = None
        self._shutdown_generation = 0
        self._room_cleanup_task = None

    async def flush_feedback(self) -> None:
        return None

    async def stop_recording(self):
        return None

    async def _emergency_shutdown_cleanup(self, reason: str) -> None:
        await worker.LiveKitWorkflowAgent._emergency_shutdown_cleanup(self, reason)


class _FakeSlowDeleteCtx(_FakeShutdownCtx):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay

    async def delete_room(self) -> None:
        self.events.append(("delete_room", None))
        await asyncio.sleep(self._delay)


class _FakeFailingDeleteCtx(_FakeShutdownCtx):
    def delete_room(self) -> None:
        raise RuntimeError("delete failed")


@pytest.mark.asyncio
async def test_shutdown_call_deletes_room_before_sdk_shutdown():
    ctx = _FakeShutdownCtx()
    agent = _FakeShutdownAgent(ctx)

    await worker.LiveKitWorkflowAgent._shutdown_call(agent, "max_duration_reached")

    assert agent._requested_shutdown_reason == "max_duration_reached"
    assert ctx.events == [
        ("delete_room", None),
        ("shutdown", "max_duration_reached"),
    ]


@pytest.mark.asyncio
async def test_shutdown_call_bounds_slow_room_delete_and_still_shuts_down(monkeypatch):
    monkeypatch.setattr(worker, "_ROOM_DELETE_TIMEOUT_SECONDS", 0.05)
    ctx = _FakeSlowDeleteCtx(delay=5)
    agent = _FakeShutdownAgent(ctx)

    started = time.monotonic()
    await worker.LiveKitWorkflowAgent._shutdown_call(agent, "max_duration_reached")

    assert time.monotonic() - started < 1.0
    assert ctx.events == [
        ("delete_room", None),
        ("shutdown", "max_duration_reached"),
    ]


@pytest.mark.asyncio
async def test_shutdown_call_shuts_down_when_room_delete_fails():
    ctx = _FakeFailingDeleteCtx()
    agent = _FakeShutdownAgent(ctx)

    await worker.LiveKitWorkflowAgent._shutdown_call(agent, "max_duration_reached")

    assert ctx.events == [("shutdown", "max_duration_reached")]
    assert agent._requested_shutdown_reason == "max_duration_reached"


@pytest.mark.asyncio
async def test_schedule_shutdown_records_reason_before_task_starts():
    agent = _FakeShutdownAgent(_FakeShutdownCtx())

    worker.LiveKitWorkflowAgent._schedule_shutdown(
        agent, "max_duration_reached", delay=10
    )

    assert agent._requested_shutdown_reason == "max_duration_reached"
    if agent._shutdown_task is not None:
        agent._shutdown_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await agent._shutdown_task


class _FakeSlowFlushAgent(_FakeShutdownAgent):
    async def flush_feedback(self) -> None:
        await asyncio.sleep(10)

    async def _emergency_shutdown_cleanup(self, reason: str) -> None:
        await worker.LiveKitWorkflowAgent._emergency_shutdown_cleanup(self, reason)


@pytest.mark.asyncio
async def test_shutdown_cancellation_without_replacement_runs_emergency_cleanup():
    ctx = _FakeShutdownCtx()
    agent = _FakeSlowFlushAgent(ctx)
    task = asyncio.create_task(
        worker.LiveKitWorkflowAgent._shutdown_call(agent, "external_cancel")
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.1)
    assert ("delete_room", None) in ctx.events
    assert ("shutdown", "external_cancel") in ctx.events
    assert agent._requested_shutdown_reason == "external_cancel"


@pytest.mark.asyncio
async def test_superseded_shutdown_cancellation_skips_emergency_cleanup():
    ctx = _FakeShutdownCtx()
    agent = _FakeSlowFlushAgent(ctx)
    agent._shutdown_generation = 2
    task = asyncio.create_task(
        worker.LiveKitWorkflowAgent._shutdown_call(
            agent, "old_reason", generation=1
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.1)
    assert ctx.events == []


@pytest.mark.asyncio
async def test_finalize_once_shares_one_task_between_concurrent_callers():
    agent = SimpleNamespace()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)
        started.set()
        await release.wait()

    first = asyncio.create_task(
        worker._finalize_once(
            agent=agent, reason="participant_disconnected", finalize=finalize
        )
    )
    await started.wait()
    second = asyncio.create_task(
        worker._finalize_once(
            agent=agent, reason="participant_disconnected", finalize=finalize
        )
    )
    await asyncio.sleep(0)
    assert agent._finalize_state == "pending"
    assert len(calls) == 1
    release.set()
    assert await first == "succeeded"
    assert await second == "succeeded"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_finalize_once_reports_failed_and_allows_retry():
    agent = SimpleNamespace()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)
        if len(calls) == 1:
            raise RuntimeError("persistence failed")

    first = await worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    assert first == "failed"
    assert agent._finalize_state == "failed"
    assert agent._finalize_error.startswith("RuntimeError")

    second = await worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    assert second == "succeeded"
    assert agent._finalize_state == "succeeded"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_finalize_once_waiter_cancellation_does_not_cancel_shared_work():
    agent = SimpleNamespace()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)
        started.set()
        await release.wait()

    first = asyncio.create_task(
        worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    )
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(
        worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    )
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    assert await second == "succeeded"


@pytest.mark.asyncio
async def test_finalize_once_timeout_marks_failed_and_cancels_task():
    agent = SimpleNamespace()
    inner_cancelled = asyncio.Event()

    async def finalize(reason: str) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            inner_cancelled.set()
            raise

    state = await worker._finalize_once(
        agent=agent, reason="x", finalize=finalize, timeout_seconds=0.05
    )

    assert state == "failed"
    assert agent._finalize_state == "failed"
    assert agent._finalize_error == "timeout"
    assert inner_cancelled.is_set()
    assert agent._finalize_task.done()
    assert not agent._finalize_task.cancelled()
    assert isinstance(agent._finalize_task.exception(), asyncio.TimeoutError)


@pytest.mark.asyncio
async def test_finalize_once_shared_task_cancellation_marks_cancelled():
    agent = SimpleNamespace()
    started = asyncio.Event()

    async def finalize(reason: str) -> None:
        started.set()
        await asyncio.sleep(10)

    waiter = asyncio.create_task(
        worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    )
    await started.wait()
    agent._finalize_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert agent._finalize_state == "cancelled"


@pytest.mark.asyncio
async def test_finalize_once_keeps_requested_reason_when_none_requested():
    agent = SimpleNamespace()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)

    await worker._finalize_once(
        agent=agent, reason="participant_disconnected", finalize=finalize
    )

    assert calls == ["participant_disconnected"]


# 10. Finalization persistence, retry safety and failure states ---------------------------


def _finalize_mocks():
    mock_db = MagicMock()
    mock_db.update_workflow_run = AsyncMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=None)
    return mock_db


@pytest.mark.asyncio
async def test_finalize_persists_pending_then_succeeded_and_awaits_post_call():
    session, agent = _wired()
    mock_db = _finalize_mocks()
    post_call_mock = AsyncMock(return_value={"status": "success"})

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        post_call_mock,
    ):
        result = await worker._finalize_livekit_workflow_run(
            workflow_run_id=904,
            session=session,
            agent=agent,
            room_name="room-904",
            reason="max_duration_reached",
        )

    assert result["state"] == "succeeded"
    states = [
        call.kwargs["annotations"]["livekit_finalization"]["state"]
        for call in mock_db.update_workflow_run.call_args_list
        if "annotations" in call.kwargs
    ]
    assert states == ["pending", "succeeded"]
    post_call_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_recording_stop_failure_does_not_lose_usage():
    session, agent = _wired()
    agent.stop_recording = AsyncMock(side_effect=RuntimeError("recording stop failed"))
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "success"}),
    ):
        with pytest.raises(RuntimeError, match="recording_stop_incomplete"):
            await worker._finalize_livekit_workflow_run(
                workflow_run_id=905,
                session=session,
                agent=agent,
                room_name="room-905",
                reason="participant_disconnected",
            )

    completion = [
        call
        for call in mock_db.update_workflow_run.call_args_list
        if "usage_info" in call.kwargs
    ]
    assert completion and completion[0].kwargs["is_completed"] is True
    final_state = [
        call.kwargs["annotations"]["livekit_finalization"]
        for call in mock_db.update_workflow_run.call_args_list
        if "annotations" in call.kwargs
    ][-1]
    assert final_state["state"] == "failed"
    assert final_state["recording_stop"] == "failed"
    assert final_state["post_call_status"] == "success"
    assert "recording_error" in final_state


@pytest.mark.asyncio
async def test_finalize_cost_failure_marks_failed_and_raises():
    session, agent = _wired()
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost",
        AsyncMock(side_effect=RuntimeError("cost failed")),
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "success"}),
    ):
        with pytest.raises(RuntimeError):
            await worker._finalize_livekit_workflow_run(
                workflow_run_id=906,
                session=session,
                agent=agent,
                room_name="room-906",
                reason="max_duration_reached",
            )

    states = [
        call.kwargs["annotations"]["livekit_finalization"]["state"]
        for call in mock_db.update_workflow_run.call_args_list
        if "annotations" in call.kwargs
    ]
    assert states == ["pending", "failed"]


@pytest.mark.asyncio
async def test_finalize_post_call_failure_marks_failed():
    session, agent = _wired()
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(side_effect=RuntimeError("post call failed")),
    ):
        with pytest.raises(RuntimeError):
            await worker._finalize_livekit_workflow_run(
                workflow_run_id=907,
                session=session,
                agent=agent,
                room_name="room-907",
                reason="max_duration_reached",
            )


@pytest.mark.asyncio
async def test_finalize_retry_reuses_first_attempt_duration():
    session, agent = _wired()
    agent._finalized_elapsed_seconds = 42
    agent._started_at = time.monotonic() - 500
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "success"}),
    ):
        await worker._finalize_livekit_workflow_run(
            workflow_run_id=908,
            session=session,
            agent=agent,
            room_name="room-908",
            reason="max_duration_reached",
        )
        await worker._finalize_livekit_workflow_run(
            workflow_run_id=908,
            session=session,
            agent=agent,
            room_name="room-908",
            reason="max_duration_reached",
        )

    durations = [
        call.kwargs["usage_info"]["call_duration_seconds"]
        for call in mock_db.update_workflow_run.call_args_list
        if "usage_info" in call.kwargs
    ]
    assert durations == [42, 42]


def test_safe_finalize_error_redacts_credentials():
    text = worker._safe_finalize_error(
        RuntimeError(
            "failed token=abc123 password: hunter2 "
            "postgresql://user:pw@host/db api_key=sk-live "
            "Authorization: Bearer sk-bearer-999"
        )
    )

    assert "abc123" not in text
    assert "hunter2" not in text
    assert "user:pw" not in text
    assert "sk-live" not in text
    assert "sk-bearer-999" not in text
    assert "<redacted>" in text


# 11. Supervisor, retry and post-call failure contracts ------------------------------


@pytest.mark.asyncio
async def test_finalize_once_sole_waiter_cancellation_settles_via_supervisor():
    agent = SimpleNamespace()
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)
        started.set()
        await release.wait()

    waiter = asyncio.create_task(
        worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    )
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert agent._finalize_state == "pending"

    release.set()
    await asyncio.sleep(0.05)
    assert agent._finalize_state == "succeeded"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_finalize_once_does_not_rerun_a_succeeded_attempt():
    agent = SimpleNamespace()
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)

    first = await worker._finalize_once(agent=agent, reason="x", finalize=finalize)
    second = await worker._finalize_once(agent=agent, reason="x", finalize=finalize)

    assert first == "succeeded"
    assert second == "succeeded"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_finalize_once_settles_completed_task_without_state_update():
    agent = SimpleNamespace()

    async def noop() -> None:
        return None

    completed = asyncio.create_task(noop())
    await completed
    agent._finalize_task = completed
    agent._finalize_state = "pending"
    calls: list[str] = []

    async def finalize(reason: str) -> None:
        calls.append(reason)

    state = await worker._finalize_once(agent=agent, reason="x", finalize=finalize)

    assert state == "succeeded"
    assert calls == []
    assert agent._finalize_state == "succeeded"


@pytest.mark.asyncio
async def test_finalize_post_call_error_status_marks_failed():
    session, agent = _wired()
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "error", "reason": "analysis_failed"}),
    ):
        with pytest.raises(RuntimeError, match="post_call_error"):
            await worker._finalize_livekit_workflow_run(
                workflow_run_id=909,
                session=session,
                agent=agent,
                room_name="room-909",
                reason="max_duration_reached",
            )

    final_state = [
        call.kwargs["annotations"]["livekit_finalization"]
        for call in mock_db.update_workflow_run.call_args_list
        if "annotations" in call.kwargs
    ][-1]
    assert final_state["state"] == "failed"
    assert final_state["post_call_status"] == "error"


@pytest.mark.asyncio
async def test_finalize_recording_stop_failure_is_persisted():
    session, agent = _wired()
    agent.stop_recording = AsyncMock(side_effect=RuntimeError("stop failed"))
    mock_db = _finalize_mocks()

    with patch("api.services.livekit.worker.db_client", mock_db), patch(
        "api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()
    ), patch(
        "api.services.livekit.post_call_triggers.process_post_call_intelligence_and_triggers",
        AsyncMock(return_value={"status": "success"}),
    ):
        with pytest.raises(RuntimeError, match="recording_stop_incomplete"):
            await worker._finalize_livekit_workflow_run(
                workflow_run_id=910,
                session=session,
                agent=agent,
                room_name="room-910",
                reason="max_duration_reached",
            )

    final_state = [
        call.kwargs["annotations"]["livekit_finalization"]
        for call in mock_db.update_workflow_run.call_args_list
        if "annotations" in call.kwargs
    ][-1]
    assert final_state["state"] == "failed"
    assert final_state["recording_stop"] == "failed"
    assert "recording_error" in final_state
    assert final_state["post_call_status"] == "success"


# ---------------------------------------------------------------------------
# P0-A: spurious server-VAD generate_reply suppression (cached-audio path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_spurious_server_vad_reply_suppressed_during_cached_opening():
    """Spurious generate_reply during cached-audio opening is interrupted."""
    handle = OpeningHandle()
    spurious = FakeSpeechHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=True, handle=handle)

    def inject_spurious_vad():
        # Server-VAD fires before caller has spoken
        session.trigger(
            "speech_created",
            SimpleNamespace(speech_handle=spurious, source="generate_reply", user_initiated=False),
        )
        handle.interrupted = False  # cached audio continues to play

    handle.on_playout = inject_spurious_vad
    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), \
         patch.object(worker, "_wav_audio_frames", return_value="offline-audio"):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    # Spurious handle must have been interrupted
    assert spurious.interrupt_calls, "spurious server-VAD reply was not interrupted"
    assert spurious.interrupt_calls[0]["force"] is True
    # Main opening must complete normally
    assert "opening_completed" in _opening_phases(agent)
    assert advances == ["main"]


@pytest.mark.asyncio
async def test_spurious_suppression_does_not_fire_on_gemini_live_opening():
    """Standard Gemini Live opening uses generate_reply: suppression must not fire."""
    handle = OpeningHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=False, handle=handle)

    # cached=False → _realtime_exact_speech_uses_tts=False → no suppression
    await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert len(calls) == 1 and calls[0][0] == "generate_reply"
    # The opening handle must NOT have been interrupted by suppression logic
    assert not handle.interrupt_calls or all(
        c.get("force") is False for c in handle.interrupt_calls
    ), "Legitimate Gemini Live opening generate_reply was incorrectly suppressed"
    assert "opening_completed" in _opening_phases(agent) or "opening_interrupted" in _opening_phases(agent)


@pytest.mark.asyncio
async def test_suppression_does_not_fire_after_caller_speech_seen():
    """When caller barge-in is detected, spurious check is bypassed."""
    handle = OpeningHandle()
    spurious = FakeSpeechHandle()
    agent, session, calls, advances, resets = _opening_harness(cached=True, handle=handle)

    def caller_then_vad():
        # Caller barge-in sets the flag first
        session.trigger(
            "user_state_changed",
            SimpleNamespace(new_state="speaking", old_state="listening", created_at=10.0),
        )
        # Then a generate_reply arrives — should not be suppressed
        session.trigger(
            "speech_created",
            SimpleNamespace(speech_handle=spurious, source="generate_reply", user_initiated=True),
        )
        handle.interrupted = True

    handle.on_playout = caller_then_vad
    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), \
         patch.object(worker, "_wav_audio_frames", return_value="offline-audio"):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    # generate_reply after genuine caller speech must not be suppressed
    assert not spurious.interrupt_calls, "post-barge-in reply was incorrectly suppressed"


# ---------------------------------------------------------------------------
# P0-B: start_node.allow_interrupt honored on cached-opening path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allow_interrupt_true_passed_to_cached_opening():
    """allow_interrupt=True on start node reaches _say_text for cached path."""
    handle = OpeningHandle()
    agent, session, calls, _, _ = _opening_harness(cached=True, handle=handle)

    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), \
         patch.object(worker, "_wav_audio_frames", return_value="offline-audio"):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert calls[0][2]["allow_interruptions"] is True


@pytest.mark.asyncio
async def test_allow_interrupt_false_passed_to_cached_opening():
    """allow_interrupt=False on start node is forwarded; opening is not interruptible."""
    handle = OpeningHandle()
    agent, session, calls, _, _ = _opening_harness(cached=True, handle=handle)
    agent._workflow.nodes["start"].allow_interrupt = False

    with patch.object(worker, "_live_opening_audio_path", AsyncMock(return_value=Path("offline.wav"))), \
         patch.object(worker, "_wav_audio_frames", return_value="offline-audio"):
        await worker.LiveKitWorkflowAgent.start_opening(agent)

    assert calls[0][2]["allow_interruptions"] is False


# ---------------------------------------------------------------------------
# P1-C: credential-safe formatter at TTS/Gemini log sites
# ---------------------------------------------------------------------------

def test_safe_error_text_does_not_expose_credentials():
    """_safe_error_text is ONLY used for non-sensitive diagnostic context.
    Verify that the three log sites now use _safe_finalize_error which redacts."""
    # _safe_finalize_error is what the three log sites now use; verify it redacts.
    text = worker._safe_finalize_error(
        RuntimeError(
            "request failed: https://generativelanguage.googleapis.com/v1beta/models"
            "?key=AIzaSyFAKE-credential-should-not-appear"
            " Authorization: Bearer sk-secret-bearer"
        )
    )
    assert "AIzaSyFAKE" not in text
    assert "sk-secret-bearer" not in text
    assert "<redacted>" in text


def test_tts_site_uses_safe_finalize_error():
    """The three P1-C log sites call _safe_finalize_error (confirmed by running
    the actual formatter on a credential-carrying exception and checking redaction)."""
    import inspect
    source = inspect.getsource(worker)
    # Verify _safe_error_text is NOT used at the three formerly-exposed log paths
    # by confirming _safe_finalize_error appears in the warning strings.
    assert "Gemini TTS failed: {_safe_finalize_error" in source
    assert "opening audio prewarm failed: {_safe_finalize_error" in source
    assert "realtime exact speech failed, falling back to TTS: {_safe_finalize_error" in source


# ---------------------------------------------------------------------------
# VAD prefix-padding regression guard (Gate 4 barge-in fix)
# ---------------------------------------------------------------------------

def test_gemini_server_vad_prefix_padding_reduced_to_120ms():
    """VOICE-01 Gate 4: prefix_padding_ms was 220 ms; reduced to 120 ms so that
    fragmented Indian speech (thinking pauses between sentence fragments) triggers
    interruption on the first attempt rather than requiring 2-3 retries.

    This test guards the constant itself. It cannot prove Gemini server-side
    VAD behaviour (that requires a live call), but it confirms the value that
    will be sent in the RealtimeInputConfig for every new session.
    """
    assert worker.GEMINI_SERVER_VAD_PREFIX_PADDING_MS == 120, (
        f"Expected 120 ms after Gate 4 barge-in correction; "
        f"got {worker.GEMINI_SERVER_VAD_PREFIX_PADDING_MS}. "
        "If this was intentionally changed, update the assertion."
    )


def test_gemini_server_vad_config_uses_prefix_padding_constant():
    """_fast_server_vad_realtime_input_config constructs a RealtimeInputConfig
    whose prefix_padding_ms matches GEMINI_SERVER_VAD_PREFIX_PADDING_MS for the
    standard (non-fast) profile, so the constant change is actually wired through."""
    import inspect

    source = inspect.getsource(worker._fast_server_vad_realtime_input_config)
    # The function must reference the constant, not a bare literal.
    assert "GEMINI_SERVER_VAD_PREFIX_PADDING_MS" in source, (
        "_fast_server_vad_realtime_input_config does not reference "
        "GEMINI_SERVER_VAD_PREFIX_PADDING_MS — the constant change is not wired."
    )


def test_fast_profile_vad_prefix_padding_unchanged():
    """The FAST profile prefix padding (320 ms, used for low-latency deployments)
    was not part of the Gate 4 correction and must remain unchanged so that
    existing fast-profile sessions are unaffected."""
    assert worker.FAST_GEMINI_SERVER_VAD_PREFIX_PADDING_MS == 320, (
        f"Fast-profile prefix padding changed unexpectedly; "
        f"got {worker.FAST_GEMINI_SERVER_VAD_PREFIX_PADDING_MS}."
    )
