import pytest
from api.services.pricing.cost_calculator import (
    CEILING_BASIC_OUTBOUND_INR,
    CEILING_IVR_ONLY_INR,
    CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR,
    CEILING_STANDARD_GEMINI_INR,
    calculate_call_cost,
    check_budget_admission,
)
from api.services.telephony.ivr_service import (
    BusinessHoursConfig,
    BusinessIVRConfig,
    generate_inbound_ivr_xml,
    handle_dtmf_input_xml,
    is_within_business_hours,
)


def test_ivr_xml_generation_disabled():
    """When IVR is disabled, inbound call dials SIP bridge directly."""
    config = BusinessIVRConfig(enabled=False)
    sip_uri = "sip:livekit-sip@sip.spxvoice.internal"
    xml = generate_inbound_ivr_xml(
        config=config,
        action_url="http://localhost:8001/api/v1/telephony/voicelink/dtmf/1",
        sip_inbound_uri=sip_uri,
    )
    assert f"<Sip>{sip_uri}</Sip>" in xml
    assert "<Gather" not in xml


def test_ivr_xml_generation_enabled():
    """When IVR is enabled, plays greeting and collects DTMF digits with zero AI costs."""
    config = BusinessIVRConfig(
        enabled=True,
        greeting="Press 1 for Solar AI or 2 for human.",
        timeout_seconds=5,
    )
    action_url = "http://localhost:8001/api/v1/telephony/voicelink/dtmf/123"
    xml = generate_inbound_ivr_xml(
        config=config,
        action_url=action_url,
        sip_inbound_uri="sip:livekit@sip.spxvoice.internal",
    )
    assert '<Gather action="' in xml
    assert action_url in xml
    assert 'numDigits="1"' in xml
    assert "Press 1 for Solar AI or 2 for human." in xml


def test_ivr_dtmf_routing_ai_and_human():
    """DTMF 1 routes to LiveKit SIP AI; DTMF 2 routes to human phone number."""
    config = BusinessIVRConfig(
        enabled=True,
        keypad_mapping={"1": "ai_agent", "2": "human_transfer"},
        human_route={"enabled": True, "phone_number": "+919849012345"},
    )
    sip_uri = "sip:livekit@sip.spxvoice.internal"

    # DTMF 1 -> AI Handoff
    xml_ai = handle_dtmf_input_xml(digits="1", config=config, sip_inbound_uri=sip_uri)
    assert f"<Sip>{sip_uri}</Sip>" in xml_ai
    assert "+919849012345" not in xml_ai

    # DTMF 2 -> Human Transfer
    xml_human = handle_dtmf_input_xml(digits="2", config=config, sip_inbound_uri=sip_uri)
    assert "<Dial" in xml_human
    assert "+919849012345" in xml_human


def test_cost_ceilings_ivr_only():
    """IVR-only call must remain strictly under ₹0.50 per connected minute."""
    cost = calculate_call_cost(mode="ivr", duration_seconds=120.0, is_outbound=False, ivr_only=True)
    assert cost["is_within_ceiling"] is True
    assert cost["effective_rate_per_min"] <= CEILING_IVR_ONLY_INR
    assert cost["itemized"]["ai_engine"] == 0.0  # Zero AI token expense


def test_cost_ceilings_basic_outbound():
    """Outbound telephony carrier pulses must meet business requirements."""
    cost = calculate_call_cost(mode="ivr", duration_seconds=60.0, is_outbound=True)
    assert cost["effective_rate_per_min"] <= CEILING_BASIC_OUTBOUND_INR


def test_cost_ceilings_standard_gemini_live():
    """Standard Gemini Live mode must remain under ₹1.80 per connected minute."""
    cost = calculate_call_cost(mode="standard", duration_seconds=180.0, is_outbound=False)
    assert cost["is_within_ceiling"] is True
    assert cost["effective_rate_per_min"] <= CEILING_STANDARD_GEMINI_INR
    assert cost["total_cost_inr"] > 0


def test_cost_ceilings_premium_deepseek_cartesia():
    """Premium DeepSeek + Cartesia + Deepgram must remain under ₹2.80 per connected minute."""
    cost = calculate_call_cost(mode="premium", duration_seconds=120.0, is_outbound=False)
    assert cost["is_within_ceiling"] is True
    assert cost["effective_rate_per_min"] <= CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR
    assert "deepgram_stt" in cost["itemized"]
    assert "deepseek_llm" in cost["itemized"]
    assert "cartesia_tts" in cost["itemized"]


def test_budget_admission_guard():
    """Budget guard stops calls when spending cap is reached."""
    # Within budget:
    assert check_budget_admission(current_spend_inr=50.0, spending_cap_inr=100.0, estimated_call_cost_inr=5.0) is True
    # Exceeds budget:
    assert check_budget_admission(current_spend_inr=98.0, spending_cap_inr=100.0, estimated_call_cost_inr=5.0) is False
    # Unlimited budget (cap <= 0):
    assert check_budget_admission(current_spend_inr=500.0, spending_cap_inr=0.0, estimated_call_cost_inr=10.0) is True


def test_ivr_xml_generation_vobiz_uses_speak():
    """Vobiz IVR XML must use <Speak> instead of <Say> and include method='POST'."""
    config = BusinessIVRConfig(
        enabled=True,
        greeting="Press 1 for sales or 2 for support.",
        timeout_seconds=5,
    )
    xml = generate_inbound_ivr_xml(
        config=config,
        action_url="http://localhost:8001/api/v1/telephony/vobiz/gather",
        sip_inbound_uri="sip:livekit@sip.spxvoice.internal",
        provider="vobiz",
    )
    assert '<Gather action="http://localhost:8001/api/v1/telephony/vobiz/gather" method="POST" numDigits="1" timeout="5">' in xml
    assert '<Speak voice="WOMAN">Press 1 for sales or 2 for support.</Speak>' in xml
    assert "<Say" not in xml


def test_ivr_xml_generation_play_for_audio_url():
    """When greeting is an audio URL, IVR XML must use <Play>."""
    config = BusinessIVRConfig(
        enabled=True,
        greeting="https://cdn.example.com/audio/welcome.wav",
        timeout_seconds=5,
    )
    xml = generate_inbound_ivr_xml(
        config=config,
        action_url="http://localhost:8001/api/v1/telephony/vobiz/gather",
        sip_inbound_uri="sip:livekit@sip.spxvoice.internal",
        provider="vobiz",
    )
    assert '<Play>https://cdn.example.com/audio/welcome.wav</Play>' in xml
    assert f"<Speak voice=\"WOMAN\">{config.greeting}</Speak>" not in xml
    assert "<Say" not in xml


def test_ivr_dtmf_routing_vobiz_uses_speak():
    """DTMF response for Vobiz uses <Speak>."""
    config = BusinessIVRConfig(
        enabled=True,
        keypad_mapping={"1": "ai_agent"},
    )
    xml = handle_dtmf_input_xml(digits="1", config=config, sip_inbound_uri="sip:livekit@sip.spxvoice.internal", provider="vobiz")
    assert '<Speak voice="WOMAN">Connecting to your AI specialist now.</Speak>' in xml
    assert "<Say" not in xml

