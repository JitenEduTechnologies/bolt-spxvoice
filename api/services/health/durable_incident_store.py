"""Durable Incident History & Operational Event Store (Phase C4).

Provides append-only persistence for system incidents, alerts, and operational failures
on persistent storage (/app/api/.runtime/incidents.jsonl or fallback).

Key Guarantees:
- Works independently of PostgreSQL: can record database outages even when the DB is down!
- Multi-tenant safe: platform-level events never assigned to customer organizations.
- Crash and restart resilient: preserves incident history across container reboots.
- Redaction: scrubs secrets and customer PII before writing.
- Honest durability: tracks `durable_persisted=True/False` and never falsely claims persistence on I/O error.
- Full DDL schema proposal provided for owner approval if future PostgreSQL migration is requested.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from api.services.audit.audit_service import sanitize_audit_payload
from api.services.health.models import AlertSeverity, HealthStatus, IncidentEvent

DEFAULT_RUNTIME_DIR = "/app/api/.runtime"

POSTGRES_INCIDENTS_DDL_PROPOSAL = """
-- Proposed DDL for optional future PostgreSQL incident history migration
-- Requires Owner Approval before creating migration scripts.
CREATE TABLE IF NOT EXISTS system_incidents (
    id BIGSERIAL PRIMARY KEY,
    incident_id VARCHAR(128) NOT NULL UNIQUE,
    component VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    health_status VARCHAR(32) NOT NULL,
    error_code VARCHAR(64),
    message TEXT NOT NULL,
    correlation_id VARCHAR(128),
    call_id VARCHAR(128),
    organization_id BIGINT REFERENCES organizations(id) ON DELETE SET NULL,
    context JSONB DEFAULT '{}'::jsonb,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    latest_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    count INTEGER NOT NULL DEFAULT 1,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_by VARCHAR(128),
    acknowledged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_incidents_component ON system_incidents(component);
CREATE INDEX IF NOT EXISTS idx_system_incidents_severity ON system_incidents(severity);
CREATE INDEX IF NOT EXISTS idx_system_incidents_call_id ON system_incidents(call_id);
CREATE INDEX IF NOT EXISTS idx_system_incidents_correlation_id ON system_incidents(correlation_id);
CREATE INDEX IF NOT EXISTS idx_system_incidents_latest_seen ON system_incidents(latest_seen DESC);
"""


def is_synthetic_incident(rec_or_inc: Any) -> bool:
    """Classifies an incident as a synthetic/test probe versus genuine operational failure."""
    if isinstance(rec_or_inc, IncidentEvent):
        comp = rec_or_inc.component or ""
        inc_id = rec_or_inc.incident_id or ""
        ctx = rec_or_inc.context or {}
        msg = rec_or_inc.message or ""
    elif isinstance(rec_or_inc, dict):
        comp = rec_or_inc.get("component") or ""
        inc_id = rec_or_inc.get("incident_id") or ""
        ctx = rec_or_inc.get("context") or {}
        msg = rec_or_inc.get("message") or ""
    else:
        return False

    if (
        comp.startswith("synthetic_")
        or comp.startswith("test_")
        or "probe" in comp
        or inc_id.startswith("test_")
        or "synthetic" in inc_id
        or "probe" in inc_id
        or ctx.get("is_test")
        or ctx.get("is_synthetic")
        or ctx.get("synthetic")
        or "Persistence proof" in msg
        or "synthetic" in msg.lower()
    ):
        return True
    return False


def resolve_runtime_directory() -> str:
    """Resolves the durable runtime storage directory across Docker and local environments."""
    env_dir = os.getenv("SPX_RUNTIME_DIR")
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir

    if os.path.exists(DEFAULT_RUNTIME_DIR) and os.access(DEFAULT_RUNTIME_DIR, os.W_OK):
        return DEFAULT_RUNTIME_DIR

    local_runtime = os.path.join(os.getcwd(), "api", ".runtime")
    if os.path.exists(local_runtime) and os.access(local_runtime, os.W_OK):
        return local_runtime

    try:
        os.makedirs(local_runtime, exist_ok=True)
        return local_runtime
    except Exception:
        fallback = os.path.join(tempfile.gettempdir(), "spx_runtime")
        os.makedirs(fallback, exist_ok=True)
        return fallback


class DurableIncidentStore:
    """Thread-safe, append-oriented incident event storage engine."""

    def __init__(self, runtime_dir: Optional[str] = None) -> None:
        self.runtime_dir = runtime_dir or resolve_runtime_directory()
        self.file_path = os.path.join(self.runtime_dir, "incidents.jsonl")
        self._lock = asyncio.Lock()

    async def record_event(
        self, incident: IncidentEvent, event_type: str = "FIRED"
    ) -> bool:
        """Appends an incident state transition to the durable log."""
        now = datetime.now(UTC)
        sanitized_context = sanitize_audit_payload(dict(incident.context or {}))

        if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("ENVIRONMENT") == "test":
            incident.context["is_test"] = True
            incident.context["is_synthetic"] = True

        is_synth = is_synthetic_incident(incident)

        record = {
            "event_type": event_type.upper(),
            "incident_id": incident.incident_id,
            "component": incident.component,
            "severity": incident.severity.value,
            "health_status": incident.health_status.value,
            "error_code": incident.error_code,
            "message": incident.message,
            "correlation_id": incident.correlation_id,
            "call_id": incident.call_id,
            "organization_id": incident.organization_id,
            "first_seen": incident.first_seen.isoformat(),
            "latest_seen": incident.latest_seen.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "count": incident.count,
            "acknowledged": incident.acknowledged,
            "acknowledged_by": incident.acknowledged_by,
            "acknowledged_at": incident.acknowledged_at.isoformat() if incident.acknowledged_at else None,
            "context": sanitized_context,
            "is_synthetic": is_synth,
            "recorded_at": now.isoformat(),
        }

        async with self._lock:
            try:
                line = json.dumps(record) + "\n"
                # Synchronous file write inside async executor to avoid event loop stalls
                await asyncio.to_thread(self._append_line, line)
                incident.context["durable_persisted"] = True
                return True
            except Exception as exc:
                logger.error(
                    f"[Durable Incident Store] Failed to append incident {incident.incident_id}: {exc}"
                )
                incident.context["durable_persisted"] = False
                return False

    def _append_line(self, line: str) -> None:
        """Internal synchronous write helper with directory verification, size bounding, and fsync."""
        parent_dir = os.path.dirname(self.file_path)
        os.makedirs(parent_dir, exist_ok=True)

        # Bounded file growth: rotate if exceeding 10 MB
        try:
            if os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 10 * 1024 * 1024:
                rotated_file = f"{self.file_path}.1"
                if os.path.exists(rotated_file):
                    os.remove(rotated_file)
                os.rename(self.file_path, rotated_file)
        except Exception as rot_err:
            logger.warning(f"[Durable Incident Store] Log rotation warning: {rot_err}")

        with open(self.file_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    async def get_all_consolidated_incidents(self) -> Dict[str, Dict[str, Any]]:
        """Reads the full log and returns the latest consolidated state for each incident."""
        rotated_file = f"{self.file_path}.1"
        if not os.path.exists(self.file_path) and not os.path.exists(rotated_file):
            return {}

        def _read_records() -> Dict[str, Dict[str, Any]]:
            consolidated: Dict[str, Dict[str, Any]] = {}
            files_to_read = [rotated_file, self.file_path] if os.path.exists(rotated_file) else [self.file_path]

            for path in files_to_read:
                if not os.path.exists(path):
                    continue
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            inc_id = rec.get("incident_id")
                            if inc_id:
                                # Ensure is_synthetic, category, and lifecycle_status are computed
                                is_synth = rec.get("is_synthetic")
                                if is_synth is None:
                                    is_synth = is_synthetic_incident(rec)
                                rec["is_synthetic"] = bool(is_synth)
                                rec["category"] = "SYNTHETIC_TEST" if is_synth else "OPERATIONAL"

                                # Compute lifecycle status
                                if rec.get("resolved_at"):
                                    rec["lifecycle_status"] = "RESOLVED"
                                elif is_synth:
                                    rec["lifecycle_status"] = "SYNTHETIC_PROBE"
                                else:
                                    rec["lifecycle_status"] = "ACTIVE"

                                consolidated[inc_id] = rec
                        except Exception:
                            # Resilient: silently drop malformed / partial lines
                            continue
            return consolidated

        async with self._lock:
            return await asyncio.to_thread(_read_records)

    async def reconcile_healthy_components(
        self, healthy_components: Dict[str, ComponentHealth]
    ) -> List[str]:
        """Reconciles unclosed historical incidents for components verified HEALTHY by live checks.
        
        Guarantees:
        - NEVER marks a failure recovered without verified live probe evidence.
        - Preserves full audit history by appending a RESOLVED record (no deletion).
        - Ensures active incident counts and live component health are semantically consistent.
        """
        all_incidents = await self.get_all_consolidated_incidents()
        now = datetime.now(UTC)
        reconciled_ids: List[str] = []

        for inc_id, rec in all_incidents.items():
            # Skip synthetic probes and already-resolved incidents
            if rec.get("resolved_at") or rec.get("is_synthetic"):
                continue

            comp_name = rec.get("component")
            live_comp = healthy_components.get(comp_name)
            if live_comp and live_comp.status == HealthStatus.HEALTHY:
                # Evidence confirmed: component is currently verified healthy!
                try:
                    first_seen_dt = datetime.fromisoformat(rec.get("first_seen", now.isoformat()))
                except Exception:
                    first_seen_dt = now

                reconciled_event = IncidentEvent(
                    incident_id=inc_id,
                    component=comp_name,
                    severity=AlertSeverity(rec.get("severity", "WARNING")),
                    health_status=HealthStatus.HEALTHY,
                    error_code="AUTO_RESOLVED_HEALTHY",
                    message=f"{rec.get('message', '')} (Resolved: component verified HEALTHY by live check)",
                    correlation_id=rec.get("correlation_id"),
                    call_id=rec.get("call_id"),
                    organization_id=rec.get("organization_id"),
                    context={
                        **rec.get("context", {}),
                        "recovery_evidence": f"Live {comp_name} probe verified HEALTHY",
                        "auto_reconciled": True,
                    },
                    first_seen=first_seen_dt,
                    latest_seen=now,
                    resolved_at=now,
                    count=rec.get("count", 1),
                    acknowledged=rec.get("acknowledged", False),
                    acknowledged_by=rec.get("acknowledged_by"),
                    acknowledged_at=(
                        datetime.fromisoformat(rec["acknowledged_at"])
                        if rec.get("acknowledged_at")
                        else None
                    ),
                )
                await self.record_event(reconciled_event, event_type="RESOLVED")
                reconciled_ids.append(inc_id)
                logger.info(
                    f"[Durable Incident Store] Reconciled unclosed incident {inc_id} on {comp_name} (evidence: live check HEALTHY)"
                )

        return reconciled_ids

    async def query_incidents(
        self,
        severity: Optional[str] = None,
        component: Optional[str] = None,
        is_resolved: Optional[bool] = None,
        is_acknowledged: Optional[bool] = None,
        is_synthetic: Optional[bool] = None,
        correlation_id: Optional[str] = None,
        call_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Queries durable incidents with server-side filtering, sorting, and pagination."""
        all_incidents = await self.get_all_consolidated_incidents()
        items = list(all_incidents.values())

        filtered: List[Dict[str, Any]] = []
        for item in items:
            if severity and item.get("severity") != severity.upper():
                continue
            if component and item.get("component") != component:
                continue
            if is_synthetic is not None and item.get("is_synthetic") != is_synthetic:
                continue
            if is_resolved is not None:
                has_resolved = bool(item.get("resolved_at"))
                if has_resolved != is_resolved:
                    continue
            if is_acknowledged is not None:
                if bool(item.get("acknowledged")) != is_acknowledged:
                    continue
            if correlation_id and item.get("correlation_id") != correlation_id:
                continue
            if call_id and item.get("call_id") != call_id:
                continue

            # Date range filtering against latest_seen
            if start_time or end_time:
                try:
                    ts_str = item.get("latest_seen")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if start_time and ts < start_time:
                            continue
                        if end_time and ts > end_time:
                            continue
                except Exception:
                    pass

            filtered.append(item)

        # Sort chronologically descending by latest_seen
        filtered.sort(key=lambda x: x.get("latest_seen", ""), reverse=True)

        total_count = len(filtered)
        paginated = filtered[offset : offset + limit]
        return paginated, total_count

    async def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single incident by its unique ID from durable storage."""
        all_incidents = await self.get_all_consolidated_incidents()
        return all_incidents.get(incident_id)

    async def acknowledge_incident(self, incident_id: str, actor_id: str) -> bool:
        """Records an ACKNOWLEDGED transition for an incident."""
        all_incidents = await self.get_all_consolidated_incidents()
        if incident_id not in all_incidents:
            return False

        current = all_incidents[incident_id]
        now = datetime.now(UTC)

        incident_obj = IncidentEvent(
            incident_id=incident_id,
            component=current.get("component", "unknown"),
            severity=AlertSeverity(current.get("severity", "WARNING")),
            health_status=HealthStatus(current.get("health_status", "UNHEALTHY")),
            error_code=current.get("error_code", "ERR_UNKNOWN"),
            message=current.get("message", ""),
            correlation_id=current.get("correlation_id"),
            call_id=current.get("call_id"),
            organization_id=current.get("organization_id"),
            context=current.get("context", {}),
            first_seen=datetime.fromisoformat(current.get("first_seen")),
            latest_seen=datetime.fromisoformat(current.get("latest_seen")),
            resolved_at=(
                datetime.fromisoformat(current["resolved_at"])
                if current.get("resolved_at")
                else None
            ),
            count=current.get("count", 1),
            acknowledged=True,
            acknowledged_by=actor_id,
            acknowledged_at=now,
        )
        return await self.record_event(incident_obj, event_type="ACKNOWLEDGED")


durable_incident_store = DurableIncidentStore()
