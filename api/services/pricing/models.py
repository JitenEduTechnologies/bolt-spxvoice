"""
Base pricing models for different service types.
"""

from decimal import Decimal
from enum import Enum
from typing import Any, Dict


class CostType(Enum):
    LLM_TOKENS = "llm_tokens"
    TTS_CHARACTERS = "tts_characters"
    STT_SECONDS = "stt_seconds"


class PricingModel:
    """Base class for pricing models"""

    def calculate_cost(self, usage: Any) -> Decimal:
        """Calculate cost based on usage"""
        raise NotImplementedError


class TokenPricingModel(PricingModel):
    """Pricing model for token-based services (LLM)"""

    def __init__(
        self,
        prompt_token_price: Decimal,
        completion_token_price: Decimal,
        cache_read_discount: Decimal = Decimal("0.5"),  # 50% discount for cache reads
        cache_creation_multiplier: Decimal = Decimal(
            "1.25"
        ),  # 25% premium for cache creation
    ):
        self.prompt_token_price = prompt_token_price
        self.completion_token_price = completion_token_price
        self.cache_read_discount = cache_read_discount
        self.cache_creation_multiplier = cache_creation_multiplier

    def calculate_cost(self, usage: Dict[str, int]) -> Decimal:
        """Calculate cost for LLM token usage"""
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens") or 0
        cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0

        # Base cost
        prompt_cost = Decimal(prompt_tokens) * self.prompt_token_price
        completion_cost = Decimal(completion_tokens) * self.completion_token_price

        # Cache adjustments
        cache_read_savings = (
            Decimal(cache_read_tokens)
            * self.prompt_token_price
            * self.cache_read_discount
        )
        cache_creation_premium = (
            Decimal(cache_creation_tokens)
            * self.prompt_token_price
            * (self.cache_creation_multiplier - 1)
        )

        total_cost = (
            prompt_cost + completion_cost - cache_read_savings + cache_creation_premium
        )
        return max(total_cost, Decimal("0"))  # Ensure non-negative


class RealtimeAudioTextPricingModel(PricingModel):
    """Pricing model for realtime (audio + text) Gemini Live usage.

    Modality-aware: audio tokens and text tokens are priced at their own rates.
    When modality-specific counts are not reported, falls back to the text
    token rates applied to the generic prompt/completion totals.
    """

    def __init__(
        self,
        audio_input_token_price: Decimal,
        audio_output_token_price: Decimal,
        text_input_token_price: Decimal,
        text_output_token_price: Decimal,
    ):
        self.audio_input_token_price = audio_input_token_price
        self.audio_output_token_price = audio_output_token_price
        self.text_input_token_price = text_input_token_price
        self.text_output_token_price = text_output_token_price

    def calculate_cost(self, usage: Dict[str, int]) -> Decimal:
        input_audio = int(usage.get("input_audio_tokens") or 0)
        output_audio = int(usage.get("output_audio_tokens") or 0)
        input_text = int(usage.get("input_text_tokens") or 0)
        output_text = int(usage.get("output_text_tokens") or 0)

        if input_audio or output_audio or input_text or output_text:
            return (
                Decimal(input_audio) * self.audio_input_token_price
                + Decimal(output_audio) * self.audio_output_token_price
                + Decimal(input_text) * self.text_input_token_price
                + Decimal(output_text) * self.text_output_token_price
            )

        # No modality breakdown reported: fall back to text rates on the
        # generic prompt/completion totals (historical behavior).
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        return (
            Decimal(prompt_tokens) * self.text_input_token_price
            + Decimal(completion_tokens) * self.text_output_token_price
        )


class CharacterPricingModel(PricingModel):
    """Pricing model for character-based services (TTS)"""

    def __init__(self, character_price: Decimal):
        self.character_price = character_price

    def calculate_cost(self, character_count: int) -> Decimal:
        """Calculate cost for TTS character usage"""
        return Decimal(character_count) * self.character_price


class TimePricingModel(PricingModel):
    """Pricing model for time-based services (STT)"""

    def __init__(self, second_price: Decimal):
        self.second_price = second_price

    def calculate_cost(self, seconds: float) -> Decimal:
        """Calculate cost for STT time usage"""
        return Decimal(str(seconds)) * self.second_price
