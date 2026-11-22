"""VoiceLink telephony provider package."""

from typing import Any, Dict

from api.services.telephony.registry import (
    ProviderSpec,
    ProviderUIField,
    ProviderUIMetadata,
    register,
)

from .config import VoiceLinkConfigurationRequest, VoiceLinkConfigurationResponse
from .provider import VoiceLinkProvider
from .transport import create_transport


def _config_loader(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "provider": "voicelink",
        "account_id": value.get("account_id"),
        "sip_host": value.get("sip_host", "sip.voicelink.co.in"),
        "sip_port": int(value.get("sip_port", 5060)),
        "sip_username": value.get("sip_username"),
        "sip_password": value.get("sip_password"),
        "webhook_secret": value.get("webhook_secret"),
        "channel_capacity": int(value.get("channel_capacity", 10)),
        "cps_limit": float(value.get("cps_limit", 2.0)),
        "livekit_sip_trunk_id": value.get("livekit_sip_trunk_id"),
        "from_numbers": value.get("from_numbers", []),
    }


_UI_METADATA = ProviderUIMetadata(
    display_name="VoiceLink (India VNO)",
    docs_url="https://voicelink.co.in",
    fields=[
        ProviderUIField(
            name="account_id",
            label="Account ID",
            type="text",
            sensitive=True,
            description="VoiceLink Customer Account ID",
        ),
        ProviderUIField(
            name="sip_host",
            label="SIP Host",
            type="text",
            required=True,
            placeholder="sip.voicelink.co.in",
            description="VoiceLink SIP Gateway FQDN or IP address",
        ),
        ProviderUIField(
            name="sip_port",
            label="SIP Port",
            type="number",
            required=True,
            placeholder="5060",
            description="Signaling port (typically 5060 or 3300)",
        ),
        ProviderUIField(
            name="sip_username",
            label="SIP Username",
            type="text",
            required=True,
            description="SIP Trunk Authentication Username",
        ),
        ProviderUIField(
            name="sip_password",
            label="SIP Password",
            type="password",
            required=True,
            sensitive=True,
            description="SIP Trunk Authentication Password",
        ),
        ProviderUIField(
            name="from_numbers",
            label="Registered DLT Numbers",
            type="string-array",
            required=True,
            description="List of verified DLT phone numbers (140/160 series or +91...)",
        ),
        ProviderUIField(
            name="livekit_sip_trunk_id",
            label="LiveKit SIP Trunk ID",
            type="text",
            required=False,
            description="LiveKit SIP Outbound Trunk ID mapped to VoiceLink",
        ),
        ProviderUIField(
            name="channel_capacity",
            label="Concurrent Channels",
            type="number",
            required=False,
            placeholder="10",
            description="Account channel concurrency limit",
        ),
        ProviderUIField(
            name="cps_limit",
            label="Calls Per Second (CPS)",
            type="number",
            required=False,
            placeholder="2.0",
            description="CPS rate limit",
        ),
        ProviderUIField(
            name="webhook_secret",
            label="Webhook Secret",
            type="password",
            required=False,
            sensitive=True,
            description="Shared secret for validating VoiceLink status callbacks",
        ),
    ],
)


SPEC = ProviderSpec(
    name="voicelink",
    provider_cls=VoiceLinkProvider,
    config_loader=_config_loader,
    transport_factory=create_transport,
    transport_sample_rate=8000,
    config_request_cls=VoiceLinkConfigurationRequest,
    ui_metadata=_UI_METADATA,
    config_response_cls=VoiceLinkConfigurationResponse,
    account_id_credential_field="account_id",
)


register(SPEC)


__all__ = [
    "SPEC",
    "VoiceLinkConfigurationRequest",
    "VoiceLinkConfigurationResponse",
    "VoiceLinkProvider",
    "create_transport",
]

