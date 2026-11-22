"""Smart DID & Campaign Safety Controller.

Manages DID inventory, capacity-aware selection, number health monitoring,
technical cooldown, carrier quarantine, and immutable per-call snapshots.
Enforces that caller IDs are never rotated to bypass spam flags or customer blocks.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from api.services.telephony.compliance import normalize_phone_number


class NumberHealthState(str, Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    QUARANTINE = "quarantine"


@dataclass
class DIDHealthMetrics:
    phone_number: str
    organization_id: int
    state: NumberHealthState = NumberHealthState.ACTIVE
    consecutive_failures: int = 0
    active_calls: int = 0
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    last_failure_reason: Optional[str] = None
    last_failure_code: Optional[str] = None
    cooldown_until: Optional[float] = None
    quarantine_reason: Optional[str] = None
    quarantined_at: Optional[float] = None
    carrier: str = "voicelink"
    dlt_header_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def is_in_cooldown(self) -> bool:
        if self.state != NumberHealthState.COOLDOWN:
            return False
        if self.cooldown_until is None:
            return False
        if time.time() >= self.cooldown_until:
            # Cooldown period elapsed
            self.state = NumberHealthState.ACTIVE
            self.consecutive_failures = 0
            self.cooldown_until = None
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phone_number": self.phone_number,
            "organization_id": self.organization_id,
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "active_calls": self.active_calls,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "last_failure_reason": self.last_failure_reason,
            "last_failure_code": self.last_failure_code,
            "cooldown_remaining_seconds": max(0.0, (self.cooldown_until or 0) - time.time())
            if self.state == NumberHealthState.COOLDOWN
            else 0.0,
            "quarantine_reason": self.quarantine_reason,
            "carrier": self.carrier,
            "dlt_header_id": self.dlt_header_id,
        }


class SmartDIDController:
    """Thread-safe controller for smart DID selection and number health."""

    def __init__(
        self,
        max_consecutive_failures: int = 3,
        cooldown_duration_seconds: float = 300.0,
    ) -> None:
        self._lock = threading.RLock()
        self._max_consecutive_failures = max_consecutive_failures
        self._cooldown_duration_seconds = cooldown_duration_seconds
        # Key: (organization_id, normalized_phone_number)
        self._dids: Dict[Tuple[int, str], DIDHealthMetrics] = {}

    def _get_or_create(
        self, organization_id: int, phone_number: str, carrier: str = "voicelink"
    ) -> DIDHealthMetrics:
        norm = normalize_phone_number(phone_number)
        key = (organization_id, norm)
        if key not in self._dids:
            self._dids[key] = DIDHealthMetrics(
                phone_number=norm,
                organization_id=organization_id,
                carrier=carrier,
            )
        return self._dids[key]

    def register_did(
        self,
        organization_id: int,
        phone_number: str,
        carrier: str = "voicelink",
        dlt_header_id: Optional[str] = None,
    ) -> DIDHealthMetrics:
        """Register or update an authorized DID for an organization."""
        with self._lock:
            did = self._get_or_create(organization_id, phone_number, carrier)
            if dlt_header_id:
                did.dlt_header_id = dlt_header_id
            did.updated_at = time.time()
            return did

    def get_did_health(
        self, organization_id: int, phone_number: str
    ) -> DIDHealthMetrics:
        with self._lock:
            did = self._get_or_create(organization_id, phone_number)
            # Evaluate cooldown expiration
            did.is_in_cooldown()
            return did

    def record_call_start(self, organization_id: int, phone_number: str) -> None:
        with self._lock:
            did = self._get_or_create(organization_id, phone_number)
            did.active_calls += 1
            did.total_calls += 1
            did.updated_at = time.time()

    def record_call_end(
        self,
        organization_id: int,
        phone_number: str,
        success: bool,
        failure_reason: Optional[str] = None,
        status_code: Optional[int | str] = None,
    ) -> None:
        """Record outcome of a call and update number health."""
        with self._lock:
            did = self._get_or_create(organization_id, phone_number)
            did.active_calls = max(0, did.active_calls - 1)
            did.updated_at = time.time()

            if success:
                did.successful_calls += 1
                did.consecutive_failures = 0
                return

            did.failed_calls += 1
            did.consecutive_failures += 1
            did.last_failure_reason = failure_reason
            did.last_failure_code = str(status_code) if status_code is not None else None

            # Check for quarantine triggers (carrier rejections, policy errors, DLT blocks)
            quarantine_keywords = [
                "403",
                "forbidden",
                "603",
                "decline",
                "unregistered_dlt",
                "spam_detected",
                "header_rejected",
                "carrier_blocked",
            ]
            reason_str = f"{failure_reason or ''} {status_code or ''}".lower()
            if any(k in reason_str for k in quarantine_keywords):
                did.state = NumberHealthState.QUARANTINE
                did.quarantine_reason = (
                    f"Carrier policy/regulatory rejection: {failure_reason or status_code}"
                )
                did.quarantined_at = time.time()
                logger.error(
                    f"[SmartDID] DID {did.phone_number} quarantined for org {organization_id}: "
                    f"{did.quarantine_reason}"
                )
                return

            # Check for technical failure cooldown trigger
            if did.consecutive_failures >= self._max_consecutive_failures:
                did.state = NumberHealthState.COOLDOWN
                did.cooldown_until = time.time() + self._cooldown_duration_seconds
                logger.warning(
                    f"[SmartDID] DID {did.phone_number} entered COOLDOWN for org {organization_id} "
                    f"after {did.consecutive_failures} failures. Cooldown duration: {self._cooldown_duration_seconds}s"
                )

    def select_caller_id(
        self,
        organization_id: int,
        available_numbers: List[str],
        preferred_number: Optional[str] = None,
        max_concurrency_per_did: int = 5,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Select an authorized caller-ID using capacity-aware and health-aware rules.

        Rules:
        1. If preferred_number requested:
           - Must be in available_numbers.
           - Must not be in QUARANTINE (fails closed).
           - Must not be in COOLDOWN (fails closed).
           - Must have capacity under max_concurrency_per_did.
           - Strictly will NOT silently rotate to another number!
        2. If no preferred_number:
           - Filter available_numbers for healthy ACTIVE numbers.
           - Filter for numbers with active_calls < max_concurrency_per_did.
           - Select the least-loaded healthy number.
           - If none available, fail closed.
        """
        with self._lock:
            if not available_numbers:
                return None, "No authorized phone numbers configured for provider"

            normalized_pool = [normalize_phone_number(n) for n in available_numbers]

            if preferred_number:
                pref_norm = normalize_phone_number(preferred_number)
                if pref_norm not in normalized_pool:
                    return None, f"Requested caller-ID {pref_norm} is not in authorized pool"

                did = self._get_or_create(organization_id, pref_norm)
                if did.state == NumberHealthState.QUARANTINE:
                    return (
                        None,
                        f"Requested caller-ID {pref_norm} is QUARANTINED: {did.quarantine_reason}",
                    )
                if did.is_in_cooldown():
                    rem = int((did.cooldown_until or time.time()) - time.time())
                    return (
                        None,
                        f"Requested caller-ID {pref_norm} is in COOLDOWN ({rem}s remaining)",
                    )
                if did.active_calls >= max_concurrency_per_did:
                    return (
                        None,
                        f"Requested caller-ID {pref_norm} capacity exceeded ({did.active_calls}/{max_concurrency_per_did} active calls)",
                    )
                return pref_norm, None

            # Autonomous pool selection: find healthy candidates
            candidates: List[Tuple[int, DIDHealthMetrics]] = []
            for num in normalized_pool:
                did = self._get_or_create(organization_id, num)
                if did.state == NumberHealthState.QUARANTINE:
                    continue
                if did.is_in_cooldown():
                    continue
                if did.active_calls >= max_concurrency_per_did:
                    continue
                candidates.append((did.active_calls, did))

            if not candidates:
                return None, "All authorized caller-IDs are either cooling down, quarantined, or at maximum capacity"

            # Sort by least active calls first (load balancing)
            candidates.sort(key=lambda x: x[0])
            chosen_did = candidates[0][1]
            return chosen_did.phone_number, None

    def reset_did_health(self, organization_id: int, phone_number: str) -> bool:
        """Admin recovery function to clear quarantine and cooldown."""
        with self._lock:
            norm = normalize_phone_number(phone_number)
            key = (organization_id, norm)
            if key in self._dids:
                did = self._dids[key]
                did.state = NumberHealthState.ACTIVE
                did.consecutive_failures = 0
                did.cooldown_until = None
                did.quarantine_reason = None
                did.quarantined_at = None
                logger.info(f"[SmartDID] Admin reset health for DID {norm} (org {organization_id})")
                return True
            return False

    def get_all_dids(self) -> Dict[str, DIDHealthMetrics]:
        with self._lock:
            return {f"{k[0]}:{k[1]}": v for k, v in self._dids.items()}

    def get_dids_for_organization(self, organization_id: int) -> List[DIDHealthMetrics]:
        with self._lock:
            return [v for k, v in self._dids.items() if k[0] == organization_id]

    def create_telephony_snapshot(
        self,
        *,
        provider: str,
        caller_number: str,
        called_number: str,
        telephony_configuration_id: Optional[int],
        sip_host: Optional[str] = None,
        sip_port: Optional[int] = None,
        account_id: Optional[str] = None,
        livekit_sip_trunk_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create an immutable snapshot of call configuration for auditing and failover tracking."""
        snapshot = {
            "provider": provider,
            "caller_number": normalize_phone_number(caller_number),
            "called_number": normalize_phone_number(called_number),
            "telephony_configuration_id": telephony_configuration_id,
            "sip_host": sip_host,
            "sip_port": sip_port or 5060,
            "account_id": account_id,
            "livekit_sip_trunk_id": livekit_sip_trunk_id,
            "fail_closed_enabled": True,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if extra:
            snapshot.update(extra)
        return snapshot

    def clear(self) -> None:
        """Clear all metrics (for testing)."""
        with self._lock:
            self._dids.clear()

    async def sync_health_to_db(self, organization_id: int, phone_number: str) -> None:
        """Persists current DID health metrics to PostgreSQL."""
        did = self.get_did_health(organization_id, phone_number)
        try:
            from api.db import db_client
            from api.db.models import TelephonyNumberHealthModel
            from sqlalchemy.future import select

            async with db_client.async_session() as session:
                stmt = select(TelephonyNumberHealthModel).where(
                    TelephonyNumberHealthModel.organization_id == organization_id,
                    TelephonyNumberHealthModel.phone_number == did.phone_number,
                )
                res = await session.execute(stmt)
                row = res.scalar_one_or_none()
                cooldown_dt = (
                    datetime.fromtimestamp(did.cooldown_until, tz=UTC)
                    if did.cooldown_until
                    else None
                )
                quarantine_dt = (
                    datetime.fromtimestamp(did.quarantined_at, tz=UTC)
                    if did.quarantined_at
                    else None
                )

                if row:
                    row.state = did.state.value
                    row.consecutive_failures = did.consecutive_failures
                    row.active_calls = did.active_calls
                    row.total_calls = did.total_calls
                    row.successful_calls = did.successful_calls
                    row.failed_calls = did.failed_calls
                    row.last_failure_reason = did.last_failure_reason
                    row.last_failure_code = did.last_failure_code
                    row.cooldown_until = cooldown_dt
                    row.quarantine_reason = did.quarantine_reason
                    row.quarantined_at = quarantine_dt
                    row.carrier = did.carrier
                    row.dlt_header_id = did.dlt_header_id
                else:
                    new_row = TelephonyNumberHealthModel(
                        phone_number=did.phone_number,
                        organization_id=organization_id,
                        state=did.state.value,
                        consecutive_failures=did.consecutive_failures,
                        active_calls=did.active_calls,
                        total_calls=did.total_calls,
                        successful_calls=did.successful_calls,
                        failed_calls=did.failed_calls,
                        last_failure_reason=did.last_failure_reason,
                        last_failure_code=did.last_failure_code,
                        cooldown_until=cooldown_dt,
                        quarantine_reason=did.quarantine_reason,
                        quarantined_at=quarantine_dt,
                        carrier=did.carrier,
                        dlt_header_id=did.dlt_header_id,
                    )
                    session.add(new_row)
                await session.commit()
                logger.info(f"[SmartDID DB] Persisted health for {did.phone_number} (state={did.state.value})")
        except Exception as e:
            logger.warning(f"[SmartDID DB] Failed to persist DID health: {e}")

    async def load_health_from_db(self, organization_id: int) -> None:
        """Loads persistent DID health metrics from PostgreSQL at startup."""
        try:
            from api.db import db_client
            from api.db.models import TelephonyNumberHealthModel
            from sqlalchemy.future import select

            async with db_client.async_session() as session:
                stmt = select(TelephonyNumberHealthModel).where(
                    TelephonyNumberHealthModel.organization_id == organization_id
                )
                res = await session.execute(stmt)
                rows = res.scalars().all()
                with self._lock:
                    for r in rows:
                        key = (r.organization_id, r.phone_number)
                        self._dids[key] = DIDHealthMetrics(
                            phone_number=r.phone_number,
                            organization_id=r.organization_id,
                            state=NumberHealthState(r.state) if r.state in [s.value for s in NumberHealthState] else NumberHealthState.ACTIVE,
                            consecutive_failures=r.consecutive_failures,
                            active_calls=r.active_calls,
                            total_calls=r.total_calls,
                            successful_calls=r.successful_calls,
                            failed_calls=r.failed_calls,
                            last_failure_reason=r.last_failure_reason,
                            last_failure_code=r.last_failure_code,
                            cooldown_until=r.cooldown_until.timestamp() if r.cooldown_until else None,
                            quarantine_reason=r.quarantine_reason,
                            quarantined_at=r.quarantined_at.timestamp() if r.quarantined_at else None,
                            carrier=r.carrier,
                            dlt_header_id=r.dlt_header_id,
                        )
                logger.info(f"[SmartDID DB] Loaded {len(rows)} DID health records from DB for org {organization_id}")
        except Exception as e:
            logger.warning(f"[SmartDID DB] Failed to load DID health from DB: {e}")

# Global singleton instance
smart_did_controller = SmartDIDController()


def get_smart_did_controller() -> SmartDIDController:
    return smart_did_controller
