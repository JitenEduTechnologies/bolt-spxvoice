"""Business Owner Action Inbox & Task Dispatch Engine for SPX Voice.

Tracks every incomplete business commitment (missed calls, appointments awaiting
confirmation, requested callbacks, brochure requests, unresolved objections)
so no customer promise is silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import WorkflowRunModel


class ActionItemType(str, Enum):
    MISSED_CALL = "MISSED_CALL"
    APPOINTMENT_CONFIRMATION = "APPOINTMENT_CONFIRMATION"
    CALLBACK_REQUEST = "CALLBACK_REQUEST"
    BROCHURE_MEDIA_REQUEST = "BROCHURE_MEDIA_REQUEST"
    ORDER_VERIFICATION = "ORDER_VERIFICATION"
    FEE_CORRECTION = "FEE_CORRECTION"
    UNRESOLVED_OBJECTION = "UNRESOLVED_OBJECTION"
    FAILED_TRANSFER = "FAILED_TRANSFER"


class ActionItemPriority(str, Enum):
    URGENT = "URGENT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ActionItemStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ActionItem(BaseModel):
    id: str
    organization_id: int
    item_type: ActionItemType
    priority: ActionItemPriority
    status: ActionItemStatus
    title: str
    description: str
    customer_phone: str
    customer_name: Optional[str] = None
    workflow_run_id: Optional[int] = None
    due_date: Optional[str] = None
    assigned_to: Optional[str] = None
    evidence: Dict[str, Any] = {}
    created_at: str


class ActionInboxController:
    """Aggregates actionable items from workflow runs and business events."""

    async def list_action_items(
        self,
        organization_id: int,
        status_filter: Optional[str] = None,
        item_type_filter: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Scans workflow runs and synthesizes pending actionable items."""
        workflows = await db_client.get_all_workflows_for_listing(organization_id=organization_id)
        workflow_ids = [w.id for w in workflows]

        if not workflow_ids:
            return []

        async with db_client.async_session() as session:
            stmt = (
                select(WorkflowRunModel)
                .where(WorkflowRunModel.workflow_id.in_(workflow_ids))
                .order_by(WorkflowRunModel.created_at.desc())
                .limit(limit * 2)
            )
            res = await session.execute(stmt)
            runs = res.scalars().all()

        items: List[Dict[str, Any]] = []

        for r in runs:
            intel = (r.annotations or {}).get("lead_intelligence") or {}
            caller_phone = (
                (r.initial_context or {}).get("caller_number")
                or (r.gathered_context or {}).get("caller_number")
                or (r.gathered_context or {}).get("phone_number")
                or "Unknown"
            )
            customer_name = (
                (r.initial_context or {}).get("customer_name")
                or (r.gathered_context or {}).get("customer_name")
                or intel.get("customer_name")
            )
            disposition = getattr(r, "call_disposition", None)
            persisted_status = (r.annotations or {}).get("action_inbox_status") or {}

            # 1. Missed Call Item
            call_type_val = r.call_type.value if hasattr(r.call_type, "value") else str(r.call_type or "")
            if disposition in ("MISSED", "NO_ANSWER", "BUSY") or (call_type_val == "inbound" and disposition == "MISSED"):
                m_id = f"act-missed-{r.id}"
                items.append({
                    "id": m_id,
                    "organization_id": organization_id,
                    "item_type": ActionItemType.MISSED_CALL.value,
                    "priority": ActionItemPriority.HIGH.value,
                    "status": persisted_status.get(m_id, ActionItemStatus.PENDING.value),
                    "title": f"Inbound Missed Call from {caller_phone}",
                    "description": f"Caller disconnected before conversation completed (disposition: {disposition}). Needs 1-click redial.",
                    "customer_phone": caller_phone,
                    "customer_name": customer_name,
                    "workflow_run_id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
                    "evidence": {"call_disposition": disposition, "run_name": r.name},
                })

            # 2. Appointment Item
            appt = intel.get("appointment") or {}
            if appt.get("requested"):
                a_id = f"act-appt-{r.id}"
                prio = ActionItemPriority.URGENT.value if appt.get("status") == "Confirmed" else ActionItemPriority.HIGH.value
                items.append({
                    "id": a_id,
                    "organization_id": organization_id,
                    "item_type": ActionItemType.APPOINTMENT_CONFIRMATION.value,
                    "priority": prio,
                    "status": persisted_status.get(a_id, ActionItemStatus.PENDING.value),
                    "title": f"Appointment Request: {appt.get('appointment_type', 'Consultation')}",
                    "description": f"Preferred slot: {appt.get('preferred_slot', 'Upcoming')}. Contact {caller_phone}.",
                    "customer_phone": caller_phone,
                    "customer_name": customer_name,
                    "workflow_run_id": r.id,
                    "due_date": appt.get("preferred_slot"),
                    "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
                    "evidence": appt,
                })

            # 3. Callback Request Item
            cb = intel.get("callback") or {}
            if cb.get("requested"):
                cb_id = f"act-cb-{r.id}"
                items.append({
                    "id": cb_id,
                    "organization_id": organization_id,
                    "item_type": ActionItemType.CALLBACK_REQUEST.value,
                    "priority": ActionItemPriority.HIGH.value,
                    "status": persisted_status.get(cb_id, ActionItemStatus.PENDING.value),
                    "title": f"Customer Requested Callback for {caller_phone}",
                    "description": "Caller requested follow-up discussion at a convenient time.",
                    "customer_phone": caller_phone,
                    "customer_name": customer_name,
                    "workflow_run_id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
                    "evidence": cb,
                })

            # 4. Human Handover / Transfer Failed
            esc = intel.get("human_escalation") or {}
            if esc.get("requested"):
                esc_id = f"act-esc-{r.id}"
                items.append({
                    "id": esc_id,
                    "organization_id": organization_id,
                    "item_type": ActionItemType.FAILED_TRANSFER.value,
                    "priority": ActionItemPriority.URGENT.value,
                    "status": persisted_status.get(esc_id, ActionItemStatus.PENDING.value),
                    "title": f"Executive Assistance Requested by {caller_phone}",
                    "description": "Customer requested live staff escalation during call.",
                    "customer_phone": caller_phone,
                    "customer_name": customer_name,
                    "workflow_run_id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
                    "evidence": esc,
                })

        # Apply filters
        if item_type_filter:
            items = [it for it in items if it["item_type"] == item_type_filter]
        if status_filter:
            items = [it for it in items if it["status"] == status_filter]

        return items[:limit]

    async def update_item_status(
        self,
        item_id: str,
        status: str,
        organization_id: int,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        """Persists resolved/archived status for an action inbox item into the workflow run."""
        try:
            parts = item_id.split("-")
            if len(parts) >= 3 and parts[-1].isdigit():
                run_id = int(parts[-1])
                run = await db_client.get_workflow_run_by_id(run_id)
                if run:
                    import copy
                    ann = copy.deepcopy(run.annotations or {})
                    inbox_status = copy.deepcopy(ann.get("action_inbox_status") or {})
                    inbox_status[item_id] = status
                    ann["action_inbox_status"] = inbox_status
                    if resolution_notes:
                        ann[f"action_notes_{item_id}"] = resolution_notes
                    await db_client.update_workflow_run(run.id, annotations=ann)
                    logger.info(f"[ActionInbox] Successfully persisted item {item_id} -> {status} in run {run_id}")
                    return True
        except Exception as e:
            logger.error(f"[ActionInbox] Failed to persist status for {item_id}: {e}")
        return False


action_inbox = ActionInboxController()

