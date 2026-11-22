from __future__ import annotations

"""IVR Service for SPX Voice MVP.

Provides simple, low-cost, carrier-standard IVR menus with SIP/DTMF handling.
Guarantees zero AI engine (Gemini, DeepSeek, Cartesia, STT) costs prior to AI handoff.
Supports:
- IVR on/off
- Custom greeting and menu prompt
- DTMF keypad mappings (AI agent handoff, human agent transfer)
- Business hours checking
- Timeout and fallback actions
- Dynamic VoiceXML / TwiML generation for VoiceLink / Vobiz
"""

from datetime import datetime, time
from html import escape
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field
from loguru import logger


class BusinessHoursConfig(BaseModel):
    enabled: bool = False
    start_time: str = "09:00"
    end_time: str = "19:00"
    timezone: str = "Asia/Kolkata"
    after_hours_message: str = "Our offices are currently closed, but our 24/7 AI assistant is available."
    after_hours_action: str = "ai_agent"  # "ai_agent" | "voicemail" | "hangup"


class HumanRouteConfig(BaseModel):
    enabled: bool = True
    phone_number: str = "+919876543210"
    timeout_seconds: int = 25


class AIRouteConfig(BaseModel):
    target_workflow_id: Optional[int] = None
    mode: str = "standard"  # "budget" | "standard" | "premium"


class BusinessIVRConfig(BaseModel):
    enabled: bool = False
    greeting: str = "Thank you for calling. Press 1 to speak with our AI specialist, or press 2 to speak with human support."
    keypad_mapping: Dict[str, str] = Field(
        default_factory=lambda: {"1": "ai_agent", "2": "human_transfer"}
    )
    timeout_seconds: int = 8
    fallback_action: str = "ai_agent"
    business_hours: BusinessHoursConfig = Field(default_factory=BusinessHoursConfig)
    ai_route: AIRouteConfig = Field(default_factory=AIRouteConfig)
    human_route: HumanRouteConfig = Field(default_factory=HumanRouteConfig)


def is_within_business_hours(bh: BusinessHoursConfig) -> bool:
    """Check if the current time falls within configured business hours."""
    if not bh.enabled:
        return True
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(bh.timezone or "Asia/Kolkata")
        now = datetime.now(tz).time()
        start_parts = [int(p) for p in bh.start_time.split(":")]
        end_parts = [int(p) for p in bh.end_time.split(":")]
        start = time(start_parts[0], start_parts[1])
        end = time(end_parts[0], end_parts[1])
        return start <= now <= end
    except Exception as e:
        logger.warning(f"Error evaluating business hours: {e}. Defaulting to open.")
        return True
    except Exception as e:
        logger.warning(f"Error evaluating business hours: {e}. Defaulting to open.")
        return True


def _render_prompt_tag(prompt: str, provider: str = "vobiz", voice: str = "WOMAN") -> str:
    """
    Render prompt text or audio URL into provider-compliant XML element.
    - If prompt starts with http:// or https://: returns <Play>url</Play>
    - If provider is Vobiz or Plivo: returns <Speak voice="WOMAN">text</Speak>
    - Otherwise (Twilio, VoiceLink): returns <Say>text</Say>
    """
    clean = prompt.strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        return f"<Play>{escape(clean)}</Play>"
    if provider.lower() in ("vobiz", "plivo"):
        return f'<Speak voice="{voice}">{escape(clean)}</Speak>'
    return f"<Say>{escape(clean)}</Say>"


def generate_inbound_ivr_xml(
    config: BusinessIVRConfig,
    action_url: str,
    sip_inbound_uri: Optional[str] = None,
    session_id: Optional[str] = None,
    provider: str = "vobiz",
) -> str:
    """Generate RFC 4733 compliant XML response for incoming calls.

    If IVR is disabled: connects directly to AI SIP URI without playing menus.
    If IVR is enabled: plays menu prompt and waits for DTMF keystroke.
    Supports Vobiz (<Speak>/<Play>) and Twilio/VoiceLink (<Say>/<Play>).
    """
    safe_action = escape(action_url, quote=True)

    if not config.enabled:
        # IVR is OFF -> Route immediately to LiveKit / AI SIP bridge
        greeting_tag = _render_prompt_tag("Thank you for calling SPX Voice.", provider=provider)
        if sip_inbound_uri:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"    {greeting_tag}\n"
                f'    <Dial><Sip>{sip_inbound_uri}</Sip></Dial>\n'
                "</Response>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f"    {greeting_tag}\n"
            "</Response>"
        )

    # Check Business Hours
    if not is_within_business_hours(config.business_hours):
        msg = config.business_hours.after_hours_message
        msg_tag = _render_prompt_tag(msg, provider=provider)
        if config.business_hours.after_hours_action == "ai_agent" and sip_inbound_uri:
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"    {msg_tag}\n"
                f'    <Dial><Sip>{sip_inbound_uri}</Sip></Dial>\n'
                "</Response>"
            )
        elif config.business_hours.after_hours_action == "hangup":
            closed_tag = _render_prompt_tag(
                f"{msg} Please call back during normal working hours. Goodbye.",
                provider=provider,
            )
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"    {closed_tag}\n"
                "    <Hangup/>\n"
                "</Response>"
            )

    # Generate Gather with DTMF
    greeting_tag = _render_prompt_tag(config.greeting, provider=provider)
    no_input_tag = _render_prompt_tag("We did not receive any input.", provider=provider)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f'    <Gather action="{safe_action}" method="POST" numDigits="1" timeout="{config.timeout_seconds}">\n'
        f"        {greeting_tag}\n"
        "    </Gather>\n"
        f"    {no_input_tag}\n"
    )

    # Fallback if no digit pressed
    if config.fallback_action == "ai_agent" and sip_inbound_uri:
        xml += f'    <Dial><Sip>{sip_inbound_uri}</Sip></Dial>\n'
    elif config.fallback_action == "human_transfer" and config.human_route.enabled:
        xml += f'    <Dial timeout="{config.human_route.timeout_seconds}">{config.human_route.phone_number}</Dial>\n'
    else:
        goodbye_tag = _render_prompt_tag("Thank you for calling. Goodbye.", provider=provider)
        xml += f"    {goodbye_tag}\n    <Hangup/>\n"

    xml += "</Response>"
    return xml


def handle_dtmf_input_xml(
    digits: str,
    config: BusinessIVRConfig,
    sip_inbound_uri: Optional[str] = None,
    provider: str = "vobiz",
) -> str:
    """Generate XML response for a captured DTMF digit."""
    action = config.keypad_mapping.get(digits.strip(), config.fallback_action)
    logger.info(f"[IVR Engine] Handling digit '{digits}' -> Action: {action}")

    if action == "human_transfer":
        if config.human_route.enabled and config.human_route.phone_number:
            hold_tag = _render_prompt_tag(
                "Transferring your call to our support team. Please hold.", provider=provider
            )
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"    {hold_tag}\n"
                f'    <Dial timeout="{config.human_route.timeout_seconds}">{config.human_route.phone_number}</Dial>\n'
                "</Response>"
            )
        else:
            unavail_tag = _render_prompt_tag(
                "Human support is currently unavailable. Connecting you to our AI specialist.",
                provider=provider,
            )
            return (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f"    {unavail_tag}\n"
                f'    <Dial><Sip>{sip_inbound_uri}</Sip></Dial>\n'
                "</Response>"
            )

    # Default or "ai_agent"
    connect_tag = _render_prompt_tag(
        "Connecting to your AI specialist now.", provider=provider
    )
    if sip_inbound_uri:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<Response>\n"
            f"    {connect_tag}\n"
            f'    <Dial><Sip>{sip_inbound_uri}</Sip></Dial>\n'
            "</Response>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        f"    {connect_tag}\n"
        "</Response>"
    )
