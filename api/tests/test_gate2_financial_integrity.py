"""Gate 2 Correction B — Gemini Live financial-integrity regression tests.

Deterministic, offline. Verifies provider/model attribution for LiveKit Gemini
processors, absence of duration-based double-charging, UNPRICED labeling for
unknown models, and explicit stale-FX flagging. No live rates or FX are fetched.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from api.services.configuration.registry import ServiceProviders
from api.services.pricing import cost_calculator as cc_mod
from api.services.pricing.cost_calculator import cost_calculator
from api.services.pricing.registry import PRICING_REGISTRY
from api.services.pricing.models import RealtimeAudioTextPricingModel

RUN345_USAGE = {
    "llm": {
        "LiveKitGeminiLLMService#0|||gemini-3.1-flash-live-preview": {
            "input_audio_tokens": 213,
            "output_audio_tokens": 337,
            "input_text_tokens": 1562,
            "output_text_tokens": 36,
        }
    },
    "tts": {},
    "stt": {},
    "call_duration_seconds": 61,
}


def test_livekit_gemini_processor_attributed_to_google_realtime_no_double_count():
    result = cost_calculator.calculate_actual_cost(
        RUN345_USAGE,
        runtime_configuration={
            "realtime_provider": "google_realtime",
            "realtime_model": "gemini-3.1-flash-live-preview",
        },
    )
    items = [
        i
        for i in result["components"]
        if i.get("service") == "realtime"
        and i.get("model") == "gemini-3.1-flash-live-preview"
    ]
    # Exactly one component: the token-based component wins; the duration
    # estimate must NOT be added on top (run-345 double-charge regression).
    assert len(items) == 1
    item = items[0]
    assert item["provider"] == "google_realtime"
    assert item["priced"] is True
    assert item["cost_status"] == "PRICED"
    expected_usd = (
        213 * 3.00 + 337 * 12.00 + 1562 * 0.75 + 36 * 4.50
    ) / 1_000_000
    assert abs(item["cost_usd"] - expected_usd) < 1e-9
    # No generic-google unpriced twin and no duration-estimate component.
    assert not any(
        i.get("provider") == "google" and i.get("service") == "realtime"
        for i in result["components"]
    )
    assert not any(
        i.get("processor") == "runtime_configuration"
        for i in result["components"]
    )
    assert not any(
        i.get("cost_status") == "UNPRICED" for i in result["components"]
    )


def test_gemini_31_flash_live_preview_has_modality_aware_rates():
    model = PRICING_REGISTRY["llm"][ServiceProviders.GOOGLE_REALTIME][
        "gemini-3.1-flash-live-preview"
    ]
    assert isinstance(model, RealtimeAudioTextPricingModel)
    assert model.audio_input_token_price == Decimal("3.00") / 1_000_000
    assert model.audio_output_token_price == Decimal("12.00") / 1_000_000
    assert model.text_input_token_price == Decimal("0.75") / 1_000_000
    assert model.text_output_token_price == Decimal("4.50") / 1_000_000


def test_stale_fx_is_flagged_and_fresh_fx_is_not():
    empty = {"llm": {}, "tts": {}, "stt": {}}
    with patch.dict(
        cc_mod.USD_INR_RATE_SOURCE,
        {"pair": "USD_INR", "rate": 95.8945, "source_last_checked": "2026-01-01"},
        clear=False,
    ):
        stale_result = cost_calculator.calculate_actual_cost(empty)
    assert stale_result["fx_stale"] is True
    assert any("stale" in w.lower() for w in stale_result["warnings"])
    today = datetime.now(UTC).date().isoformat()
    with patch.dict(
        cc_mod.USD_INR_RATE_SOURCE,
        {"source_last_checked": today},
        clear=False,
    ):
        fresh_result = cost_calculator.calculate_actual_cost(empty)
    assert fresh_result["fx_stale"] is False
    assert not any("stale" in w.lower() for w in fresh_result["warnings"])


def test_unknown_model_is_unpriced_not_silently_zero():
    usage = {
        "llm": {
            "MysteryLLMService#0|||mystery-model-1": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            }
        },
        "tts": {},
        "stt": {},
    }
    result = cost_calculator.calculate_actual_cost(usage)
    item = result["components"][0]
    assert item["priced"] is False
    assert item["cost_status"] == "UNPRICED"
    assert item["cost_usd"] == 0.0
    assert any("pricing" in w.lower() for w in result["warnings"])
