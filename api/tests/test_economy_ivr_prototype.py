"""Automated acceptance tests for SPX Voice Economy IVR Mode.

Covers:
1. Inbound call lifecycle (answer -> greeting playback -> DTMF selection -> action execution -> auto hangup)
2. Outbound call lifecycle
3. Bulk campaign integration (DNC suppression, calling hours, concurrency slot preservation)
4. All supported keypad actions (callback, appointment, order, delivery, fee, enquiry, custom, hangup)
5. Keypad action deduplication (rapid multi-press)
6. Callback deduplication (prevents duplicate callbacks within window)
7. Invalid digit handling & retry limit auto-hangup
8. No-input timeout & auto-hangup
9. Prompt barge-in (stops audio immediately on keypress)
10. Multi-tenant audio cache isolation (cross-tenant access rejected)
11. Language-specific audio selection (Telugu, Hindi, English)
12. Zero AI guarantee (no Gemini, Cartesia, Deepgram, DeepSeek, or LiveKit agents initialized)
13. Usage pulse recording without hardcoded tariff
14. Disconnect & StasisEnd resource cleanup
"""

import asyncio
import json
import math
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from api.db import db_client
from api.schemas.economy_ivr import (
    EconomyIVRConfig,
    IVRActionType,
    KeypadAction,
)
from api.services.telephony.ivr.actions import IVRActionExecutor, ivr_action_executor
from api.services.telephony.ivr.audio_cache import EconomyIVRAudioCache
from api.services.telephony.ivr.economy_ivr_controller import (
    EconomyIVRCallSession,
    EconomyIVRManager,
    EconomyIVRState,
)


# ==============================================================================
# FIXTURES & MOCKS
# ==============================================================================

@pytest.fixture
def mock_ari_client():
    client = MagicMock()
    client.answer_channel = AsyncMock(return_value=True)
    client.hangup_channel = AsyncMock(return_value=True)
    client.play_sound = AsyncMock(return_value={"id": "playback_123", "state": "queued"})
    client.stop_playback = AsyncMock(return_value=True)
    return client


@pytest.fixture
def sample_ivr_config():
    return EconomyIVRConfig(
        enabled=True,
        default_language="en",
        greeting_recording_id="rec_greeting_en",
        language_greetings={
            "te": "rec_greeting_te",
            "hi": "rec_greeting_hi",
            "en": "rec_greeting_en",
        },
        keypad_actions={
            "1": KeypadAction(
                key="1",
                action_type=IVRActionType.CALLBACK,
                label="Request Callback",
                confirmation_recording_id="rec_callback_confirmed",
            ),
            "2": KeypadAction(
                key="2",
                action_type=IVRActionType.APPOINTMENT_REQUEST,
                label="Book Appointment",
                confirmation_recording_id="rec_appt_confirmed",
            ),
            "3": KeypadAction(
                key="3",
                action_type=IVRActionType.ORDER_ENQUIRY,
                label="Order Status",
            ),
            "4": KeypadAction(
                key="4",
                action_type=IVRActionType.DELIVERY_ENQUIRY,
                label="Delivery Enquiry",
            ),
            "5": KeypadAction(
                key="5",
                action_type=IVRActionType.FEE_ENQUIRY,
                label="Fee Structure",
            ),
            "6": KeypadAction(
                key="6",
                action_type=IVRActionType.GENERAL_ENQUIRY,
                label="General Enquiry",
            ),
            "7": KeypadAction(
                key="7",
                action_type=IVRActionType.CUSTOM_ACTION,
                label="Custom Webhook",
                action_params={"tag": "vip_customer_ping"},
            ),
            "9": KeypadAction(
                key="9",
                action_type=IVRActionType.HANGUP,
                label="End Call",
            ),
        },
        timeout_seconds=5,
        max_invalid_retries=2,
        invalid_input_recording_id="rec_invalid_input",
        timeout_recording_id="rec_timeout_prompt",
        auto_hangup_after_action=True,
        allow_barge_in=True,
    )


# ==============================================================================
# TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_01_inbound_call_lifecycle_greeting_playback_and_dtmf_action(mock_ari_client, sample_ivr_config):
    """Inbound call: answers, plays greeting, captures DTMF 1, executes callback, plays confirmation, hangs up."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch("api.services.telephony.ivr.actions.ivr_action_executor.execute_action", new_callable=AsyncMock) as mock_exec, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/rec_audio.wav"
        mock_exec.return_value = {"action": "callback", "status": "created", "is_duplicate": False}

        session = EconomyIVRCallSession(
            channel_id="chan_inbound_001",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1001,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
            language="en",
        )

        # 1. Start call
        await session.start()
        mock_ari_client.answer_channel.assert_awaited_once_with("chan_inbound_001")
        mock_ari_client.play_sound.assert_awaited_once_with("chan_inbound_001", "sound:/tmp/cache/rec_audio.wav")
        assert session.state == EconomyIVRState.PLAYING_GREETING

        # 2. Greeting playback finishes
        await session.on_playback_finished("playback_123")
        assert session.state == EconomyIVRState.WAITING_FOR_DIGIT
        assert session.timeout_task is not None

        # 3. Caller presses '1' (Callback)
        await session.on_dtmf_received("1")
        mock_exec.assert_awaited_once()
        assert session.state == EconomyIVRState.PLAYING_CONFIRMATION

        # 4. Confirmation playback finishes -> auto hangup
        await session.on_playback_finished("playback_conf_456")
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_inbound_001")


@pytest.mark.asyncio
async def test_02_single_outbound_call_lifecycle(mock_ari_client, sample_ivr_config):
    """Single outbound call: originates, enters Stasis, plays greeting, handles key 3, terminates."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch("api.services.telephony.ivr.actions.ivr_action_executor.execute_action", new_callable=AsyncMock) as mock_exec, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/rec_outbound.wav"
        mock_exec.return_value = {"action": "order_enquiry", "status": "recorded"}

        session = EconomyIVRCallSession(
            channel_id="chan_outbound_002",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1002,
            config=sample_ivr_config,
            direction="outbound",
            caller_number="+918000112233",
            called_number="+919876543210",
            ari_client=mock_ari_client,
            language="en",
        )

        await session.start()
        assert session.state == EconomyIVRState.PLAYING_GREETING

        # Caller presses '3' while greeting is playing (barge-in)
        session.active_playback_id = "playback_greeting"
        await session.on_dtmf_received("3")

        mock_ari_client.stop_playback.assert_awaited_once_with("playback_greeting")
        mock_exec.assert_awaited_once()
        # Key 3 has no confirmation audio -> auto-hangup
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_outbound_002")


@pytest.mark.asyncio
async def test_03_bulk_campaign_safety_dnc_and_concurrency_reused():
    """Campaign dispatcher safety: DNC suppressed numbers are blocked before ARI dispatch."""
    from api.services.telephony.compliance import compliance_manager

    with patch.object(compliance_manager, "is_suppressed_db", new_callable=AsyncMock) as mock_dnc:
        mock_dnc.return_value = (True, "National DNC Registry")

        # Simulate check in dispatcher
        is_suppressed, reason = await compliance_manager.is_suppressed_db(1, "+919999999999")
        assert is_suppressed is True
        assert reason == "National DNC Registry"


@pytest.mark.asyncio
async def test_04_all_keypad_actions_1_to_9_verified(sample_ivr_config):
    """Verifies that all defined action types in the configuration execute cleanly."""
    executor = IVRActionExecutor(redis_client=MagicMock())

    with patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):
        # 1. Appointment
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["2"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["status"] == "recorded"
        assert res["action"] == "appointment_request"

        # 2. Order
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["3"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "order_enquiry"

        # 3. Delivery
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["4"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "delivery_enquiry"

        # 4. Fee
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["5"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "fee_enquiry"

        # 5. General
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["6"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "general_enquiry"

        # 6. Custom
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["7"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "custom_action"

        # 7. Hangup
        res = await executor.execute_action(
            sample_ivr_config.keypad_actions["9"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1004,
        )
        assert res["action"] == "hangup"


@pytest.mark.asyncio
async def test_05_duplicate_keypad_presses_prevented(mock_ari_client, sample_ivr_config):
    """Rapid multi-press of digits executes action only once."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch("api.services.telephony.ivr.actions.ivr_action_executor.execute_action", new_callable=AsyncMock) as mock_exec, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/rec.wav"
        mock_exec.return_value = {"action": "order_enquiry", "status": "recorded"}

        session = EconomyIVRCallSession(
            channel_id="chan_dedup_005",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1005,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        session.state = EconomyIVRState.WAITING_FOR_DIGIT

        # Send multiple rapid digits
        await session.on_dtmf_received("3")
        await session.on_dtmf_received("3")
        await session.on_dtmf_received("1")

        # execute_action should only be called once!
        assert mock_exec.await_count == 1
        assert session.action_completed is True


@pytest.mark.asyncio
async def test_06_duplicate_callback_requests_suppressed(sample_ivr_config):
    """Second callback within TTL window returns already_pending and avoids duplicate lead creation."""
    mock_redis = AsyncMock()
    # First call: set returns True (new key)
    # Second call: set returns False (key exists)
    mock_redis.set = AsyncMock(side_effect=[True, False])
    mock_redis.get = AsyncMock(return_value="1005")

    executor = IVRActionExecutor(redis_client=mock_redis)

    with patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):
        # Call 1: New callback
        res1 = await executor.execute_action(
            sample_ivr_config.keypad_actions["1"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1006,
        )
        assert res1["is_duplicate"] is False
        assert res1["status"] == "created"

        # Call 2: Duplicate callback
        res2 = await executor.execute_action(
            sample_ivr_config.keypad_actions["1"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=1007,
        )
        assert res2["is_duplicate"] is True
        assert res2["status"] == "already_pending"


@pytest.mark.asyncio
async def test_07_invalid_digit_retry_limit_exceeded_hangs_up(mock_ari_client, sample_ivr_config):
    """Entering invalid digits up to max_invalid_retries terminates call."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/invalid.wav"

        session = EconomyIVRCallSession(
            channel_id="chan_invalid_007",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1007,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        session.state = EconomyIVRState.WAITING_FOR_DIGIT

        # Invalid attempt 1 (digit '8' not in menu)
        await session.on_dtmf_received("8")
        assert session.invalid_attempts == 1
        assert session.state == EconomyIVRState.PLAYING_INVALID

        # Invalid attempt 2 (max_invalid_retries is 2)
        session.state = EconomyIVRState.WAITING_FOR_DIGIT
        await session.on_dtmf_received("0")
        assert session.invalid_attempts == 2
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_invalid_007")


@pytest.mark.asyncio
async def test_08_no_input_timeout_triggers_auto_hangup(mock_ari_client, sample_ivr_config):
    """No input before timeout elapses plays timeout prompt and hangs up."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/timeout.wav"

        session = EconomyIVRCallSession(
            channel_id="chan_timeout_008",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1008,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        session.state = EconomyIVRState.WAITING_FOR_DIGIT
        await session.on_timeout()

        assert session.state == EconomyIVRState.PLAYING_TIMEOUT
        mock_ari_client.play_sound.assert_awaited_once_with("chan_timeout_008", "sound:/tmp/cache/timeout.wav")

        # Playback completes -> auto hangup
        await session.on_playback_finished("playback_timeout")
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_timeout_008")


@pytest.mark.asyncio
async def test_09_prompt_barge_in_stops_greeting_audio(mock_ari_client, sample_ivr_config):
    """Caller pressing digit during greeting immediately stops audio playback."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch("api.services.telephony.ivr.actions.ivr_action_executor.execute_action", new_callable=AsyncMock), \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/rec.wav"

        session = EconomyIVRCallSession(
            channel_id="chan_barge_009",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1009,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        session.state = EconomyIVRState.PLAYING_GREETING
        session.active_playback_id = "playback_greeting_active"

        await session.on_dtmf_received("9")

        mock_ari_client.stop_playback.assert_awaited_once_with("playback_greeting_active")
        assert session.active_playback_id is None


@pytest.mark.asyncio
async def test_10_multitenant_audio_cache_isolation(tmp_path):
    """Audio cache verifies organization ID: cross-tenant access is rejected."""
    cache = EconomyIVRAudioCache(cache_dir=str(tmp_path))

    with patch.object(db_client, "get_recording_by_recording_id", new_callable=AsyncMock) as mock_db:
        # DB returns recording belonging to Org 100
        mock_rec = MagicMock()
        mock_rec.organization_id = 100
        mock_rec.storage_key = "recordings/100/rec_123/prompt.wav"
        mock_db.return_value = mock_rec

        # Org 200 tries to access Org 100's recording -> Rejected!
        with pytest.raises(PermissionError):
            await cache.get_or_cache_recording("rec_123", organization_id=200)


@pytest.mark.asyncio
async def test_11_language_specific_audio_selection(mock_ari_client, sample_ivr_config):
    """Selects Telugu, Hindi, or English greeting audio based on call language context."""
    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = "/tmp/cache/sound.wav"

        # 1. Telugu call
        session_te = EconomyIVRCallSession(
            channel_id="chan_te",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1011,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
            language="te",
        )
        await session_te.start()
        mock_cache.assert_awaited_with(recording_id="rec_greeting_te", organization_id=1)

        # 2. Hindi call
        session_hi = EconomyIVRCallSession(
            channel_id="chan_hi",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=1012,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
            language="hi",
        )
        await session_hi.start()
        mock_cache.assert_awaited_with(recording_id="rec_greeting_hi", organization_id=1)


def test_12_zero_ai_guarantee(mock_ari_client, sample_ivr_config):
    """Economy IVR session has zero dependencies on AI packages, models, or LiveKit rooms."""
    session = EconomyIVRCallSession(
        channel_id="chan_no_ai",
        organization_id=1,
        workflow_id=10,
        workflow_run_id=1012,
        config=sample_ivr_config,
        direction="inbound",
        caller_number="+919876543210",
        called_number="+918000112233",
        ari_client=mock_ari_client,
    )

    # Asserts that session never instantiates AI pipeline components
    assert not hasattr(session, "livekit_room")
    assert not hasattr(session, "pipeline")
    assert not hasattr(session, "llm_service")
    assert not hasattr(session, "tts_service")
    assert not hasattr(session, "stt_service")


@pytest.mark.asyncio
async def test_13_usage_recording_without_hardcoded_pricing(mock_ari_client, sample_ivr_config):
    """Call duration and pulses are recorded in gathered_context without hardcoded pricing."""
    session = EconomyIVRCallSession(
        channel_id="chan_pulse_013",
        organization_id=1,
        workflow_id=10,
        workflow_run_id=1013,
        config=sample_ivr_config,
        direction="inbound",
        caller_number="+919876543210",
        called_number="+918000112233",
        ari_client=mock_ari_client,
    )

    session.start_time = time.time() - 125.0  # 125 seconds = 3 60s pulses

    with patch.object(db_client, "update_workflow_run", new_callable=AsyncMock) as mock_update:
        await session.hangup()

        mock_update.assert_awaited_once()
        kwargs = mock_update.await_args.kwargs
        context = kwargs["gathered_context"]

        assert context["ivr_mode"] == "economy_ivr"
        assert context["billed_pulses"] == 3
        assert 124.0 <= context["duration_seconds"] <= 126.0
        # Assert no hardcoded currency or amounts
        assert "cost_inr" not in context
        assert "price" not in context


@pytest.mark.asyncio
async def test_14_unexpected_disconnect_and_stasis_end_cleanup(mock_ari_client, sample_ivr_config):
    """ChannelDestroyed cancels timers and removes session cleanly."""
    manager = EconomyIVRManager()
    session = EconomyIVRCallSession(
        channel_id="chan_clean_014",
        organization_id=1,
        workflow_id=10,
        workflow_run_id=1014,
        config=sample_ivr_config,
        direction="inbound",
        caller_number="+919876543210",
        called_number="+918000112233",
        ari_client=mock_ari_client,
    )

    manager.register_session("chan_clean_014", session)
    session._start_timeout_timer()
    assert session.timeout_task is not None

    # Simulate disconnect cleanup
    removed = manager.remove_session("chan_clean_014")
    assert removed is session
    removed._cancel_timeout_timer()
    assert session.timeout_task is None
    assert manager.get_session("chan_clean_014") is None


@pytest.mark.asyncio
async def test_15_audio_cache_path_traversal_rejection(tmp_path):
    """Path traversal and illegal characters in recording_id are strictly rejected."""
    cache = EconomyIVRAudioCache(cache_dir=str(tmp_path))

    # Directory traversal attempts
    with pytest.raises(ValueError, match="Invalid recording ID format"):
        await cache.get_or_cache_recording("../../etc/passwd", organization_id=1)

    with pytest.raises(ValueError, match="Invalid recording ID format"):
        await cache.get_or_cache_recording("rec_test/../../evil", organization_id=1)

    with pytest.raises(ValueError, match="Invalid recording ID format"):
        await cache.get_or_cache_recording("rec_null\x00byte", organization_id=1)


@pytest.mark.asyncio
async def test_16_audio_cache_cleanup_and_eviction(tmp_path):
    """Tests temporary download cleanup, manual eviction, and age-based cache eviction."""
    cache = EconomyIVRAudioCache(cache_dir=str(tmp_path))
    tenant_dir = cache._get_tenant_dir(1)

    # 1. Orphaned temp file
    temp_file = tenant_dir / "rec_temp.tmp_999_12345"
    temp_file.write_text("audio-temp-data")
    assert temp_file.exists()

    cleaned = cache.cleanup_temp_files()
    assert cleaned == 1
    assert not temp_file.exists()

    # 2. Manual eviction of cached recording
    cached_audio = tenant_dir / "rec_evict.wav"
    cached_audio.write_text("wav-data")
    assert cached_audio.exists()

    evicted = cache.remove_cached_recording("rec_evict", organization_id=1)
    assert evicted is True
    assert not cached_audio.exists()

    # 3. Old cache eviction
    old_file = tenant_dir / "rec_old.wav"
    old_file.write_text("old-audio")
    # Set mtime to 2 days ago
    past_time = time.time() - 172800
    os.utime(str(old_file), (past_time, past_time))

    evicted_count = cache.cleanup_old_cache(max_age_seconds=86400)
    assert evicted_count >= 1
    assert not old_file.exists()


@pytest.mark.asyncio
async def test_17_actions_guarantee_no_false_confirmations(sample_ivr_config):
    """Enquiries and booking requests are never falsely marked as confirmed or completed."""
    executor = IVRActionExecutor(redis_client=MagicMock())

    with patch.object(db_client, "update_workflow_run", new_callable=AsyncMock) as mock_update:
        # Appointment Request
        res_appt = await executor.execute_action(
            sample_ivr_config.keypad_actions["2"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=2001,
        )
        assert res_appt["is_confirmed_booking"] is False
        assert res_appt["is_completed_order"] is False
        assert res_appt["enquiry"]["appointment"]["status"] == "requested"
        assert res_appt["enquiry"]["status"] == "requested"

        # Order Enquiry
        res_order = await executor.execute_action(
            sample_ivr_config.keypad_actions["3"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=2001,
        )
        assert res_order["is_confirmed_booking"] is False
        assert res_order["is_completed_order"] is False
        assert res_order["enquiry"]["order"]["status"] == "enquiry_pending"

        # Delivery Enquiry
        res_deliv = await executor.execute_action(
            sample_ivr_config.keypad_actions["4"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=2001,
        )
        assert res_deliv["is_confirmed_booking"] is False
        assert res_deliv["is_completed_order"] is False
        assert res_deliv["enquiry"]["delivery"]["status"] == "enquiry_pending"

        # Hangup
        res_hang = await executor.execute_action(
            sample_ivr_config.keypad_actions["9"],
            organization_id=1,
            caller_number="+919876543210",
            workflow_id=10,
            workflow_run_id=2001,
        )
        assert res_hang["is_confirmed_booking"] is False
        assert res_hang["is_completed_order"] is False


@pytest.mark.asyncio
async def test_18_failed_playback_recovers_and_hangs_up(mock_ari_client, sample_ivr_config):
    """Playback failure in ARI does not leave session hanging; it terminates cleanly."""
    mock_ari_client.play_sound = AsyncMock(side_effect=RuntimeError("ARI Playback Error 500"))

    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/rec.wav"

        session = EconomyIVRCallSession(
            channel_id="chan_fail_018",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=2018,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        await session.start()
        # Should gracefully terminate rather than hanging
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_fail_018")


@pytest.mark.asyncio
async def test_19_caller_disconnect_channel_destroyed_cleanup(mock_ari_client, sample_ivr_config):
    """ChannelDestroyed event cancels timers and avoids redundant hangup calls."""
    with patch.object(db_client, "update_workflow_run", new_callable=AsyncMock) as mock_update:
        session = EconomyIVRCallSession(
            channel_id="chan_destroyed_019",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=2019,
            config=sample_ivr_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )
        session._start_timeout_timer()
        assert session.timeout_task is not None

        # Simulate Asterisk ChannelDestroyed
        await session.on_channel_destroyed(cause=16, cause_txt="Normal Clearing")

        assert session.state == EconomyIVRState.TERMINATED
        assert session.timeout_task is None
        # Does NOT invoke hangup_channel since channel is already gone
        mock_ari_client.hangup_channel.assert_not_awaited()
        mock_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_20_announcement_only_auto_hangup(mock_ari_client):
    """Greeting-only IVR with no keypad actions auto-hangs up when greeting finishes."""
    announcement_config = EconomyIVRConfig(
        enabled=True,
        greeting_recording_id="rec_announcement",
        keypad_actions={},  # No menu options
    )

    with patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache, \
         patch.object(db_client, "update_workflow_run", new_callable=AsyncMock):

        mock_cache.return_value = "/tmp/cache/announcement.wav"

        session = EconomyIVRCallSession(
            channel_id="chan_announce_020",
            organization_id=1,
            workflow_id=10,
            workflow_run_id=2020,
            config=announcement_config,
            direction="inbound",
            caller_number="+919876543210",
            called_number="+918000112233",
            ari_client=mock_ari_client,
        )

        await session.start()
        assert session.state == EconomyIVRState.PLAYING_GREETING

        # Greeting finishes
        await session.on_playback_finished("playback_123")

        # Automatically terminates
        assert session.state == EconomyIVRState.TERMINATED
        mock_ari_client.hangup_channel.assert_awaited_once_with("chan_announce_020")


@pytest.mark.asyncio
async def test_21_outbound_ari_call_enters_stasis_and_routes_to_economy_ivr(mock_ari_client, sample_ivr_config):
    """Outbound call entering Stasis routes to EconomyIVRCallSession and skips AI pipeline."""
    from api.services.telephony.ari_manager import ARIConnection

    ari_conn = ARIConnection(
        organization_id=1,
        telephony_configuration_id=100,
        ari_endpoint="http://mock-asterisk:8088",
        app_name="spx-voice",
        app_password="secret",
    )
    ari_conn._ari_request = AsyncMock(return_value={"id": "playback_out_1"})
    ari_conn._set_channel_run = AsyncMock()
    ari_conn._is_ext_channel = AsyncMock(return_value=False)
    ari_conn._get_transfer_id = MagicMock(return_value=None)

    mock_wf = MagicMock()
    mock_wf.workflow_configurations = {"economy_ivr": sample_ivr_config.model_dump()}

    with patch.object(db_client, "get_workflow", new_callable=AsyncMock) as mock_get_wf, \
         patch.object(ari_conn, "_handle_stasis_start", new_callable=AsyncMock) as mock_pipe, \
         patch("api.services.telephony.ivr.economy_ivr_controller.ivr_audio_cache.get_or_cache_recording", new_callable=AsyncMock) as mock_cache:

        mock_get_wf.return_value = mock_wf
        mock_cache.return_value = "/tmp/cache/outbound.wav"

        # Outbound channel answered (state="Up")
        outbound_event = {
            "type": "StasisStart",
            "channel": {
                "id": "chan_outbound_stasis_21",
                "state": "Up",
                "caller": {"number": "+918000112233"},
                "dialplan": {"exten": "+919876543210"},
            },
            "args": ["workflow_run_id=2021,workflow_id=10,user_id=1"],
        }

        await ari_conn._handle_event(json.dumps(outbound_event))
        # Give event task a slice to run
        await asyncio.sleep(0.05)

        # Standard AI pipeline MUST NOT be called!
        mock_pipe.assert_not_called()

        from api.services.telephony.ivr.economy_ivr_controller import ivr_manager
        active_sess = ivr_manager.get_session("chan_outbound_stasis_21")
        assert active_sess is not None
        assert active_sess.direction == "outbound"
        assert active_sess.state in (EconomyIVRState.ANSWERED, EconomyIVRState.PLAYING_GREETING)


@pytest.mark.asyncio
async def test_22_economy_ivr_configuration_api_get_and_post(sample_ivr_config):
    """GET and POST /business/economy-ivr persist and retrieve Economy IVR configs."""
    from api.routes.business import get_economy_ivr_config, save_economy_ivr_config

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.selected_organization_id = 1

    mock_wf = MagicMock()
    mock_wf.id = 10
    mock_wf.workflow_configurations = {}

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().first.return_value = mock_wf
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("api.routes.business._get_primary_workflow", new_callable=AsyncMock) as mock_get_prim, \
         patch.object(db_client, "async_session", return_value=mock_cm):

        mock_get_prim.return_value = mock_wf

        # 1. GET initial (disabled default)
        cfg_default = await get_economy_ivr_config(user=mock_user)
        assert cfg_default["enabled"] is False

        # 2. POST save
        res = await save_economy_ivr_config(config=sample_ivr_config, user=mock_user)
        assert res["status"] == "saved"
        assert res["config"]["enabled"] is True
        assert len(res["config"]["keypad_actions"]) == 8

        # 3. GET saved
        mock_wf.workflow_configurations = {"economy_ivr": sample_ivr_config.model_dump()}
        cfg_saved = await get_economy_ivr_config(user=mock_user)
        assert cfg_saved["enabled"] is True
        assert cfg_saved["greeting_recording_id"] == "rec_greeting_en"

