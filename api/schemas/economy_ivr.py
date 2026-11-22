"""Schemas for SPX Voice Economy IVR Mode.

Defines the configuration contract for customizable, low-cost IVR:
- Prerecorded owner greetings
- Optional keypad menu (1-9)
- Business actions: callback, appointment request, order enquiry,
  delivery enquiry, fee enquiry, general enquiry, custom action, hangup
- Language-specific audio mappings (e.g. Telugu, Hindi, English)
- Retry limits, timeouts, and automatic hang-up
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class IVRActionType(str, Enum):
    CALLBACK = "callback"
    APPOINTMENT_REQUEST = "appointment_request"
    ORDER_ENQUIRY = "order_enquiry"
    DELIVERY_ENQUIRY = "delivery_enquiry"
    FEE_ENQUIRY = "fee_enquiry"
    GENERAL_ENQUIRY = "general_enquiry"
    CUSTOM_ACTION = "custom_action"
    HUMAN_TRANSFER = "human_transfer"
    HANGUP = "hangup"


class KeypadAction(BaseModel):
    """Configuration for a single DTMF keypad button (1-9)."""

    key: str = Field(..., description="Keypad digit '1' through '9'")
    action_type: IVRActionType = Field(..., description="Action to perform when digit is pressed")
    label: str = Field(default="", description="Human-readable description for UI")
    confirmation_recording_id: Optional[str] = Field(
        default=None,
        description="Optional audio recording ID to play after action execution",
    )
    action_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional action parameters (e.g. transfer phone number, webhook URL)",
    )

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        v = v.strip()
        if v not in [str(i) for i in range(1, 10)]:
            raise ValueError(f"Key must be a digit from 1 to 9, got '{v}'")
        return v


class EconomyIVRConfig(BaseModel):
    """Universal business configuration for Economy IVR."""

    enabled: bool = Field(default=True, description="Whether Economy IVR mode is active")
    default_language: str = Field(default="en", description="Default language code ('en', 'te', 'hi')")
    
    # Prerecorded audio references (stored in workflow_recordings)
    greeting_recording_id: Optional[str] = Field(
        default="", description="Default prerecorded greeting recording_id"
    )
    language_greetings: Dict[str, str] = Field(
        default_factory=dict,
        description="Optional language-specific greeting recordings (e.g. {'te': 'rec_te_01', 'hi': 'rec_hi_01'})",
    )

    # Keypad menu options (1-9)
    keypad_actions: Dict[str, KeypadAction] = Field(
        default_factory=dict,
        description="Mapping of digit '1'-'9' to KeypadAction",
    )

    # Safety, timeout & retry limits
    timeout_seconds: int = Field(default=8, ge=3, le=30, description="Seconds to wait for DTMF before timeout action")
    max_invalid_retries: int = Field(default=2, ge=0, le=5, description="Max invalid keypad entries before hangup")
    
    invalid_input_recording_id: Optional[str] = Field(
        default=None,
        description="Audio to play when invalid digit is pressed",
    )
    timeout_recording_id: Optional[str] = Field(
        default=None,
        description="Audio to play when no input is received",
    )

    auto_hangup_after_action: bool = Field(
        default=True,
        description="Automatically terminate call after executing action and confirmation",
    )
    allow_barge_in: bool = Field(
        default=True,
        description="Allow caller to press digit while greeting audio is playing",
    )

    @field_validator("keypad_actions")
    @classmethod
    def validate_keypad_actions(cls, v: Dict[str, KeypadAction]) -> Dict[str, KeypadAction]:
        for k, action in v.items():
            if str(k) not in [str(i) for i in range(1, 10)]:
                raise ValueError(f"Keypad mapping keys must be '1'-'9', got '{k}'")
            if action.key != str(k):
                action.key = str(k)
        return v


class EconomyIVRCallContext(BaseModel):
    """Runtime context for an active Economy IVR call."""

    call_id: str
    channel_id: str
    organization_id: int
    workflow_id: int
    workflow_run_id: int
    direction: str  # "inbound" | "outbound"
    caller_number: str
    called_number: str
    language: str = "en"
    digits_entered: List[str] = Field(default_factory=list)
    invalid_attempts: int = 0
    executed_action: Optional[str] = None
    action_result: Optional[Dict[str, Any]] = None
    call_start_time: float = Field(default=0.0)
    call_end_time: Optional[float] = None
    duration_seconds: float = 0.0
    billed_pulses: int = 0

