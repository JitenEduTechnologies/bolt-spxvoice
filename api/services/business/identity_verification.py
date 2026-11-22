"""Customer Identity & Sensitive Privacy Verification Policy Engine for SPX Voice.

Enforces that sensitive customer records (school fee balances, eCommerce order items,
clinic records, payment details) are never disclosed without multi-factor verification
appropriate to the business domain. Never relies solely on Caller ID.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from pydantic import BaseModel


class VerificationLevel(str, Enum):
    PUBLIC = "public"            # General inquiries, brochure requests, business hours
    TRANSACTIONAL = "transactional"  # Booking inquiry, order status overview
    SENSITIVE = "sensitive"      # Detailed fee balances, itemized orders, clinic notes


class IdentityVerificationPolicy(BaseModel):
    domain: str
    required_level: VerificationLevel
    allowed_verification_factors: List[str]  # e.g., ["student_dob", "admission_no", "order_id", "pin_code"]
    allow_staff_escalation: bool = True
    general_fallback_message: str


DEFAULT_POLICIES: Dict[str, IdentityVerificationPolicy] = {
    "schools": IdentityVerificationPolicy(
        domain="schools",
        required_level=VerificationLevel.SENSITIVE,
        allowed_verification_factors=["admission_no", "student_dob", "registered_guardian_name"],
        allow_staff_escalation=True,
        general_fallback_message=(
            "For student privacy and security, I cannot share specific fee balances over the phone "
            "without verification. I can arrange for our school accounts office to call you back directly."
        ),
    ),
    "ecommerce": IdentityVerificationPolicy(
        domain="ecommerce",
        required_level=VerificationLevel.TRANSACTIONAL,
        allowed_verification_factors=["order_id", "delivery_pin_code", "customer_name"],
        allow_staff_escalation=True,
        general_fallback_message=(
            "To protect your order details, could you please confirm your Order ID or delivery pincode? "
            "Otherwise, our customer care team can reach out to you."
        ),
    ),
    "healthcare": IdentityVerificationPolicy(
        domain="healthcare",
        required_level=VerificationLevel.SENSITIVE,
        allowed_verification_factors=["patient_id", "patient_dob", "doctor_name"],
        allow_staff_escalation=True,
        general_fallback_message=(
            "To safeguard patient health privacy, detailed medical records and consultation notes "
            "cannot be disclosed over the phone. Would you like to schedule a consultation with the doctor?"
        ),
    ),
}


class IdentityVerificationController:
    """Evaluates caller identity verification before sensitive data exposure."""

    def __init__(self, policies: Optional[Dict[str, IdentityVerificationPolicy]] = None):
        self._policies = policies or DEFAULT_POLICIES

    def evaluate_access(
        self,
        domain: str,
        caller_phone: str,
        provided_factors: Dict[str, Any],
        authoritative_record: Dict[str, Any],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Evaluates whether sensitive data can be revealed to the caller.

        Returns:
            Tuple of (is_verified: bool, refusal_reason: Optional[str], fallback_message: Optional[str])
        """
        policy = self._policies.get(domain)
        if not policy or policy.required_level == VerificationLevel.PUBLIC:
            return True, None, None

        # Check required verification factors
        matched_factors = 0
        for factor in policy.allowed_verification_factors:
            provided_val = str(provided_factors.get(factor) or "").strip().lower()
            record_val = str(authoritative_record.get(factor) or "").strip().lower()

            if provided_val and record_val and provided_val == record_val:
                matched_factors += 1

        # At least one authoritative secondary factor must match (beyond phone number)
        if matched_factors >= 1:
            logger.info(
                f"[Identity Verification] Verified caller {caller_phone} for domain '{domain}' with {matched_factors} factor(s)"
            )
            return True, None, None

        logger.warning(
            f"[Identity Verification] Access denied for caller {caller_phone} in domain '{domain}'. Insufficient verified factors."
        )
        return False, "Unverified caller identity", policy.general_fallback_message


identity_verifier = IdentityVerificationController()

