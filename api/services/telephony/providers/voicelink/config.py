"""VoiceLink telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class VoiceLinkConfigurationRequest(BaseModel):
    """Request schema for VoiceLink telephony configuration."""

    provider: Literal["voicelink"] = Field(default="voicelink")
    account_id: str = Field(..., description="VoiceLink Customer Account ID")
    sip_host: str = Field(
        default="sip.voicelink.co.in",
        description="VoiceLink SIP Gateway Host (FQDN or IP)",
    )
    sip_port: int = Field(
        default=5060,
        description="VoiceLink SIP Signaling Port (typically 5060 or 3300)",
    )
    sip_username: str = Field(..., description="VoiceLink SIP Trunk Username")
    sip_password: str = Field(..., description="VoiceLink SIP Trunk Password")
    from_numbers: List[str] = Field(
        default_factory=list,
        description="List of verified DLT-registered VoiceLink phone numbers",
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Optional shared secret for authenticating VoiceLink webhook callbacks",
    )
    channel_capacity: int = Field(
        default=10,
        description="Maximum concurrent channels allocated to this VoiceLink account",
    )
    cps_limit: float = Field(
        default=2.0,
        description="Calls per second (CPS) rate limit for outbound dialing",
    )
    livekit_sip_trunk_id: Optional[str] = Field(
        default=None,
        description="LiveKit SIP Outbound Trunk ID mapped to this VoiceLink trunk",
    )

    @field_validator("sip_host")
    @classmethod
    def _validate_sip_host(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("sip_host cannot be empty")
        return v

    @field_validator("sip_port")
    @classmethod
    def _validate_sip_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("sip_port must be between 1 and 65535")
        return v

    @field_validator("account_id")
    @classmethod
    def _validate_account_id(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("account_id cannot be empty")
        return v

    @field_validator("channel_capacity")
    @classmethod
    def _validate_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("channel_capacity must be at least 1")
        return v


class VoiceLinkConfigurationResponse(BaseModel):
    """Response schema for VoiceLink configuration with sensitive fields masked."""

    provider: Literal["voicelink"] = Field(default="voicelink")
    account_id: str
    sip_host: str
    sip_port: int
    sip_username: str
    sip_password: str  # Masked (e.g. "****************1234")
    from_numbers: List[str]
    webhook_secret: Optional[str] = None  # Masked
    channel_capacity: int
    cps_limit: float
    livekit_sip_trunk_id: Optional[str] = None

