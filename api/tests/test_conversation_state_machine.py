import time
import pytest
from api.services.livekit.conversation_state import (
    ConversationState,
    ConversationStateMachine,
    VALID_TRANSITIONS,
)


def test_state_machine_initial_state():
    sm = ConversationStateMachine(run_id=101)
    assert sm.state == ConversationState.IDLE
    assert sm.turn_id == 0
    assert len(sm.history()) == 1
    assert sm.history()[0]["state"] == "IDLE"


def test_valid_conversation_turn_cycle():
    sm = ConversationStateMachine(run_id=101)

    # Agent starts with greeting
    assert sm.transition_to(ConversationState.AGENT_SPEAKING, reason="greeting")
    assert sm.state == ConversationState.AGENT_SPEAKING

    # Greeting completes -> Listening
    assert sm.transition_to(ConversationState.LISTENING, reason="greeting_ended")
    assert sm.state == ConversationState.LISTENING

    # Customer speaks (Turn 1)
    assert sm.transition_to(ConversationState.CUSTOMER_SPEAKING, reason="user_speaking")
    assert sm.state == ConversationState.CUSTOMER_SPEAKING
    assert sm.turn_id == 1

    # Customer finishes speaking -> Thinking
    assert sm.transition_to(ConversationState.THINKING, reason="user_speech_end")
    assert sm.state == ConversationState.THINKING

    # Model generates answer -> Agent speaking
    assert sm.transition_to(ConversationState.AGENT_SPEAKING, reason="response_audio_ready")
    assert sm.state == ConversationState.AGENT_SPEAKING

    # Customer interrupts during speech
    assert sm.transition_to(ConversationState.INTERRUPTED, reason="barge_in")
    assert sm.state == ConversationState.INTERRUPTED

    # Customer continues speaking after interruption
    assert sm.transition_to(ConversationState.CUSTOMER_SPEAKING, reason="user_post_interruption")
    assert sm.state == ConversationState.CUSTOMER_SPEAKING

    # Customer wraps up
    assert sm.transition_to(ConversationState.WRAPUP, reason="call_completed")
    assert sm.state == ConversationState.WRAPUP


def test_invalid_transitions_are_rejected():
    sm = ConversationStateMachine(run_id=102)
    # Cannot jump from IDLE to THINKING directly
    assert not sm.transition_to(ConversationState.THINKING, reason="invalid_jump")
    assert sm.state == ConversationState.IDLE

    # Valid to LISTENING
    assert sm.transition_to(ConversationState.LISTENING)
    # Cannot jump from LISTENING to INTERRUPTED (agent wasn't speaking)
    assert not sm.transition_to(ConversationState.INTERRUPTED)
    assert sm.state == ConversationState.LISTENING


def test_forced_emergency_transition():
    sm = ConversationStateMachine(run_id=103)
    # Forced transition succeeds even if not in transition matrix
    assert sm.transition_to(ConversationState.THINKING, reason="emergency_sync", force=True)
    assert sm.state == ConversationState.THINKING


def test_turn_id_increments_per_customer_turn():
    sm = ConversationStateMachine(run_id=104)
    sm.transition_to(ConversationState.LISTENING)

    sm.transition_to(ConversationState.CUSTOMER_SPEAKING)
    assert sm.turn_id == 1
    sm.transition_to(ConversationState.THINKING)
    sm.transition_to(ConversationState.AGENT_SPEAKING)
    sm.transition_to(ConversationState.LISTENING)

    sm.transition_to(ConversationState.CUSTOMER_SPEAKING)
    assert sm.turn_id == 2


def test_thinking_timeout_recovery(monkeypatch):
    sm = ConversationStateMachine(
        run_id=105,
        timeouts={ConversationState.THINKING: 0.1},
    )
    sm.transition_to(ConversationState.LISTENING)
    sm.transition_to(ConversationState.CUSTOMER_SPEAKING)
    sm.transition_to(ConversationState.THINKING)

    # Immediately check: not timed out yet
    recovered = sm.check_timeouts()
    assert recovered is None
    assert sm.state == ConversationState.THINKING

    # Wait past timeout
    time.sleep(0.12)
    recovered = sm.check_timeouts()
    assert recovered == ConversationState.LISTENING
    assert sm.state == ConversationState.LISTENING


def test_state_change_callback_fires():
    events = []

    def on_change(prev, new_st, reason):
        events.append((prev, new_st, reason))

    sm = ConversationStateMachine(run_id=106, on_state_changed=on_change)
    sm.transition_to(ConversationState.LISTENING, reason="start_listen")

    assert len(events) == 1
    assert events[0] == (ConversationState.IDLE, ConversationState.LISTENING, "start_listen")


def test_history_records_durations():
    sm = ConversationStateMachine(run_id=107)
    sm.transition_to(ConversationState.LISTENING)
    time.sleep(0.05)
    sm.transition_to(ConversationState.CUSTOMER_SPEAKING)

    hist = sm.history()
    assert len(hist) >= 3
    # Check that previous state duration was recorded
    assert hist[1]["duration_seconds"] >= 0.04

