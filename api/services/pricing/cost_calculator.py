"""
Cost Calculator for Workflow Runs

This module provides a comprehensive cost calculation system for workflow runs based on usage metrics
from different AI service providers (OpenAI, Groq, Deepgram, etc.).

Features:
- Token-based pricing for LLM services with cache optimization support
- Character-based pricing for TTS services
- Time-based pricing for STT services
- Configurable pricing models that can be updated
- Support for multiple providers and models
- Automatic provider inference from model names
- JSON serialization support for database storage
- Commercial cost ceilings and budget admission checks for Indian business voice MVP
"""

import math
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from loguru import logger

from api.services.configuration.registry import ServiceProviders
from api.services.pricing import PRICING_REGISTRY
from api.services.pricing.models import (
    CharacterPricingModel,
    PricingModel,
    RealtimeAudioTextPricingModel,
    TimePricingModel,
    TokenPricingModel,
)

REALTIME_PROVIDERS = {
    ServiceProviders.OPENAI_REALTIME.value,
    ServiceProviders.GOOGLE_REALTIME.value,
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
}

# Official Gemini Live audio rates (USD per audio minute), used for
# duration-based estimates when separate audio token counts are not persisted.
REALTIME_AUDIO_PER_MINUTE_USD: dict[str, dict[str, Decimal]] = {
    "gemini-3.8-live": {"input": Decimal("0.005"), "output": Decimal("0.018")},
    "gemini-3.1-flash-live-preview": {
        "input": Decimal("0.005"),
        "output": Decimal("0.018"),
    },
}

PRICING_SOURCES = {
    ServiceProviders.GROQ.value: {
        "provider": ServiceProviders.GROQ.value,
        "source_url": "https://groq.com/pricing",
        "source_last_checked": "2026-05-22",
    },
    ServiceProviders.GOOGLE.value: {
        "provider": ServiceProviders.GOOGLE.value,
        "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "source_last_checked": "2026-05-22",
        "source_last_updated": "2026-05-19",
    },
    ServiceProviders.GOOGLE_REALTIME.value: {
        "provider": ServiceProviders.GOOGLE_REALTIME.value,
        "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "source_last_checked": "2026-05-22",
        "source_last_updated": "2026-05-19",
    },
    ServiceProviders.GOOGLE_VERTEX_REALTIME.value: {
        "provider": ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
        "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        "source_last_checked": "2026-05-22",
        "source_last_updated": "2026-05-19",
    },
}

DEFAULT_USD_INR_RATE = Decimal(os.getenv("USD_INR_RATE", "95.8945"))
USD_INR_RATE_SOURCE = {
    "pair": "USD_INR",
    "rate": float(DEFAULT_USD_INR_RATE),
    "source_url": os.getenv(
        "USD_INR_RATE_SOURCE_URL",
        "https://www.xe.com/currencyconverter/convert/?Amount=1&From=USD&To=INR",
    ),
    "source_last_checked": os.getenv("USD_INR_RATE_SOURCE_LAST_CHECKED", "2026-05-22"),
    "note": "Used to normalize USD-denominated AI API spend into INR for cost analytics.",
}

# Number of days after source_last_checked before INR conversions are flagged
# as stale estimates. We never invent a newer rate; the configured/default rate
# remains in use but is explicitly labelled.
FX_STALE_AFTER_DAYS = 30


def fx_rate_is_stale(now: datetime | None = None) -> bool:
    """True when the configured FX source is older than FX_STALE_AFTER_DAYS.

    A missing or unparseable ``source_last_checked`` is treated as stale.
    """
    last_checked = str(USD_INR_RATE_SOURCE.get("source_last_checked") or "")
    try:
        checked = datetime.fromisoformat(last_checked)
    except ValueError:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - checked > timedelta(days=FX_STALE_AFTER_DAYS)


def _provider_value(provider: str | ServiceProviders) -> str:
    return provider.value if isinstance(provider, ServiceProviders) else str(provider)


def usd_to_inr(amount_usd: Decimal | float | int | str) -> Decimal:
    return Decimal(str(amount_usd)) * DEFAULT_USD_INR_RATE


class CostCalculator:
    """Main cost calculator class"""

    def __init__(self, pricing_registry: Dict = None):
        self.pricing_registry = pricing_registry or PRICING_REGISTRY

    def get_pricing_model(
        self, service_type: str, provider: str, model: str
    ) -> Optional[PricingModel]:
        """Get pricing model for a specific service, provider, and model"""
        try:
            service_pricing = self.pricing_registry.get(service_type, {})

            provider_pricing = service_pricing.get(provider, {})
            pricing_model = provider_pricing.get(model) or provider_pricing.get(
                "default"
            )

            if pricing_model:
                return pricing_model

            default_provider_pricing = service_pricing.get("default", {})
            return default_provider_pricing.get(model) or default_provider_pricing.get(
                "default"
            )

        except (KeyError, AttributeError):
            return None

    def calculate_llm_cost(
        self, provider: str, model: str, usage: Dict[str, int]
    ) -> Decimal:
        """Calculate cost for LLM usage"""
        pricing_model = self.get_pricing_model("llm", provider, model)
        if not pricing_model:
            return Decimal("0")
        return pricing_model.calculate_cost(usage)

    def calculate_tts_cost(
        self, provider: str, model: str, character_count: int
    ) -> Decimal:
        """Calculate cost for TTS usage"""
        pricing_model = self.get_pricing_model("tts", provider, model)
        if not pricing_model:
            return Decimal("0")
        return pricing_model.calculate_cost(character_count)

    def calculate_stt_cost(self, provider: str, model: str, seconds: float) -> Decimal:
        """Calculate cost for STT usage"""
        pricing_model = self.get_pricing_model("stt", provider, model)
        if not pricing_model:
            return Decimal("0")
        return pricing_model.calculate_cost(seconds)

    def calculate_total_cost(self, usage_info: Dict) -> Dict[str, Any]:
        llm_cost_total = Decimal("0")
        tts_cost_total = Decimal("0")
        stt_cost_total = Decimal("0")

        llm_usage = usage_info.get("llm", {})
        for key, usage in llm_usage.items():
            processor, model = self._parse_key(key)
            provider = self._infer_provider(processor, model, "llm")
            cost = self.calculate_llm_cost(provider, model, usage)
            llm_cost_total += cost

        tts_usage = usage_info.get("tts", {})
        for key, character_count in tts_usage.items():
            processor, model = self._parse_key(key)
            if model.lower() in ["none", "null", ""]:
                provider = self._infer_provider_from_processor(processor, "tts")
                model = "default"
            else:
                provider = self._infer_provider(processor, model, "tts")
            cost = self.calculate_tts_cost(provider, model, character_count)
            tts_cost_total += cost

        stt_usage = usage_info.get("stt", {})
        for key, seconds in stt_usage.items():
            processor, model = self._parse_key(key)
            provider = self._infer_provider(processor, model, "stt")
            cost = self.calculate_stt_cost(provider, model, seconds)
            stt_cost_total += cost

        total_cost = llm_cost_total + tts_cost_total + stt_cost_total

        return {
            "llm_cost": float(llm_cost_total),
            "tts_cost": float(tts_cost_total),
            "stt_cost": float(stt_cost_total),
            "total": float(total_cost),
        }

    def calculate_actual_cost(
        self,
        usage_info: Dict | None,
        *,
        runtime_configuration: dict | None = None,
        calculated_at: str | None = None,
    ) -> Dict[str, Any]:
        usage_info = usage_info or {}
        components: list[dict[str, Any]] = []
        warnings: list[str] = []

        components.extend(
            self._llm_cost_components(usage_info.get("llm", {}), warnings)
        )
        components.extend(
            self._tts_cost_components(usage_info.get("tts", {}), warnings)
        )
        components.extend(
            self._stt_cost_components(usage_info.get("stt", {}), warnings)
        )
        components.extend(
            self._realtime_duration_estimate_components(
                usage_info,
                runtime_configuration=runtime_configuration,
                existing_components=components,
            )
        )

        for item in components:
            cost_usd = Decimal(str(item.get("cost_usd") or 0))
            item["currency"] = item.get("currency") or "USD"
            item["cost_inr"] = float(usd_to_inr(cost_usd))
            if cost_usd:
                item["exchange_rate"] = USD_INR_RATE_SOURCE

        total_usd = sum(Decimal(str(item.get("cost_usd") or 0)) for item in components)
        ai_total_usd = sum(
            Decimal(str(item.get("cost_usd") or 0))
            for item in components
            if item.get("service") != "telephony"
        )
        total_inr = sum(Decimal(str(item.get("cost_inr") or 0)) for item in components)
        ai_total_inr = sum(
            Decimal(str(item.get("cost_inr") or 0))
            for item in components
            if item.get("service") != "telephony"
        )

        sources_by_provider: dict[str, dict[str, str]] = {}
        for item in components:
            provider = str(item.get("provider") or "")
            if provider in PRICING_SOURCES:
                sources_by_provider[provider] = PRICING_SOURCES[provider]

        fx_stale = fx_rate_is_stale()
        if fx_stale:
            warnings.append(
                "USD_INR exchange rate is stale "
                f"(source_last_checked={USD_INR_RATE_SOURCE.get('source_last_checked')}); "
                "INR figures are estimates, not reconciled charges."
            )

        return {
            "version": 1,
            "currency": "INR",
            "source_currency": "USD",
            "total_usd": float(total_usd),
            "ai_total_usd": float(ai_total_usd),
            "telephony_total_usd": 0.0,
            "total_inr": float(total_inr),
            "ai_total_inr": float(ai_total_inr),
            "telephony_total_inr": 0.0,
            "components": components,
            "estimated": any(bool(item.get("estimated")) for item in components),
            "warnings": warnings,
            "exchange_rates": [USD_INR_RATE_SOURCE],
            "fx_stale": fx_stale,
            "pricing_sources": list(sources_by_provider.values()),
            "calculated_at": calculated_at or datetime.now(UTC).isoformat(),
        }

    def _llm_cost_components(
        self, llm_usage: Dict[str, Dict[str, int]], warnings: list[str]
    ) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []
        for key, usage in llm_usage.items():
            processor, model = self._parse_key(key)
            provider = self._infer_provider(processor, model, "llm")
            pricing_model = self.get_pricing_model("llm", provider, model)
            cost = (
                pricing_model.calculate_cost(usage) if pricing_model else Decimal("0")
            )
            service = (
                "realtime"
                if self._is_realtime_provider_or_model(provider, model)
                else "llm"
            )
            component = self._base_component(
                service=service,
                provider=provider,
                model=model,
                processor=processor,
                cost=cost,
                pricing_model=pricing_model,
                usage={
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens")
                    or 0,
                    "cache_creation_input_tokens": usage.get(
                        "cache_creation_input_tokens"
                    )
                    or 0,
                },
            )
            if not pricing_model:
                warning = (
                    f"No llm pricing configured for provider={_provider_value(provider)} "
                    f"model={model}"
                )
                warnings.append(warning)
                component["warning"] = warning
            if service == "realtime":
                component["estimated"] = True
                component["note"] = (
                    "Realtime usage is estimated. Audio/text token rates are "
                    "applied when modality-specific counts are reported; "
                    "otherwise text token rates are used."
                )
            components.append(component)
        return components

    def _tts_cost_components(
        self, tts_usage: Dict[str, int], warnings: list[str]
    ) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []
        for key, character_count in tts_usage.items():
            processor, model = self._parse_key(key)
            if model.lower() in ["none", "null", ""]:
                provider = self._infer_provider_from_processor(processor, "tts")
                model = "default"
            else:
                provider = self._infer_provider(processor, model, "tts")
            pricing_model = self.get_pricing_model("tts", provider, model)
            cost = (
                pricing_model.calculate_cost(character_count)
                if pricing_model
                else Decimal("0")
            )
            component = self._base_component(
                service="tts",
                provider=provider,
                model=model,
                processor=processor,
                cost=cost,
                pricing_model=pricing_model,
                usage={"characters": character_count},
            )
            if not pricing_model:
                warning = (
                    f"No tts pricing configured for provider={_provider_value(provider)} "
                    f"model={model}"
                )
                warnings.append(warning)
                component["warning"] = warning
            components.append(component)
        return components

    def _stt_cost_components(
        self, stt_usage: Dict[str, float], warnings: list[str]
    ) -> list[dict[str, Any]]:
        components: list[dict[str, Any]] = []
        for key, seconds in stt_usage.items():
            processor, model = self._parse_key(key)
            provider = self._infer_provider(processor, model, "stt")
            pricing_model = self.get_pricing_model("stt", provider, model)
            cost = (
                pricing_model.calculate_cost(seconds) if pricing_model else Decimal("0")
            )
            component = self._base_component(
                service="stt",
                provider=provider,
                model=model,
                processor=processor,
                cost=cost,
                pricing_model=pricing_model,
                usage={"seconds": seconds},
            )
            if not pricing_model:
                warning = (
                    f"No stt pricing configured for provider={_provider_value(provider)} "
                    f"model={model}"
                )
                warnings.append(warning)
                component["warning"] = warning
            components.append(component)
        return components

    def _realtime_duration_estimate_components(
        self,
        usage_info: Dict,
        *,
        runtime_configuration: dict | None,
        existing_components: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        runtime_configuration = runtime_configuration or {}
        provider = runtime_configuration.get("realtime_provider")
        model = runtime_configuration.get("realtime_model")
        if not provider or not model:
            return []

        provider_value = _provider_value(provider)
        if any(
            item.get("service") == "realtime"
            and item.get("provider") == provider_value
            and item.get("model") == model
            for item in existing_components
        ):
            return []

        duration_seconds = float(usage_info.get("call_duration_seconds") or 0)
        if duration_seconds <= 0:
            return []

        audio_rates = REALTIME_AUDIO_PER_MINUTE_USD.get(model)
        if provider_value == ServiceProviders.GOOGLE_REALTIME.value and audio_rates:
            minutes = Decimal(str(duration_seconds)) / Decimal("60")
            input_cost = minutes * audio_rates["input"]
            output_cost = minutes * audio_rates["output"]
            return [
                {
                    "service": "realtime",
                    "provider": provider_value,
                    "model": model,
                    "processor": "runtime_configuration",
                    "label": f"Gemini Live audio estimate ({model})",
                    "cost_usd": float(input_cost + output_cost),
                    "estimated": True,
                    "usage": {
                        "duration_seconds": duration_seconds,
                        "estimated_input_audio_minutes": float(minutes),
                        "estimated_output_audio_minutes": float(minutes),
                    },
                    "pricing": {
                        "input_audio_usd_per_minute": float(audio_rates["input"]),
                        "output_audio_usd_per_minute": float(audio_rates["output"]),
                    },
                    "source_url": PRICING_SOURCES[provider_value]["source_url"],
                    "note": (
                        "Estimated from full call duration because separate "
                        "Gemini input/output audio minutes were not persisted."
                    ),
                }
            ]
        return []

    def _base_component(
        self,
        *,
        service: str,
        provider: str | ServiceProviders,
        model: str,
        processor: str,
        cost: Decimal,
        pricing_model: PricingModel | None,
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        provider_value = _provider_value(provider)
        component = {
            "service": service,
            "provider": provider_value,
            "model": model,
            "processor": processor,
            "cost_usd": float(cost),
            "usage": usage,
            "priced": pricing_model is not None,
            "cost_status": "UNPRICED" if pricing_model is None else "PRICED",
        }
        pricing = self._pricing_metadata(pricing_model)
        if pricing:
            component["pricing"] = pricing
        source = PRICING_SOURCES.get(provider_value)
        if source:
            component["source_url"] = source["source_url"]
        return component

    def _pricing_metadata(
        self, pricing_model: PricingModel | None
    ) -> dict[str, float | str] | None:
        if pricing_model is None:
            return None
        if isinstance(pricing_model, RealtimeAudioTextPricingModel):
            return {
                "unit": "tokens",
                "audio_input_usd_per_1m_tokens": float(
                    pricing_model.audio_input_token_price * Decimal("1000000")
                ),
                "audio_output_usd_per_1m_tokens": float(
                    pricing_model.audio_output_token_price * Decimal("1000000")
                ),
                "text_input_usd_per_1m_tokens": float(
                    pricing_model.text_input_token_price * Decimal("1000000")
                ),
                "text_output_usd_per_1m_tokens": float(
                    pricing_model.text_output_token_price * Decimal("1000000")
                ),
            }
        if isinstance(pricing_model, TokenPricingModel):
            return {
                "unit": "tokens",
                "prompt_usd_per_1m_tokens": float(
                    pricing_model.prompt_token_price * Decimal("1000000")
                ),
                "completion_usd_per_1m_tokens": float(
                    pricing_model.completion_token_price * Decimal("1000000")
                ),
            }
        if isinstance(pricing_model, CharacterPricingModel):
            return {
                "unit": "characters",
                "usd_per_1k_characters": float(
                    pricing_model.character_price * Decimal("1000")
                ),
            }
        if isinstance(pricing_model, TimePricingModel):
            return {
                "unit": "seconds",
                "usd_per_minute": float(pricing_model.second_price * Decimal("60")),
            }
        return {"unit": "unknown"}

    def _parse_key(self, key) -> Tuple[str, str]:
        if isinstance(key, str) and "|||" in key:
            parts = key.split("|||", 1)
            return parts[0], parts[1]
        else:
            return str(key), "unknown"

    def _infer_provider(self, processor: str, model: str, service_type: str) -> str:
        processor_provider = self._infer_provider_from_processor(
            processor, service_type
        )
        if processor_provider != "unknown":
            return processor_provider
        return self._infer_provider_from_model(model, service_type)

    def _infer_provider_from_model(self, model: str, service_type: str) -> str:
        if not model:
            return "unknown"

        model_lower = model.lower()

        if "openai" in model_lower and "realtime" in model_lower:
            return ServiceProviders.OPENAI_REALTIME
        if "gemini" in model_lower and "live" in model_lower:
            if model_lower.startswith("google/"):
                return ServiceProviders.GOOGLE_VERTEX_REALTIME
            return ServiceProviders.GOOGLE_REALTIME

        if "gemini" in model_lower:
            return ServiceProviders.GOOGLE

        if any(
            keyword in model_lower
            for keyword in [
                "groq",
                "llama",
                "deepseek",
                "qwen",
                "gemma",
                "gpt-oss",
            ]
        ):
            return ServiceProviders.GROQ

        if any(keyword in model_lower for keyword in ["gpt", "whisper", "openai"]):
            return ServiceProviders.OPENAI

        if any(keyword in model_lower for keyword in ["eleven"]):
            return ServiceProviders.ELEVENLABS

        if any(
            keyword in model_lower
            for keyword in ["deepgram", "nova", "phonecall", "general"]
        ):
            return ServiceProviders.DEEPGRAM

        return "unknown"

    def _infer_provider_from_processor(self, processor: str, service_type: str) -> str:
        if not processor:
            return "unknown"

        processor_lower = processor.lower()

        if "openai" in processor_lower and "realtime" in processor_lower:
            return ServiceProviders.OPENAI_REALTIME
        if any(
            keyword in processor_lower
            for keyword in ["google_vertex_realtime", "geminilivevertex", "vertex"]
        ):
            return ServiceProviders.GOOGLE_VERTEX_REALTIME
        if any(
            keyword in processor_lower
            for keyword in [
                "google_realtime",
                "gemini_live",
                "geminilive",
                "livekitgemini",
            ]
        ):
            return ServiceProviders.GOOGLE_REALTIME

        if any(keyword in processor_lower for keyword in ["google", "gemini"]):
            return ServiceProviders.GOOGLE

        if any(keyword in processor_lower for keyword in ["openai", "gpt"]):
            return ServiceProviders.OPENAI

        if any(keyword in processor_lower for keyword in ["groq"]):
            return ServiceProviders.GROQ

        if any(keyword in processor_lower for keyword in ["deepgram"]):
            return ServiceProviders.DEEPGRAM

        return "unknown"

    def _is_realtime_provider_or_model(
        self, provider: str | ServiceProviders, model: str
    ) -> bool:
        provider_value = _provider_value(provider)
        model_lower = (model or "").lower()
        return provider_value in REALTIME_PROVIDERS or any(
            keyword in model_lower for keyword in ["realtime", "live"]
        )

    def update_pricing(
        self, service_type: str, provider: str, model: str, pricing_model: PricingModel
    ):
        if service_type not in self.pricing_registry:
            self.pricing_registry[service_type] = {}
        if provider not in self.pricing_registry[service_type]:
            self.pricing_registry[service_type][provider] = {}
        self.pricing_registry[service_type][provider][model] = pricing_model


# Global cost calculator instance
cost_calculator = CostCalculator()


# ---------------------------------------------------------------------------
# Commercial Operating Cost Ceilings & Budget Admission Controller
# ---------------------------------------------------------------------------

CEILING_IVR_ONLY_INR = 0.50
CEILING_BASIC_OUTBOUND_INR = 0.60
CEILING_STANDARD_GEMINI_INR = 1.80
CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR = 2.80

RATES = {
    "carrier_inbound_pulse_inr": 0.30,
    "carrier_outbound_pulse_inr": 0.40,
    "infra_per_minute_inr": 0.15,
    "gemini_live_per_minute_inr": 0.85,
    "deepgram_stt_per_minute_inr": 0.36,
    "deepseek_llm_per_minute_inr": 0.01,
    "cartesia_tts_per_minute_inr": 0.90,
}


RATE_CARD_VERSION = "2026.05"


def calculate_call_cost(
    mode: str,
    duration_seconds: float,
    is_outbound: bool = False,
    ivr_only: bool = False,
) -> Dict[str, Any]:
    """Calculate exact billable cost using Decimal precision and verify compliance against commercial cost ceilings."""
    dur_sec = max(0.0, float(duration_seconds or 0.0))
    pulses = int(math.ceil(dur_sec / 60.0)) if dur_sec > 0 else 0
    minutes = dur_sec / 60.0

    carrier_rate = (
        Decimal(str(RATES["carrier_outbound_pulse_inr"]))
        if is_outbound
        else Decimal(str(RATES["carrier_inbound_pulse_inr"]))
    )
    carrier_cost = Decimal(str(pulses)) * carrier_rate
    infra_cost = Decimal(str(round(minutes * RATES["infra_per_minute_inr"], 4)))

    ai_cost = Decimal("0.0")
    itemized: Dict[str, float] = {
        "carrier_telecom": float(round(carrier_cost, 2)),
        "infrastructure": float(round(infra_cost, 2)),
    }

    mode_lower = mode.lower()

    if ivr_only or mode_lower in ("ivr", "economy_ivr"):
        ceiling = CEILING_IVR_ONLY_INR
        ai_cost = Decimal("0.0")
        itemized["ai_engine"] = 0.0
    elif mode_lower == "budget":
        ceiling = CEILING_STANDARD_GEMINI_INR
        ai_cost = Decimal(str(round(minutes * (RATES["gemini_live_per_minute_inr"] * 0.5), 4)))
        itemized["ai_engine"] = float(round(ai_cost, 2))
    elif mode_lower == "premium":
        ceiling = CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR
        stt_cost = Decimal(str(round(minutes * RATES["deepgram_stt_per_minute_inr"], 4)))
        llm_cost = Decimal(str(round(minutes * RATES["deepseek_llm_per_minute_inr"], 4)))
        tts_cost = Decimal(str(round(minutes * RATES["cartesia_tts_per_minute_inr"], 4)))
        ai_cost = stt_cost + llm_cost + tts_cost
        itemized["deepgram_stt"] = float(round(stt_cost, 2))
        itemized["deepseek_llm"] = float(round(llm_cost, 2))
        itemized["cartesia_tts"] = float(round(tts_cost, 2))
    else:  # "standard"
        ceiling = CEILING_STANDARD_GEMINI_INR
        ai_cost = Decimal(str(round(minutes * RATES["gemini_live_per_minute_inr"], 4)))
        itemized["gemini_live_ai"] = float(round(ai_cost, 2))

    total_cost_d = carrier_cost + infra_cost + ai_cost
    total_cost = float(round(total_cost_d, 2))
    effective_rate_per_min = round(total_cost / max(1.0, minutes), 2) if minutes > 0 else 0.0
    is_compliant = effective_rate_per_min <= (ceiling * 1.05)

    return {
        "mode": mode,
        "duration_seconds": dur_sec,
        "billed_pulses": pulses,
        "total_cost_inr": total_cost,
        "effective_rate_per_min": effective_rate_per_min,
        "ceiling_rate_per_min": ceiling,
        "is_within_ceiling": is_compliant,
        "currency": "INR",
        "rate_card_version": RATE_CARD_VERSION,
        "itemized": itemized,
    }


def check_budget_admission(
    current_spend_inr: float,
    spending_cap_inr: float,
    estimated_call_cost_inr: float = 4.50,
) -> bool:
    """Verifies that a business has sufficient remaining balance before admitting new calls."""
    if spending_cap_inr <= 0:
        return True
    remaining = spending_cap_inr - current_spend_inr
    admitted = remaining >= estimated_call_cost_inr
    if not admitted:
        logger.warning(
            f"[Budget Guard] Call rejected: spend ₹{current_spend_inr:.2f} + cost ₹{estimated_call_cost_inr:.2f} exceeds cap ₹{spending_cap_inr:.2f}"
        )
    return admitted
