import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routes.workflow import validate_phase2_configurations
from api.services.livekit.conversation_state import (
    ConversationState,
    ConversationStateMachine,
)
from api.services.livekit.interruption_filter import (
    InterruptionDecision,
    InterruptionFilter,
)
from api.services.livekit.language_packs import get_language_pack
from api.services.livekit.latency_tracker import LatencyTracker
from api.services.livekit.silence_manager import SilenceManager
from api.services.livekit import worker


# ---------------------------------------------------------------------------
# Test Helpers & Mocks
# ---------------------------------------------------------------------------

class MockSession:
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


class MockAgentForRemediation:
    def __init__(self, run_id: int = 701, language: str = "hi-en"):
        self._workflow_run_id = run_id
        self._started_at = time.monotonic()
        self._tts_language = language
        self._language_pack = get_language_pack(language)
        self._user_speech_started_at = None
        self._last_user_speech_end_at = None
        self._last_final_transcript_at = None
        self._feedbacks = []
        self._shutdown_task = None

        self._state_machine = ConversationStateMachine(run_id=run_id)
        self._interruption_filter = InterruptionFilter(
            min_duration_seconds=0.45,
            min_words_for_barge_in=2,
            backchannel_filter_enabled=True,
            custom_backchannel_words=self._language_pack.backchannel_keywords,
        )
        self._latency_tracker = LatencyTracker(run_id=run_id)
        self._silence_manager = SilenceManager(
            stage1_seconds=4.0,
            stage2_seconds=8.0,
            stage3_seconds=14.0,
            language=language,
        )

    def _emit_feedback(self, payload):
        self._feedbacks.append(payload)


# ---------------------------------------------------------------------------
# Gap 1 Verification: Customer Utterance Duration & Backchannel Retention
# ---------------------------------------------------------------------------

def test_gap1_customer_utterance_duration_and_backchannel_state_retention():
    """Verify customer speech timing is measured from speech start, and that a short
    utterance the SDK does not accept as a barge-in returns the state to
    AGENT_SPEAKING instead of forcing THINKING while the agent is still speaking.

    VOICE-01 contract: barge-in acceptance is decided by the SDK/provider, not by
    delayed final transcripts, so transcripts never call session.interrupt().
    """
    session = MockSession()
    agent = MockAgentForRemediation(run_id=702)
    worker._register_feedback_handlers(session, agent)

    # 1. Agent is actively speaking (the SDK reports agent_state="speaking")
    agent._state_machine.transition_to(ConversationState.AGENT_SPEAKING, reason="agent_turn")
    session.agent_state = "speaking"

    # 2. Customer speaks a short backchannel at t=10.0 -> reported as caller speech
    spk_start_ev = SimpleNamespace(new_state="speaking", old_state="listening", created_at=10.0)
    session.trigger("user_state_changed", spk_start_ev)
    assert agent._user_speech_started_at == 10.0
    assert agent._state_machine.state == ConversationState.INTERRUPTED

    # 3. Transcript arrives at t=10.3 with "theek hai": retained, never an interrupt
    transcript_ev = SimpleNamespace(is_final=True, transcript="theek hai", created_at=10.3)
    session.trigger("user_input_transcribed", transcript_ev)
    assert session.interrupt_called is False
    assert agent._state_machine.state == ConversationState.INTERRUPTED

    # 4. Customer stops speaking at t=10.4 while the agent is STILL speaking:
    #    the SDK did not accept the barge-in -> back to AGENT_SPEAKING, not THINKING.
    spk_end_ev = SimpleNamespace(new_state="listening", old_state="speaking", created_at=10.4)
    session.trigger("user_state_changed", spk_end_ev)
    assert agent._state_machine.state == ConversationState.AGENT_SPEAKING

    # 5. Now customer gives a genuine interruption at t=11.0 and the SDK silences the agent
    barge_start_ev = SimpleNamespace(new_state="speaking", old_state="listening", created_at=11.0)
    session.trigger("user_state_changed", barge_start_ev)
    assert agent._user_speech_started_at == 11.0
    assert agent._state_machine.state == ConversationState.INTERRUPTED

    session.agent_state = "listening"
    silenced_ev = SimpleNamespace(new_state="listening", old_state="speaking", created_at=11.2)
    session.trigger("agent_state_changed", silenced_ev)
    assert agent._state_machine.state == ConversationState.CUSTOMER_SPEAKING

    barge_transcript_ev = SimpleNamespace(
        is_final=True, transcript="Rukiye mujhe baat karni hai", created_at=12.2
    )
    session.trigger("user_input_transcribed", barge_transcript_ev)
    assert session.interrupt_called is False

    # When user finishes their genuine question, it transitions to THINKING
    user_done_ev = SimpleNamespace(new_state="listening", old_state="speaking", created_at=12.5)
    session.trigger("user_state_changed", user_done_ev)
    assert agent._state_machine.state == ConversationState.THINKING


# ---------------------------------------------------------------------------
# Gap 2 Verification: Multilingual Language Packs Runtime Connection
# ---------------------------------------------------------------------------

def test_gap2_multilingual_language_packs_runtime_prompts():
    """Verify Hinglish, Tenglish, Telugu, Hindi, and English silence prompts load correctly."""
    # Hinglish (hi-en)
    sm_hien = SilenceManager(language="hi-en")
    assert "main sun raha hoon" in sm_hien.prompts[1]
    assert "connection issue" in sm_hien.prompts[3]

    # Tenglish (te-en)
    sm_teen = SilenceManager(language="te-en")
    assert "nenu vintunnanu" in sm_teen.prompts[1]
    assert "connection drop" in sm_teen.prompts[3]

    # Telugu (te)
    sm_te = SilenceManager(language="te")
    assert "Meeru aalochinchi" in sm_te.prompts[1]
    assert "Selavu" in sm_te.prompts[3]

    # Hindi (hi)
    sm_hi = SilenceManager(language="hi")
    assert "Aap aaram se boliye" in sm_hi.prompts[1]
    assert "Alvida" in sm_hi.prompts[3]

    # Custom prompt override preserved
    sm_custom = SilenceManager(language="hi-en", custom_prompts={1: "Custom nudge"})
    assert sm_custom.prompts[1] == "Custom nudge"
    assert "connection issue" in sm_custom.prompts[3]


def test_gap2_system_prompt_language_guidance_injection():
    """Verify language pack system prompt rules are injected into node instructions."""
    pack_hien = get_language_pack("hi-en")
    assert "Hinglish" in pack_hien.system_prompt_language_rule

    pack_teen = get_language_pack("te-en")
    assert "Tenglish" in pack_teen.system_prompt_language_rule

    # Verify agent attaches language pack to interruption filter
    agent_te = MockAgentForRemediation(run_id=703, language="te")
    # Telugu backchannel words should be in the filter
    assert "avunu" in agent_te._interruption_filter.backchannel_vocab
    assert "sare" in agent_te._interruption_filter.backchannel_vocab
    assert agent_te._interruption_filter.evaluate(0.3, "avunu") == InterruptionDecision.BACKCHANNEL


# ---------------------------------------------------------------------------
# Gap 3 Verification: Robust Tool Execution Silence Gating
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gap3_silence_gated_during_tool_execution_and_exceptions():
    """Verify silence recovery never triggers while tools run, and set_tool_running(False)

    is guaranteed even when an exception occurs.
    """
    sm = SilenceManager(stage1_seconds=0.04)

    # Tool starts
    sm.set_tool_running(True)
    assert sm._is_tool_running is True

    # Sleep past stage 1 threshold
    await asyncio.sleep(0.06)
    # Evaluate silence while tool is running: MUST return None
    res = await sm.evaluate_silence(ConversationState.LISTENING)
    assert res is None
    assert sm.current_stage == 0

    # Simulate tool error with try/finally guarantee
    try:
        raise RuntimeError("External KB Timeout or Disconnect")
    except RuntimeError:
        pass
    finally:
        sm.set_tool_running(False)

    assert sm._is_tool_running is False

    # Once tool finishes, silence progression resumes cleanly
    await asyncio.sleep(0.05)
    res_after = await sm.evaluate_silence(ConversationState.LISTENING)
    assert res_after == 1
    assert sm.current_stage == 1


# ---------------------------------------------------------------------------
# Gap 4 Verification: Backend Validation of Owner Configuration
# ---------------------------------------------------------------------------

def test_gap4_valid_silence_and_interruption_configurations():
    """Verify valid owner configurations pass validation without error."""
    valid_cfg = {
        "silence_config": {
            "stage1_seconds": 3.0,
            "stage2_seconds": 7.0,
            "stage3_seconds": 12.0,
        },
        "interruption_config": {
            "min_duration_seconds": 0.5,
            "min_words": 3,
            "backchannel_filter_enabled": True,
            "custom_backchannel_words": ["arre", "achha"],
        },
    }
    validated = validate_phase2_configurations(valid_cfg)
    assert validated["silence_config"]["stage1_seconds"] == 3.0
    assert validated["interruption_config"]["min_words"] == 3


def test_gap4_shorthand_top_level_keys_normalized():
    """Verify top-level shorthand settings are normalized into structured configs."""
    shorthand_cfg = {
        "silence_stage1_seconds": 5.0,
        "silence_stage2_seconds": 10.0,
        "silence_stage3_seconds": 20.0,
        "min_interruption_duration": 0.6,
        "min_interruption_words": 3,
        "backchannel_filter_enabled": False,
    }
    validated = validate_phase2_configurations(shorthand_cfg)
    assert validated["silence_config"]["stage1_seconds"] == 5.0
    assert validated["silence_config"]["stage2_seconds"] == 10.0
    assert validated["silence_config"]["stage3_seconds"] == 20.0
    assert validated["interruption_config"]["min_duration_seconds"] == 0.6
    assert validated["interruption_config"]["backchannel_filter_enabled"] is False


def test_gap4_invalid_silence_stages_ordering_rejected():
    """Verify that inverted or non-increasing silence stages raise HTTP 400."""
    # stage1 >= stage2 is invalid!
    invalid_cfg = {
        "silence_config": {
            "stage1_seconds": 10.0,
            "stage2_seconds": 5.0,
            "stage3_seconds": 14.0,
        }
    }
    with pytest.raises(HTTPException) as exc_info:
        validate_phase2_configurations(invalid_cfg)
    assert exc_info.value.status_code == 400
    assert "strictly increasing" in exc_info.value.detail


def test_gap4_out_of_range_interruption_values_rejected():
    """Verify out-of-range interruption parameters raise HTTP 400."""
    invalid_dur = {
        "interruption_config": {
            "min_duration_seconds": 0.01,  # Too small (< 0.1)
        }
    }
    with pytest.raises(HTTPException) as exc_info:
        validate_phase2_configurations(invalid_dur)
    assert exc_info.value.status_code == 400
    assert "min_duration_seconds" in exc_info.value.detail

    invalid_words = {
        "interruption_config": {
            "min_words": 0,  # Invalid (< 1)
        }
    }
    with pytest.raises(HTTPException) as exc_info:
        validate_phase2_configurations(invalid_words)
    assert exc_info.value.status_code == 400
    assert "min_words" in exc_info.value.detail

