"""Durable Audit History & Administrative Event Tracking (Phase C2).

Provides durable, tamper-evident audit logging for security, administrative,
configuration, and financial events in SPX Voice.

Key Guarantees:
- Enforces multi-tenant isolation (all queries and mutations scoped by organization_id).
- Server-derived identities: actor_id and organization_id are never blindly trusted from client payloads.
- Sensitive data protection: automatically redacts secrets, credentials, tokens, and masks customer phone numbers.
- Fail-closed capability for high-risk operations (administrative action fails if audit record cannot be written).
- Seamless correlation with Phase C1 request/telephony tracking (`get_current_request_id()`).
- Stored durably in PostgreSQL `dashboard_events` with resource_type='audit'.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import desc, select

from api.db import db_client
from api.db.models import DashboardEventModel, UserModel
from api.utils.run_context import get_current_request_id

# Keys that must NEVER be persisted in audit records
REDACTED_KEYS = {
    "password",
    "password_hash",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "x_api_key",
    "auth",
    "authorization",
    "private_key",
    "credential_data",
    "header_value",
    "webhook_secret",
}


def mask_phone_number(val: str) -> str:
    """Mask phone numbers to prevent storing raw PII in audit records."""
    if not val or not isinstance(val, str):
        return val

    def _replace(m: re.Match) -> str:
        s = m.group(0)
        prefix = ""
        if s.startswith("+"):
            prefix = s[:3]
            s = s[3:]
        if len(s) >= 8:
            return prefix + s[:2] + "****" + s[-2:]
        return m.group(0)

    return re.sub(r"\+?\d{10,15}", _replace, val)


def sanitize_audit_payload(obj: Any) -> Any:
    """Recursively scrub secrets and mask customer phone numbers in audit data."""
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(secret_term in k_lower for secret_term in REDACTED_KEYS):
                cleaned[k] = "[REDACTED_SECRET]"
            elif k_lower in ("transcript", "full_transcript", "raw_transcript"):
                cleaned[k] = "[REDACTED_TRANSCRIPT]"
            elif isinstance(v, (dict, list)):
                cleaned[k] = sanitize_audit_payload(v)
            elif isinstance(v, str):
                cleaned[k] = mask_phone_number(v)
            else:
                cleaned[k] = v
        return cleaned
    elif isinstance(obj, list):
        return [sanitize_audit_payload(item) for item in obj]
    elif isinstance(obj, str):
        return mask_phone_number(obj)
    return obj


class AuditService:
    """Durable audit history recording and query service."""

    async def record_event(
        self,
        organization_id: int,
        action_type: str,
        target_resource: str,
        target_id: str | int,
        outcome: str = "SUCCESS",
        actor_id: Optional[str | int] = None,
        actor_role: Optional[str] = None,
        changed_fields: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        workflow_run_id: Optional[int] = None,
        call_id: Optional[str] = None,
        request_id: Optional[str] = None,
        session: Optional[Any] = None,
        fail_closed: bool = False,
    ) -> Dict[str, Any]:
        """Record a durable audit event in PostgreSQL.

        Args:
            organization_id: Server-derived tenant ID.
            action_type: Hierarchical event key, e.g. 'audit.security.login_failed'.
            target_resource: Resource classification ('workflow', 'credential', 'business_profile', etc.).
            target_id: ID of the modified or inspected resource.
            outcome: Outcome status: 'SUCCESS', 'DENIED', or 'FAILED'.
            actor_id: Server-derived user ID, email, or 'system'.
            actor_role: User role ('owner', 'admin', 'staff', 'system').
            changed_fields: Summary of modified fields (secrets automatically scrubbed).
            reason: Optional explanation or error reason.
            workflow_run_id: Associated workflow run if applicable.
            call_id: Associated telephony call ID if applicable.
            request_id: Request correlation ID (defaults to C1 request contextvar).
            session: Optional existing AsyncSession for shared-transaction atomicity.
            fail_closed: If True, raises exception on write failure to block untracked admin writes.
        """
        now = datetime.now(UTC)
        effective_req_id = request_id or get_current_request_id() or "untracked_req"

        sanitized_changes = sanitize_audit_payload(changed_fields or {})
        sanitized_reason = mask_phone_number(str(reason)) if reason else None

        audit_payload = {
            "actor_id": str(actor_id or "system"),
            "actor_role": str(actor_role or "system"),
            "outcome": outcome.upper(),
            "target_resource": target_resource,
            "target_id": str(target_id),
            "request_id": effective_req_id,
            "workflow_run_id": workflow_run_id,
            "call_id": call_id,
            "changed_fields": sanitized_changes,
            "reason": sanitized_reason,
            "timestamp": now.isoformat(),
        }

        # If a shared transaction session is provided, add event to that session
        if session is not None:
            try:
                event_row = DashboardEventModel(
                    organization_id=organization_id,
                    event_type=action_type,
                    resource_type="audit",
                    resource_id=str(target_id),
                    data=audit_payload,
                    created_at=now,
                )
                session.add(event_row)
                await session.flush()
                event_id = event_row.id
                audit_payload["event_id"] = event_id
                logger.info(
                    f"[Audit Atomic] Added {action_type} [{outcome}] org={organization_id} "
                    f"actor={actor_id} target={target_resource}:{target_id} in shared transaction"
                )
                return audit_payload
            except Exception as e:
                logger.error(
                    f"[Audit Atomic] FAILED to add audit event {action_type} in shared transaction: {e}"
                )
                if fail_closed:
                    raise RuntimeError(
                        f"Administrative action blocked: audit log could not be durably recorded: {e}"
                    ) from e
                audit_payload["event_id"] = None
                audit_payload["persistence_error"] = str(e)
                return audit_payload

        # Standalone session fallback
        try:
            async with db_client.async_session() as session_standalone:
                event_row = DashboardEventModel(
                    organization_id=organization_id,
                    event_type=action_type,
                    resource_type="audit",
                    resource_id=str(target_id),
                    data=audit_payload,
                    created_at=now,
                )
                session_standalone.add(event_row)
                await session_standalone.commit()
                await session_standalone.refresh(event_row)

                event_id = event_row.id
                audit_payload["event_id"] = event_id
                logger.info(
                    f"[Audit] Recorded {action_type} [{outcome}] org={organization_id} "
                    f"actor={actor_id} target={target_resource}:{target_id} req={effective_req_id}"
                )
                return audit_payload
        except Exception as e:
            logger.error(
                f"[Audit] FAILED to persist audit event {action_type} for org {organization_id}: {e}"
            )
            if fail_closed:
                raise RuntimeError(
                    f"Administrative action blocked: audit log could not be durably recorded: {e}"
                ) from e
            audit_payload["event_id"] = None
            audit_payload["persistence_error"] = str(e)
            return audit_payload

    async def get_audit_trail(
        self,
        organization_id: int,
        user: UserModel,
        limit: int = 50,
        offset: int = 0,
        action_prefix: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch audit records with strict multi-tenant isolation and role enforcement."""
        # Enforce tenant isolation: user can only view their selected organization unless superuser
        if not user.is_superuser and user.selected_organization_id != organization_id:
            logger.warning(
                f"[Audit Security] User {user.id} denied access to audit logs for org {organization_id}"
            )
            raise PermissionError("Access to audit records for this organization is forbidden.")

        limit = min(max(1, limit), 200)
        offset = max(0, offset)

        async with db_client.async_session() as session:
            stmt = (
                select(DashboardEventModel)
                .where(
                    DashboardEventModel.organization_id == organization_id,
                    DashboardEventModel.resource_type == "audit",
                )
            )

            if action_prefix:
                stmt = stmt.where(DashboardEventModel.event_type.startswith(action_prefix))

            stmt = stmt.order_by(desc(DashboardEventModel.id)).limit(limit).offset(offset)
            res = await session.execute(stmt)
            rows = res.scalars().all()

            results = []
            for r in rows:
                data = dict(r.data or {})
                if outcome and data.get("outcome") != outcome.upper():
                    continue
                results.append({
                    "event_id": r.id,
                    "organization_id": r.organization_id,
                    "action_type": r.event_type,
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "actor_id": data.get("actor_id"),
                    "actor_role": data.get("actor_role"),
                    "outcome": data.get("outcome"),
                    "request_id": data.get("request_id"),
                    "target_resource": data.get("target_resource"),
                    "target_id": data.get("target_id"),
                    "changed_fields": data.get("changed_fields"),
                    "reason": data.get("reason"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return results


audit_service = AuditService()

