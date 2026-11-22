"""Human Handover Controller for SPX Voice Telephony.

Provides gated human agent transfer coordination, cross-tenant protection,
explicit state machine transitions (REQUESTED -> PENDING -> SUCCESSFUL / FAILED),
live resource tracking, and fail-closed SIP 503 fallback to callback offer.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from loguru import logger

from api.db import db_client
from api.services.telephony.compliance import normalize_phone_number
from api.services.telephony.smart_did import smart_did_controller
from api.services.telephony.transfer_event_protocol import (
    TransferEvent,
    TransferEventType,
)


class HandoverState(str, Enum):
    REQUESTED = "REQUESTED"
    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    FAILED = "FAILED"


class HumanHandoverController:
    """Manages human handover state transitions, tenant isolation, and failure fallbacks."""

    def __init__(self) -> None:
        # Key: (organization_id, normalized_phone_number) -> True
        self._authorized_endpoints: Dict[Tuple[int, str], bool] = {}

    def register_authorized_endpoint(
        self, organization_id: int, phone_number: str
    ) -> None:
        """Register an authorized human handover destination number for a tenant."""
        norm = normalize_phone_number(phone_number)
        self._authorized_endpoints[(organization_id, norm)] = True

    def is_endpoint_authorized(
        self, organization_id: int, target_number: str
    ) -> bool:
        """Verify target number is authorized for the given organization."""
        norm = normalize_phone_number(target_number)
        if (organization_id, norm) in self._authorized_endpoints:
            return True
        # Check if number is registered in smart_did_controller for this org
        key = (organization_id, norm)
        if key in smart_did_controller._dids:
            return True
        return False

    def clear(self) -> None:
        """Clear registered endpoints (useful for test isolation)."""
        self._authorized_endpoints.clear()

    async def request_handover(
        self,
        workflow_run_id: int,
        organization_id: int,
        target_number: str,
        reason: str = "customer_requested_human_agent",
        transfer_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Initiate human handover for a workflow run.

        Transitions to PENDING and registers initial state REQUESTED.
        Enforces tenant boundary and destination authorization.
        """
        # Validate endpoint authorization (Cross-tenant / unauthorized rejection)
        if not self.is_endpoint_authorized(organization_id, target_number):
            logger.warning(
                f"[Handover] Target {target_number} not authorized for org {organization_id}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Unauthorized transfer destination: {target_number} is not authorized for organization {organization_id}",
            )

        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow run {workflow_run_id} not found",
            )

        # Verify organization ownership
        run_org_id = await db_client.get_organization_id_by_workflow_run_id(workflow_run_id)
        if run_org_id is not None and run_org_id != organization_id:
            logger.warning(
                f"[Handover] Run {workflow_run_id} belongs to org {run_org_id}, not {organization_id}"
            )
            raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

        existing_annotations = getattr(workflow_run, "annotations", None) or {}
        existing = existing_annotations.get("human_handover", {})
        current_state = existing.get("state")
        if current_state in (HandoverState.SUCCESSFUL.value, HandoverState.FAILED.value):
            logger.info(
                f"[Handover] Run {workflow_run_id} already in terminal state {current_state}; ignoring request"
            )
            return existing

        now_iso = datetime.now(UTC).isoformat()
        t_id = transfer_id or f"xfer-{workflow_run_id}-{int(time.time() * 1000)}"
        norm_target = normalize_phone_number(target_number)

        handover_data = {
            "transfer_id": t_id,
            "state": HandoverState.PENDING.value,
            "initial_state": HandoverState.REQUESTED.value,
            "target_number": norm_target,
            "organization_id": organization_id,
            "reason": reason,
            "requested_at": now_iso,
            "updated_at": now_iso,
            "history": existing.get("history", [])
            + [
                {"state": HandoverState.REQUESTED.value, "timestamp": now_iso},
                {"state": HandoverState.PENDING.value, "timestamp": now_iso},
            ],
        }

        # Track active channel usage on the target
        smart_did_controller.record_call_start(organization_id, norm_target)

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            annotations={"human_handover": handover_data},
        )
        return handover_data

    async def complete_handover(
        self,
        workflow_run_id: int,
        organization_id: int,
        bridge_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Transition pending handover to SUCCESSFUL upon bridge connection."""
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow run {workflow_run_id} not found",
            )

        run_org_id = await db_client.get_organization_id_by_workflow_run_id(workflow_run_id)
        if run_org_id is not None and run_org_id != organization_id:
            raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

        existing_annotations = getattr(workflow_run, "annotations", None) or {}
        existing = existing_annotations.get("human_handover", {})
        current_state = existing.get("state")

        # Idempotency: late/duplicate events must not change terminal state
        if current_state in (HandoverState.SUCCESSFUL.value, HandoverState.FAILED.value):
            logger.info(
                f"[Handover] Run {workflow_run_id} is already in terminal state {current_state}; ignoring complete event"
            )
            return existing

        now_iso = datetime.now(UTC).isoformat()
        target_number = existing.get("target_number")
        if target_number:
            smart_did_controller.record_call_end(
                organization_id, target_number, success=True
            )

        updated_handover = {
            **existing,
            "state": HandoverState.SUCCESSFUL.value,
            "bridge_event_id": bridge_event_id,
            "completed_at": now_iso,
            "updated_at": now_iso,
            "history": existing.get("history", [])
            + [
                {
                    "state": HandoverState.SUCCESSFUL.value,
                    "timestamp": now_iso,
                    "bridge_event_id": bridge_event_id,
                }
            ],
        }

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            annotations={"human_handover": updated_handover},
        )
        return updated_handover

    async def fail_handover(
        self,
        workflow_run_id: int,
        organization_id: int,
        failure_code: str = "503",
        failure_reason: str = "Service Unavailable / Agent Busy",
        offer_callback: bool = True,
    ) -> Dict[str, Any]:
        """Transition pending handover to FAILED, release resources, and persist callback offer."""
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not workflow_run:
            raise HTTPException(
                status_code=404,
                detail=f"Workflow run {workflow_run_id} not found",
            )

        run_org_id = await db_client.get_organization_id_by_workflow_run_id(workflow_run_id)
        if run_org_id is not None and run_org_id != organization_id:
            raise HTTPException(status_code=403, detail="Cross-tenant access forbidden")

        existing_annotations = getattr(workflow_run, "annotations", None) or {}
        existing = existing_annotations.get("human_handover", {})
        current_state = existing.get("state")

        # Idempotency: late/duplicate events must not change terminal state
        if current_state in (HandoverState.SUCCESSFUL.value, HandoverState.FAILED.value):
            logger.info(
                f"[Handover] Run {workflow_run_id} is already in terminal state {current_state}; ignoring fail event"
            )
            return existing

        now_iso = datetime.now(UTC).isoformat()
        target_number = existing.get("target_number")

        # Release resource / channel capacity
        if target_number:
            smart_did_controller.record_call_end(
                organization_id,
                target_number,
                success=False,
                failure_reason=failure_reason,
                status_code=failure_code,
            )

        updated_handover = {
            **existing,
            "state": HandoverState.FAILED.value,
            "failure_code": str(failure_code),
            "failure_reason": failure_reason,
            "failed_at": now_iso,
            "updated_at": now_iso,
            "history": existing.get("history", [])
            + [
                {
                    "state": HandoverState.FAILED.value,
                    "timestamp": now_iso,
                    "code": str(failure_code),
                    "reason": failure_reason,
                }
            ],
        }

        updated_annotations: Dict[str, Any] = {
            "human_handover": updated_handover,
        }

        if offer_callback:
            # Fall back to callback offer in lead_intelligence annotations
            existing_lead_intel = existing_annotations.get("lead_intelligence", {})
            updated_annotations["lead_intelligence"] = {
                **existing_lead_intel,
                "callback": {
                    "requested": True,
                    "status": "Pending",
                    "reason": f"human_handover_failed_{failure_code}",
                    "offered_at": now_iso,
                    "target_number": target_number,
                },
            }

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            annotations=updated_annotations,
        )
        return updated_handover


human_handover_controller = HumanHandoverController()

