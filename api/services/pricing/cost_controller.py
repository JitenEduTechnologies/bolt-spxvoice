"""
Commercial Cost Controller & Financial Protection Engine (Stage 3).

Provides:
- Configurable, effective-dated tariffs for Indian & global voice AI operations
- Calling mode cost models: Economy IVR (zero AI), Standard (Gemini Live), Premium (DeepSeek + Cartesia)
- Four-tier cost classifications: SIMULATED, ESTIMATED, MEASURED, PROVIDER-BILLED
- Separation of wholesale supplier costs from owner-facing selling prices via agency margins
- Financial safety controls: per-call limits, daily/monthly spending caps, budget admission, emergency stop
"""

import math
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from loguru import logger
from pydantic import BaseModel, Field


class CostClassification(str, Enum):
    SIMULATED = "SIMULATED"
    ESTIMATED = "ESTIMATED"
    MEASURED = "MEASURED"
    PROVIDER_BILLED = "PROVIDER_BILLED"


class VoiceProductMode(str, Enum):
    ECONOMY_IVR = "economy_ivr"
    STANDARD = "standard"
    PREMIUM = "premium"


# ---------------------------------------------------------------------------
# Configurable Effective-Dated Tariffs & Wholesale Costs
# ---------------------------------------------------------------------------

DEFAULT_TARIFFS = {
    # Wholesale costs (internal supplier cost)
    "wholesale": {
        "carrier_inbound_pulse_inr": 0.25,
        "carrier_outbound_pulse_inr": 0.35,
        "infra_per_minute_inr": 0.10,
        "gemini_live_per_minute_inr": 0.85,
        "deepgram_stt_per_minute_inr": 0.36,
        "deepseek_llm_per_minute_inr": 0.01,
        "cartesia_tts_per_minute_inr": 0.90,
    },
    # Default agency selling price rates (owner-visible agreed commercial prices)
    "selling": {
        "economy_ivr_rate_per_min_inr": 0.80,
        "standard_rate_per_min_inr": 1.80,
        "premium_rate_per_min_inr": 3.20,
    },
    "effective_date": "2026-11-01T00:00:00Z",
    "agency_margin_multiplier": 1.25,  # 25% agency margin over wholesale
}


class BudgetConfig(BaseModel):
    """Owner financial protection settings."""
    daily_cap_inr: float = Field(default=500.0, description="Daily spend cap in INR")
    monthly_cap_inr: float = Field(default=10000.0, description="Monthly spend cap in INR")
    per_call_cap_inr: float = Field(default=25.0, description="Maximum single-call cost cap in INR")
    max_call_duration_seconds: int = Field(default=900, description="Maximum call length in seconds (15 min)")
    mode_preference: VoiceProductMode = Field(default=VoiceProductMode.STANDARD)
    emergency_stop_active: bool = Field(default=False, description="Emergency kill switch for outbound calls")
    selling_price_multiplier: float = Field(default=1.25, description="Agency markup over supplier wholesale cost")


def calculate_comprehensive_call_cost(
    mode: str | VoiceProductMode,
    duration_seconds: float,
    is_outbound: bool = False,
    classification: CostClassification = CostClassification.MEASURED,
    tariffs: Optional[Dict[str, Any]] = None,
    is_admin: bool = False,
) -> Dict[str, Any]:
    """Calculates call cost itemization, wholesale breakdown, and agreed selling price.
    
    Args:
        mode: Calling mode ('economy_ivr', 'standard', 'premium')
        duration_seconds: Measured or estimated call duration in seconds
        is_outbound: True for outbound calls, False for inbound
        classification: SIMULATED, ESTIMATED, MEASURED, or PROVIDER_BILLED
        tariffs: Optional custom tariffs dictionary
        is_admin: If True, includes confidential supplier wholesale costs in response
    """
    tariffs = tariffs or DEFAULT_TARIFFS
    wholesale_rates = tariffs.get("wholesale", DEFAULT_TARIFFS["wholesale"])
    selling_rates = tariffs.get("selling", DEFAULT_TARIFFS["selling"])
    margin_mult = float(tariffs.get("agency_margin_multiplier", 1.25))

    duration_sec = max(0.0, float(duration_seconds))
    pulses = max(1, math.ceil(duration_sec / 60.0)) if duration_sec > 0 else 0
    minutes = round(duration_sec / 60.0, 3)

    mode_str = str(mode).lower().replace("voiceproductmode.", "")

    # 1. Wholesale Telecom & Infra Cost
    carrier_rate = (
        wholesale_rates["carrier_outbound_pulse_inr"]
        if is_outbound
        else wholesale_rates["carrier_inbound_pulse_inr"]
    )
    carrier_wholesale = round(pulses * carrier_rate, 4)
    infra_wholesale = round(minutes * wholesale_rates["infra_per_minute_inr"], 4)

    # 2. Wholesale AI Cost
    ai_itemized = {}
    if mode_str in ("economy_ivr", "economy", "ivr"):
        # Zero AI for Economy IVR
        ai_wholesale = 0.0
        selling_rate_per_min = selling_rates["economy_ivr_rate_per_min_inr"]
    elif mode_str == "premium":
        stt = round(minutes * wholesale_rates["deepgram_stt_per_minute_inr"], 4)
        llm = round(minutes * wholesale_rates["deepseek_llm_per_minute_inr"], 4)
        tts = round(minutes * wholesale_rates["cartesia_tts_per_minute_inr"], 4)
        ai_itemized = {"stt_deepgram": stt, "llm_deepseek": llm, "tts_cartesia": tts}
        ai_wholesale = round(stt + llm + tts, 4)
        selling_rate_per_min = selling_rates["premium_rate_per_min_inr"]
    else:  # Standard (Gemini Live)
        gemini = round(minutes * wholesale_rates["gemini_live_per_minute_inr"], 4)
        ai_itemized = {"realtime_gemini": gemini}
        ai_wholesale = gemini
        selling_rate_per_min = selling_rates["standard_rate_per_min_inr"]

    total_wholesale = round(carrier_wholesale + infra_wholesale + ai_wholesale, 2)

    # 3. Agreed Selling Price (Owner-Visible)
    selling_price_total = round(pulses * (selling_rate_per_min), 2)
    agency_margin = round(max(0.0, selling_price_total - total_wholesale), 2)

    result = {
        "mode": mode_str,
        "duration_seconds": duration_sec,
        "billed_pulses": pulses,
        "classification": classification.value,
        "selling_price_inr": selling_price_total,
        "rate_per_pulse_inr": selling_rate_per_min,
        "currency": "INR",
        "is_outbound": is_outbound,
        "effective_date": tariffs.get("effective_date", "2026-11-01"),
    }

    if is_admin:
        result["wholesale"] = {
            "total_wholesale_inr": total_wholesale,
            "carrier_telecom_inr": carrier_wholesale,
            "infrastructure_inr": infra_wholesale,
            "ai_engine_inr": ai_wholesale,
            "ai_components": ai_itemized,
            "agency_margin_inr": agency_margin,
            "agency_margin_percentage": round((agency_margin / max(0.01, total_wholesale)) * 100, 1),
        }

    return result


def check_financial_admission(
    budget: BudgetConfig,
    current_day_spend_inr: float,
    current_month_spend_inr: float,
    estimated_call_cost_inr: float,
) -> tuple[bool, str]:
    """Verifies whether a call can be admitted without breaching spending caps."""
    if budget.emergency_stop_active:
        return False, "Emergency stop active: all outbound and campaign calls are halted."

    # Check per-call cap
    if budget.per_call_cap_inr > 0 and estimated_call_cost_inr > budget.per_call_cap_inr:
        return False, f"Call cost (₹{estimated_call_cost_inr:.2f}) exceeds per-call cap (₹{budget.per_call_cap_inr:.2f})."

    # Check daily cap
    if budget.daily_cap_inr > 0:
        if (current_day_spend_inr + estimated_call_cost_inr) > budget.daily_cap_inr:
            return False, f"Daily spend cap (₹{budget.daily_cap_inr:.2f}) would be exceeded."

    # Check monthly cap
    if budget.monthly_cap_inr > 0:
        if (current_month_spend_inr + estimated_call_cost_inr) > budget.monthly_cap_inr:
            return False, f"Monthly spend cap (₹{budget.monthly_cap_inr:.2f}) would be exceeded."

    return True, "Call admitted."

