"""Global customer consent, suppression, and DNC (Do Not Call) controller.

Maintains cross-provider compliance records for SPX Voice, ensuring that
customer suppression and consent preferences are enforced consistently
across VoiceLink, Vobiz, and all secondary carriers.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


def normalize_phone_number(raw_number: str) -> str:
    """Normalize phone numbers to standard E.164-like string."""
    cleaned = re.sub(r"[\s\-\(\)\.]", "", str(raw_number).strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif not cleaned.startswith("+"):
        # If 10 digits (India domestic), prepend +91
        if len(cleaned) == 10 and cleaned.isdigit():
            cleaned = "+91" + cleaned
        elif len(cleaned) == 11 and cleaned.startswith("0") and cleaned[1:].isdigit():
            cleaned = "+91" + cleaned[1:]
        elif len(cleaned) == 12 and cleaned.startswith("91") and cleaned.isdigit():
            cleaned = "+" + cleaned
        else:
            cleaned = "+" + cleaned
    return cleaned


@dataclass(frozen=True)
class DNCRecord:
    phone_number: str
    organization_id: int
    reason: str
    source: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: Optional[datetime] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        if self.expires_at:
            d["expires_at"] = self.expires_at.isoformat()
        return d


@dataclass(frozen=True)
class ConsentRecord:
    phone_number: str
    organization_id: int
    consent_type: str  # e.g. "transactional", "promotional", "explicit_opt_in"
    source: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d


class GlobalComplianceManager:
    """Thread-safe global manager for DNC, customer suppression, and consent."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Key: (organization_id, normalized_number)
        # Note: organization_id = 0 represents global / multi-tenant suppression
        self._dnc_registry: Dict[Tuple[int, str], DNCRecord] = {}
        self._consent_registry: Dict[Tuple[int, str], ConsentRecord] = {}

    def add_to_dnc(
        self,
        organization_id: int,
        phone_number: str,
        reason: str,
        source: str = "manual",
        expires_at: Optional[datetime] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> DNCRecord:
        norm = normalize_phone_number(phone_number)
        record = DNCRecord(
            phone_number=norm,
            organization_id=organization_id,
            reason=reason,
            source=source,
            expires_at=expires_at,
            evidence=evidence or {},
        )
        with self._lock:
            self._dnc_registry[(organization_id, norm)] = record
            logger.info(
                f"[Compliance] Added {norm} to DNC for org {organization_id}: {reason} (source: {source})"
            )
        return record

    def remove_from_dnc(self, organization_id: int, phone_number: str) -> bool:
        norm = normalize_phone_number(phone_number)
        with self._lock:
            key = (organization_id, norm)
            if key in self._dnc_registry:
                del self._dnc_registry[key]
                logger.info(f"[Compliance] Removed {norm} from DNC for org {organization_id}")
                return True
            return False

    def is_suppressed(
        self, organization_id: int, phone_number: str
    ) -> Tuple[bool, Optional[str]]:
        norm = normalize_phone_number(phone_number)
        with self._lock:
            # Check org-specific DNC
            record = self._dnc_registry.get((organization_id, norm))
            # Check global DNC (org 0)
            if not record:
                record = self._dnc_registry.get((0, norm))

            if record:
                if record.is_expired():
                    # Evict expired record
                    del self._dnc_registry[(record.organization_id, norm)]
                    return False, None
                return True, f"DNC list match: {record.reason} (source: {record.source})"

            return False, None

    def record_consent(
        self,
        organization_id: int,
        phone_number: str,
        consent_type: str,
        source: str = "explicit_opt_in",
        evidence: Optional[Dict[str, Any]] = None,
    ) -> ConsentRecord:
        norm = normalize_phone_number(phone_number)
        record = ConsentRecord(
            phone_number=norm,
            organization_id=organization_id,
            consent_type=consent_type,
            source=source,
            evidence=evidence or {},
        )
        with self._lock:
            self._consent_registry[(organization_id, norm)] = record
            logger.info(
                f"[Compliance] Recorded consent for {norm} (org {organization_id}, type={consent_type})"
            )
        return record

    def revoke_consent(self, organization_id: int, phone_number: str) -> bool:
        norm = normalize_phone_number(phone_number)
        with self._lock:
            key = (organization_id, norm)
            if key in self._consent_registry:
                del self._consent_registry[key]
                logger.info(f"[Compliance] Revoked consent for {norm} (org {organization_id})")
                return True
            return False

    def has_consent(self, organization_id: int, phone_number: str) -> bool:
        norm = normalize_phone_number(phone_number)
        with self._lock:
            return (organization_id, norm) in self._consent_registry

    def check_call_allowed(
        self,
        organization_id: int,
        phone_number: str,
        require_explicit_consent: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Verify whether an outbound call to phone_number is permitted.

        Fails closed:
        1. If number is on DNC/suppression list -> DISALLOWED.
        2. If require_explicit_consent is True and no consent record exists -> DISALLOWED.
        """
        suppressed, reason = self.is_suppressed(organization_id, phone_number)
        if suppressed:
            return False, f"Call suppressed by compliance policy: {reason}"

        if require_explicit_consent and not self.has_consent(organization_id, phone_number):
            return False, "Call suppressed: explicit customer consent required but not recorded"

        return True, None

    def get_suppression_list(self, organization_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                rec.to_dict()
                for (org_id, _), rec in self._dnc_registry.items()
                if org_id in (organization_id, 0) and not rec.is_expired()
            ]

    def clear(self) -> None:
        """Clear all records (intended for test environments)."""
        with self._lock:
            self._dnc_registry.clear()
            self._consent_registry.clear()

    async def add_to_dnc_db(
        self,
        organization_id: int,
        phone_number: str,
        reason: str,
        source: str = "manual",
        expires_at: Optional[datetime] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> DNCRecord:
        """Adds record to in-memory cache and persists to PostgreSQL."""
        record = self.add_to_dnc(
            organization_id=organization_id,
            phone_number=phone_number,
            reason=reason,
            source=source,
            expires_at=expires_at,
            evidence=evidence,
        )
        try:
            from api.db import db_client
            from api.db.models import CustomerSuppressionDNCModel
            from sqlalchemy.future import select

            async with db_client.async_session() as session:
                stmt = select(CustomerSuppressionDNCModel).where(
                    CustomerSuppressionDNCModel.organization_id == organization_id,
                    CustomerSuppressionDNCModel.phone_number == record.phone_number,
                )
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                if existing:
                    existing.reason = reason
                    existing.source = source
                    existing.expires_at = expires_at
                    existing.evidence = evidence or {}
                else:
                    new_entry = CustomerSuppressionDNCModel(
                        phone_number=record.phone_number,
                        organization_id=organization_id,
                        reason=reason,
                        source=source,
                        expires_at=expires_at,
                        evidence=evidence or {},
                    )
                    session.add(new_entry)
                await session.commit()
                logger.info(f"[Compliance DB] Persisted DNC record for {record.phone_number}")
        except Exception as e:
            logger.warning(f"[Compliance DB] Failed to persist DNC record: {e}")
        return record

    async def is_suppressed_db(
        self, organization_id: int, phone_number: str
    ) -> Tuple[bool, Optional[str]]:
        """Checks in-memory cache and falls back to PostgreSQL."""
        suppressed, reason = self.is_suppressed(organization_id, phone_number)
        if suppressed:
            return True, reason

        norm = normalize_phone_number(phone_number)
        try:
            from api.db import db_client
            from api.db.models import CustomerSuppressionDNCModel
            from sqlalchemy.future import select

            async with db_client.async_session() as session:
                stmt = select(CustomerSuppressionDNCModel).where(
                    CustomerSuppressionDNCModel.phone_number == norm,
                    CustomerSuppressionDNCModel.organization_id.in_([organization_id, 0]),
                )
                res = await session.execute(stmt)
                record = res.scalars().first()
                if record:
                    if record.expires_at and datetime.now(UTC) > record.expires_at:
                        return False, None
                    self.add_to_dnc(
                        organization_id=record.organization_id,
                        phone_number=record.phone_number,
                        reason=record.reason,
                        source=record.source,
                        expires_at=record.expires_at,
                        evidence=record.evidence,
                    )
                    return True, f"DNC list match: {record.reason} (source: {record.source})"
        except Exception as e:
            logger.error(
                f"[Compliance] DNC DB check failed for {norm} org {organization_id}: "
                f"[{type(e).__name__}]. Failing closed — treating as suppressed."
            )
            return True, f"dnc_check_unavailable: database error [{type(e).__name__}]"

        return False, None

# Global singleton instance
compliance_manager = GlobalComplianceManager()
