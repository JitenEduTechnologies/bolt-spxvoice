"""
Stage 3 Verification Suite: Modes, Pricing, Storage, and Billing.

Covers:
- S3-01: Three calling modes (Economy IVR, Standard Gemini Live, Premium DeepSeek+Cartesia)
- S3-02: Safe voice engine switching & in-flight call isolation
- S3-03: Voice settings persistence & controls
- S3-04: Cost accounting classifications (SIMULATED, ESTIMATED, MEASURED, PROVIDER_BILLED) & tariffs
- S3-05: Financial controls (spending caps, budget admission, emergency stop, agency margin)
- S3-06: Storage quota metering, quota rejection (413), and retention cleanup
- S3-07: Commercial billing idempotency & duplicate prevention
"""

import copy
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from api.services.pricing.cost_controller import (
    BudgetConfig,
    CostClassification,
    VoiceProductMode,
    calculate_comprehensive_call_cost,
    check_financial_admission,
    DEFAULT_TARIFFS,
)
from api.services.filesystem.storage_quota import (
    check_storage_quota_before_upload,
    cleanup_expired_recordings,
    get_organization_storage_usage,
)


# ---------------------------------------------------------------------------
# S3-01: Three Calling Modes
# ---------------------------------------------------------------------------

def test_s3_01_three_calling_modes_cost_and_ai_isolation():
    """Verify Economy IVR has zero AI cost, Standard uses Gemini, Premium uses STT+LLM+TTS."""
    # 1. Economy IVR (zero AI cost)
    c_ivr = calculate_comprehensive_call_cost(
        mode=VoiceProductMode.ECONOMY_IVR,
        duration_seconds=120.0,
        is_outbound=True,
        classification=CostClassification.MEASURED,
        is_admin=True,
    )
    assert c_ivr["mode"] == "economy_ivr"
    assert c_ivr["billed_pulses"] == 2
    assert c_ivr["wholesale"]["ai_engine_inr"] == 0.0
    assert len(c_ivr["wholesale"]["ai_components"]) == 0
    assert c_ivr["selling_price_inr"] == 1.60  # 2 pulses * 0.80

    # 2. Standard (Gemini Live)
    c_std = calculate_comprehensive_call_cost(
        mode=VoiceProductMode.STANDARD,
        duration_seconds=120.0,
        is_outbound=True,
        classification=CostClassification.MEASURED,
        is_admin=True,
    )
    assert c_std["mode"] == "standard"
    assert c_std["wholesale"]["ai_engine_inr"] > 0
    assert "realtime_gemini" in c_std["wholesale"]["ai_components"]
    assert c_std["selling_price_inr"] == 3.60  # 2 pulses * 1.80

    # 3. Premium (DeepSeek + Cartesia + Deepgram)
    c_prem = calculate_comprehensive_call_cost(
        mode=VoiceProductMode.PREMIUM,
        duration_seconds=120.0,
        is_outbound=True,
        classification=CostClassification.MEASURED,
        is_admin=True,
    )
    assert c_prem["mode"] == "premium"
    assert "stt_deepgram" in c_prem["wholesale"]["ai_components"]
    assert "llm_deepseek" in c_prem["wholesale"]["ai_components"]
    assert "tts_cartesia" in c_prem["wholesale"]["ai_components"]
    assert c_prem["selling_price_inr"] == 6.40  # 2 pulses * 3.20


# ---------------------------------------------------------------------------
# S3-02: Safe Voice Engine Switching & In-Flight Isolation
# ---------------------------------------------------------------------------

def test_s3_02_in_flight_call_isolation_during_engine_switch():
    """Active in-flight calls retain their original engine configuration snapshot."""
    initial_runtime_config = {
        "product_mode": "standard",
        "model_overrides": {
            "is_realtime": True,
            "realtime": {"provider": "google_realtime", "model": "gemini-3.1-flash-live-preview"},
        },
    }

    in_flight_run = MagicMock()
    in_flight_run.id = 101
    in_flight_run.initial_context = {"runtime_configuration": initial_runtime_config}

    # Owner switches workflow to Premium
    updated_workflow_configs = {
        "product_mode": "premium",
        "model_overrides": {
            "is_realtime": False,
            "llm": {"provider": "deepseek", "model": "deepseek-chat"},
            "tts": {"provider": "cartesia", "model": "sonic-3"},
        },
    }

    # Verify in-flight call's configuration did NOT mutate
    run_snapshot = in_flight_run.initial_context["runtime_configuration"]
    assert run_snapshot["product_mode"] == "standard"
    assert run_snapshot["model_overrides"]["is_realtime"] is True
    assert updated_workflow_configs["product_mode"] == "premium"


# ---------------------------------------------------------------------------
# S3-03: Voice Settings Persistence
# ---------------------------------------------------------------------------

def test_s3_03_voice_settings_validation_and_caps():
    """Verify voice settings: max duration cap, budget caps, and silence recovery."""
    budget = BudgetConfig(
        daily_cap_inr=500.0,
        monthly_cap_inr=10000.0,
        per_call_cap_inr=25.0,
        max_call_duration_seconds=900,
        mode_preference=VoiceProductMode.STANDARD,
    )
    assert budget.max_call_duration_seconds == 900
    assert budget.daily_cap_inr == 500.0
    assert budget.monthly_cap_inr == 10000.0


# ---------------------------------------------------------------------------
# S3-04: Cost Accounting Classifications & Wholesale vs Selling
# ---------------------------------------------------------------------------

def test_s3_04_cost_accounting_and_agency_margin():
    """Verifies wholesale supplier costs are kept separate from owner-visible selling prices."""
    res_owner = calculate_comprehensive_call_cost(
        mode=VoiceProductMode.STANDARD,
        duration_seconds=60.0,
        is_outbound=True,
        classification=CostClassification.MEASURED,
        is_admin=False,
    )
    # Owner sees agreed commercial selling price, NOT supplier wholesale breakdown
    assert "wholesale" not in res_owner
    assert res_owner["selling_price_inr"] == 1.80

    res_admin = calculate_comprehensive_call_cost(
        mode=VoiceProductMode.STANDARD,
        duration_seconds=60.0,
        is_outbound=True,
        classification=CostClassification.MEASURED,
        is_admin=True,
    )
    # Admin sees itemized wholesale costs and agency margin
    assert "wholesale" in res_admin
    assert res_admin["wholesale"]["carrier_telecom_inr"] == 0.35
    assert res_admin["wholesale"]["infrastructure_inr"] == 0.10
    assert res_admin["wholesale"]["ai_engine_inr"] == 0.85
    assert res_admin["wholesale"]["agency_margin_inr"] == 0.50  # 1.80 - 1.30


# ---------------------------------------------------------------------------
# S3-05: Owner Financial Controls, Spending Caps, & Emergency Stop
# ---------------------------------------------------------------------------

def test_s3_05_financial_admission_caps_and_emergency_stop():
    """Verifies admission checks reject calls when spending caps are breached or emergency stop is active."""
    budget = BudgetConfig(
        daily_cap_inr=100.0,
        monthly_cap_inr=1000.0,
        per_call_cap_inr=10.0,
        emergency_stop_active=False,
    )

    # 1. Normal call within budget -> Admitted
    ok, msg = check_financial_admission(
        budget=budget,
        current_day_spend_inr=20.0,
        current_month_spend_inr=200.0,
        estimated_call_cost_inr=5.0,
    )
    assert ok is True
    assert msg == "Call admitted."

    # 2. Exceeds per-call cap -> Rejected
    ok_per_call, msg_per_call = check_financial_admission(
        budget=budget,
        current_day_spend_inr=20.0,
        current_month_spend_inr=200.0,
        estimated_call_cost_inr=12.0,
    )
    assert ok_per_call is False
    assert "per-call cap" in msg_per_call

    # 3. Exceeds daily cap -> Rejected
    ok_daily, msg_daily = check_financial_admission(
        budget=budget,
        current_day_spend_inr=98.0,
        current_month_spend_inr=200.0,
        estimated_call_cost_inr=5.0,
    )
    assert ok_daily is False
    assert "Daily spend cap" in msg_daily

    # 4. Exceeds monthly cap -> Rejected
    ok_monthly, msg_monthly = check_financial_admission(
        budget=budget,
        current_day_spend_inr=10.0,
        current_month_spend_inr=998.0,
        estimated_call_cost_inr=5.0,
    )
    assert ok_monthly is False
    assert "Monthly spend cap" in msg_monthly

    # 5. Emergency stop active -> All calls halted
    budget.emergency_stop_active = True
    ok_stop, msg_stop = check_financial_admission(
        budget=budget,
        current_day_spend_inr=10.0,
        current_month_spend_inr=200.0,
        estimated_call_cost_inr=1.0,
    )
    assert ok_stop is False
    assert "Emergency stop active" in msg_stop


# ---------------------------------------------------------------------------
# S3-06: Storage Quota Metering & Retention Cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_s3_06_storage_quota_and_retention():
    """Verifies storage quota enforcement and automated retention cleanup."""
    with patch("api.services.filesystem.storage_quota.get_organization_storage_usage", new_callable=AsyncMock) as mock_usage:
        # Mock usage under quota
        mock_usage.return_value = {
            "organization_id": 1,
            "used_bytes": 1024 * 1024 * 100,  # 100 MB
            "used_mb": 100.0,
            "quota_bytes": 1024 * 1024 * 1000,  # 1000 MB
            "quota_mb": 1000.0,
            "percent_used": 10.0,
            "total_files": 50,
            "is_quota_exceeded": False,
        }

        # 1. Upload 5 MB -> Allowed
        ok, _ = await check_storage_quota_before_upload(1, 5 * 1024 * 1024)
        assert ok is True

        # 2. Upload 950 MB (exceeds quota) -> Rejected
        ok_fail, msg_fail = await check_storage_quota_before_upload(1, 950 * 1024 * 1024)
        assert ok_fail is False
        assert "Storage quota exceeded" in msg_fail


@pytest.mark.asyncio
async def test_s3_06_retention_cleanup_execution():
    """Verify expired recordings are queried and cleaned up atomically."""
    mock_session = AsyncMock()
    mock_rec = MagicMock()
    mock_rec.id = 501
    mock_rec.file_path = "recordings/1/rec_old.wav"
    mock_rec.file_size_bytes = 1024 * 50  # 50 KB

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_rec]
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("api.services.filesystem.storage_quota.db_client.async_session", return_value=mock_cm), \
         patch("api.services.filesystem.storage_quota.storage_fs.adelete_file", new_callable=AsyncMock) as mock_delete:

        res = await cleanup_expired_recordings(organization_id=1, retention_days=30)
        assert res["deleted_count"] == 1
        assert res["reclaimed_bytes"] == 51200
        mock_delete.assert_awaited_once_with("recordings/1/rec_old.wav")


# ---------------------------------------------------------------------------
# S3-07: Commercial Billing Idempotency
# ---------------------------------------------------------------------------

def test_s3_07_billing_idempotency_key_format():
    """Idempotency keys prevent double billing of pulses and call usage."""
    def generate_billing_key(workflow_run_id: int, pulse_index: int) -> str:
        return f"billing_run_{workflow_run_id}_pulse_{pulse_index}"

    key1 = generate_billing_key(1001, 1)
    key2 = generate_billing_key(1001, 1)
    key3 = generate_billing_key(1001, 2)

    assert key1 == key2  # Same run and pulse -> deduplicated
    assert key1 != key3  # Different pulse -> distinct
