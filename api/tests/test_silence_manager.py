import asyncio
import pytest
from api.services.livekit.conversation_state import ConversationState
from api.services.livekit.silence_manager import SilenceManager


@pytest.mark.asyncio
async def test_silence_progression_stages():
    spoken = []
    disconnected = []

    async def fake_speak(text: str):
        spoken.append(text)

    async def fake_disconnect(reason: str):
        disconnected.append(reason)

    sm = SilenceManager(
        stage1_seconds=0.05,
        stage2_seconds=0.10,
        stage3_seconds=0.15,
        language="en",
        speak_callback=fake_speak,
        disconnect_callback=fake_disconnect,
    )

    # Immediately: 0 duration
    triggered = await sm.evaluate_silence(ConversationState.LISTENING)
    assert triggered is None
    assert sm.current_stage == 0

    # Wait past Stage 1
    await asyncio.sleep(0.06)
    triggered = await sm.evaluate_silence(ConversationState.LISTENING)
    assert triggered == 1
    assert sm.current_stage == 1
    assert len(spoken) == 1
    assert "Take your time" in spoken[0]

    # Wait past Stage 2
    await asyncio.sleep(0.06)
    triggered = await sm.evaluate_silence(ConversationState.LISTENING)
    assert triggered == 2
    assert sm.current_stage == 2
    assert len(spoken) == 2
    assert "Are you still there?" in spoken[1]

    # Wait past Stage 3
    await asyncio.sleep(0.06)
    triggered = await sm.evaluate_silence(ConversationState.LISTENING)
    assert triggered == 3
    assert sm.current_stage == 3
    assert len(spoken) == 3
    assert "disconnected" in spoken[2]
    assert len(disconnected) == 1
    assert disconnected[0] == "no_response_timeout"
    assert sm.disposition == "no_response"


@pytest.mark.asyncio
async def test_silence_never_triggers_while_customer_speaking_or_thinking():
    spoken = []

    async def fake_speak(text: str):
        spoken.append(text)

    sm = SilenceManager(
        stage1_seconds=0.05,
        stage2_seconds=0.10,
        stage3_seconds=0.15,
        speak_callback=fake_speak,
    )

    await asyncio.sleep(0.07)
    # While customer is speaking: MUST NOT trigger
    res = await sm.evaluate_silence(ConversationState.CUSTOMER_SPEAKING)
    assert res is None
    assert len(spoken) == 0

    # While agent is thinking: MUST NOT trigger
    res = await sm.evaluate_silence(ConversationState.THINKING)
    assert res is None
    assert len(spoken) == 0

    # While agent is speaking: MUST NOT trigger
    res = await sm.evaluate_silence(ConversationState.AGENT_SPEAKING)
    assert res is None
    assert len(spoken) == 0


@pytest.mark.asyncio
async def test_silence_blocked_while_tool_executing():
    spoken = []

    sm = SilenceManager(
        stage1_seconds=0.05,
        speak_callback=lambda text: spoken.append(text),
    )
    sm.set_tool_running(True)

    await asyncio.sleep(0.07)
    res = await sm.evaluate_silence(ConversationState.LISTENING)
    assert res is None
    assert len(spoken) == 0

    # After tool finishes, silence resumes
    sm.set_tool_running(False)
    await asyncio.sleep(0.06)
    res = await sm.evaluate_silence(ConversationState.LISTENING)
    assert res == 1
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_silence_resets_on_customer_speech():
    spoken = []

    sm = SilenceManager(
        stage1_seconds=0.05,
        stage2_seconds=0.10,
        stage3_seconds=0.15,
        speak_callback=lambda t: spoken.append(t),
    )

    await asyncio.sleep(0.06)
    assert await sm.evaluate_silence(ConversationState.LISTENING) == 1
    assert sm.current_stage == 1

    # Customer speaks
    sm.reset_silence("customer_spoke")
    assert sm.current_stage == 0
    assert sm.silence_duration < 0.02


@pytest.mark.asyncio
async def test_localized_prompts_hindi_and_telugu():
    sm_hi = SilenceManager(language="hi")
    assert "sun raha hoon" in sm_hi.prompts[1]
    assert "sampark toot gaya" in sm_hi.prompts[3]

    sm_te = SilenceManager(language="te")
    assert "vintunnanu" in sm_te.prompts[1]
    assert "Selavu" in sm_te.prompts[3]

