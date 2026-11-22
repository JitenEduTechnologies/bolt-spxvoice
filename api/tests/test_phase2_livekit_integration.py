import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.livekit.conversation_state import (
    ConversationState,
    ConversationStateMachine,
)
from api.services.livekit.interruption_filter import (
    InterruptionDecision,
    InterruptionFilter,
)
from api.services.livekit.latency_tracker import LatencyTracker
from api.services.livekit.silence_manager import SilenceManager
from api.services.livekit import worker


class MockSession:
    """Mock LiveKit AgentSession that records event callbacks and supports interrupt()."""

    def __init__(self):
        self._handlers = {}
        self.interrupt_called = False
        self.interrupt_count = 0
        self.history = SimpleNamespace(to_dict=lambda: {"items": []})
        self.usage = None

    def on(self, event_name: str):
        def decorator(fn):
            self._handlers[event_name] = fn
            return fn

        return decorator

    def interrupt(self, *, force: bool = False):
        self.interrupt_called = True
        self.interrupt_count += 1
        return None

    def trigger(self, event_name: str, event_obj):
        handler = self._handlers.get(event_name)
        if handler:
            return handler(event_obj)
        return None


class MockAgent:
    """Mock LiveKitWorkflowAgent containing real Phase 2 state and managers."""

    def __init__(self, run_id: int = 501, language: str = "en"):
        self._workflow_run_id = run_id
        self._started_at = time.monotonic()
        self._user_speech_started_at = None
        self._last_user_speech_end_at = None
        self._last_final_transcript_at = None
        self._feedbacks = []
        self._shutdown_task = None
        self._shutdown_reason = None

        self._state_machine = ConversationStateMachine(
            run_id=run_id,
            on_state_changed=self._on_state_changed,
        )
        self._interruption_filter = InterruptionFilter(
            min_duration_seconds=0.45,
            min_words_for_barge_in=2,
            backchannel_filter_enabled=True,
        )
        self._latency_tracker = LatencyTracker(run_id=run_id)
        self._silence_manager = SilenceManager(
            stage1_seconds=0.04,
            stage2_seconds=0.08,
            stage3_seconds=0.12,
            language=language,
            speak_callback=self._speak_silence_prompt,
            disconnect_callback=self._handle_silence_disconnect,
        )
        self.spoken_prompts = []
        self.disconnected_reasons = []

    def _on_state_changed(self, prev, new_st, reason):
        self._feedbacks.append((prev, new_st, reason))

    def _emit_feedback(self, payload):
        self._feedbacks.append(payload)

    async def _speak_silence_prompt(self, text: str):
        self.spoken_prompts.append(text)
        self._state_machine.transition_to(
            ConversationState.AGENT_SPEAKING,
            reason="silence_recovery_prompt",
            force=True,
        )

    async def _handle_silence_disconnect(self, reason: str):
        self.disconnected_reasons.append(reason)
        self._state_machine.transition_to(
            ConversationState.WRAPUP,
            reason="silence_timeout_disconnect",
            force=True,
        )
        self.schedule_shutdown(reason="no_response_timeout", delay=0.0)

    def schedule_shutdown(self, reason: str, delay: float = 0.0):
        self._shutdown_reason = reason
        self._shutdown_task = True

    async def stop_recording(self):
        return None


def test_session_event_bindings_drive_state_and_latency():
    """Verify _register_feedback_handlers binds to LiveKit session events and drives state transitions."""
    session = MockSession()
    agent = MockAgent(run_id=502)

    # Register handlers exactly as in worker.py
    worker._register_feedback_handlers(session, agent)

    # 1. Agent starts in IDLE; start speaking (e.g. greeting)
    agent_spk_ev = SimpleNamespace(new_state="speaking", old_state="listening", created_at=10.0)
    session.trigger("agent_state_changed", agent_spk_ev)
    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING

    # 2. Agent finishes speaking -> LISTENING
    agent_listen_ev = SimpleNamespace(new_state="listening", old_state="speaking", created_at=12.0)
    session.trigger("agent_state_changed", agent_listen_ev)
    assert agent._state_machine.state == ConversationState.LISTENING

    # 3. User begins speaking -> CUSTOMER_SPEAKING
    user_spk_ev = SimpleNamespace(new_state="speaking", old_state="listening", created_at=13.0)
    session.trigger("user_state_changed", user_spk_ev)
    assert agent._state_machine.state == ConversationState.CUSTOMER_SPEAKING
    assert agent._state_machine.turn_id == 1

    # 4. User finishes speaking -> THINKING
    user_listen_ev = SimpleNamespace(new_state="listening", old_state="speaking", created_at=15.0)
    session.trigger("user_state_changed", user_listen_ev)
    assert agent._state_machine.state == ConversationState.THINKING

    # 5. Agent responds -> AGENT_SPEAKING
    agent_res_ev = SimpleNamespace(new_state="speaking", old_state="listening", created_at=15.7)
    session.trigger("agent_state_changed", agent_res_ev)
    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING

    # Verify turnaround latency was measured: 15.7 - 15.0 = 700ms
    summary = agent._latency_tracker.summary()
    assert summary["measured_turns"] == 1
    assert summary["turnaround_ms"]["p50"] == 700.0


def test_transcription_events_are_retained_but_never_trigger_interrupt():
    """VOICE-01: final transcripts are retained for the caller's words only.

    With the Google realtime plugin a final input transcript is emitted when
    Gemini's generation for that turn completes, so it must never be used as a
    barge-in trigger (it would cancel the agent's fresh answer). Barge-in is
    owned by the SDK/provider and surfaces through user_state/speech events.
    """
    session = MockSession()
    agent = MockAgent(run_id=503)
    worker._register_feedback_handlers(session, agent)

    # Put agent in AGENT_SPEAKING
    agent._state_machine.transition_to(ConversationState.AGENT_SPEAKING, reason="ai_speech")

    for transcript, created_at in (
        ("yeah", 20.0),
        ("theek hai", 20.5),
        ("Wait a second, stop there", 21.0),
    ):
        ev = SimpleNamespace(is_final=True, transcript=transcript, created_at=created_at)
        session.trigger("user_input_transcribed", ev)
        # Never interrupt from a transcript, never fabricate an INTERRUPTED state.
        assert session.interrupt_called is False
        assert agent._state_machine.state == ConversationState.AGENT_SPEAKING
        assert agent._last_final_transcript_at == created_at

    # The caller's words are still forwarded as feedback (retained).
    transcripts = [
        fb["payload"]["text"]
        for fb in agent._feedbacks
        if isinstance(fb, dict) and fb.get("type") == "rtf-user-transcription"
    ]
    assert transcripts == ["yeah", "theek hai", "Wait a second, stop there"]

    # A real barge-in is reported from the SDK's user_state signal instead.
    session.trigger(
        "user_state_changed",
        SimpleNamespace(new_state="speaking", old_state="listening", created_at=21.5),
    )
    assert agent._state_machine.state == ConversationState.INTERRUPTED
    assert session.interrupt_called is False


@pytest.mark.asyncio
async def test_silence_watchdog_progression_and_graceful_disconnect():
    """Verify silence watchdog advances 3 stages and triggers graceful shutdown with no_response."""
    agent = MockAgent(run_id=504, language="en")
    agent._state_machine.transition_to(ConversationState.LISTENING, reason="ready")

    # Poll loop for 0.15s (thresholds are 0.04s, 0.08s, 0.12s)
    for _ in range(8):
        await asyncio.sleep(0.02)
        agent._state_machine.check_timeouts()
        await agent._silence_manager.evaluate_silence(agent._state_machine.state)
        # If Stage 1 or 2 spoke, simulate returning to LISTENING
        if agent._state_machine.state == ConversationState.AGENT_SPEAKING:
            agent._state_machine.transition_to(
                ConversationState.LISTENING,
                reason="prompt_ended",
                force=True,
            )

    # Verify all 3 stages executed
    assert len(agent.spoken_prompts) >= 3
    assert "Take your time" in agent.spoken_prompts[0]
    assert "Are you still there" in agent.spoken_prompts[1]
    assert "disconnected" in agent.spoken_prompts[2]

    # Verify graceful disconnect and no_response disposition
    assert len(agent.disconnected_reasons) == 1
    assert agent._silence_manager.disposition == "no_response"
    assert agent._shutdown_reason == "no_response_timeout"
    assert agent._state_machine.state == ConversationState.WRAPUP


@pytest.mark.asyncio
async def test_silence_timer_cancelled_by_customer_speech_and_repeated_silence():
    """Verify timer resets when customer speaks, avoiding premature disconnection."""
    agent = MockAgent(run_id=505, language="en")
    agent._silence_manager.stage1_seconds = 0.03
    agent._silence_manager.stage2_seconds = 0.30
    agent._silence_manager.stage3_seconds = 0.60
    agent._state_machine.transition_to(ConversationState.LISTENING, reason="ready")

    # Wait 0.05s -> Stage 1 triggers
    await asyncio.sleep(0.05)
    await agent._silence_manager.evaluate_silence(agent._state_machine.state)
    assert agent._silence_manager.current_stage == 1

    # Simulate agent returning to LISTENING after nudge
    agent._state_machine.transition_to(ConversationState.LISTENING, reason="nudge_ended", force=True)

    # Customer speaks -> cancels silence timer
    agent._silence_manager.reset_silence(reason="user_spoke")
    assert agent._silence_manager.current_stage == 0
    assert agent._silence_manager.silence_duration < 0.02

    # Agent is not disconnected
    assert agent._silence_manager.disposition is None
    assert agent._shutdown_reason is None

    # Wait again -> Stage 1 re-triggers cleanly (repeated silence recovery)
    await asyncio.sleep(0.05)
    await agent._silence_manager.evaluate_silence(agent._state_machine.state)
    assert agent._silence_manager.current_stage == 1


@pytest.mark.asyncio
async def test_finalize_persists_latency_and_disposition_in_gathered_context():
    """Verify _finalize_livekit_workflow_run persists latency, history, and disposition into DB."""
    session = MockSession()
    agent = MockAgent(run_id=506)

    # Record some latency metrics
    agent._latency_tracker.record_user_speech_end(1, timestamp=10.0)
    agent._latency_tracker.record_agent_speech_start(1, timestamp=10.8) # 800ms
    # Set disposition to no_response
    agent._silence_manager._disposition = "no_response"

    mock_db_client = MagicMock()
    mock_db_client.update_workflow_run = AsyncMock()
    mock_db_client.get_workflow_run_by_id = AsyncMock(return_value=SimpleNamespace(cost_info={}))

    with patch("api.services.livekit.worker.db_client", mock_db_client), \
         patch("api.services.livekit.worker._calculate_livekit_workflow_run_cost", AsyncMock()):
        await worker._finalize_livekit_workflow_run(
            workflow_run_id=506,
            session=session,
            agent=agent,
            room_name="test-room-506",
            reason="no_response_timeout",
        )

    # Verify db_client.update_workflow_run called with gathered_context
    assert mock_db_client.update_workflow_run.called
    call_kwargs = mock_db_client.update_workflow_run.call_args.kwargs
    gathered = call_kwargs["gathered_context"]

    assert gathered["livekit_room"] == "test-room-506"
    assert gathered["livekit_shutdown_reason"] == "no_response_timeout"
    assert gathered["disposition"] == "no_response"

    # Latency metrics verification
    latency = gathered["turn_latency_metrics"]
    assert latency["total_turns"] == 1
    assert latency["turnaround_ms"]["p50"] == 800.0
    assert latency["turnaround_ms"]["target_met"] is True

    # State history verification
    assert len(gathered["conversation_history"]) >= 1
    # Interruption stats verification
    assert "interruptions" in gathered["interruption_stats"]
