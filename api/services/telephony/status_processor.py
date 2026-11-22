"""Provider-agnostic call status processing.

Extracted from ``api/routes/telephony.py`` so that per-provider route
modules can import the processor and normalized request type without
introducing a circular import on the routes module.
"""

from datetime import UTC, datetime
from typing import Optional

from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.enums import WorkflowRunState
from api.services.campaign.campaign_call_dispatcher import campaign_call_dispatcher
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.telephony.inbound_queue import inbound_queue_manager


class StatusCallbackRequest(BaseModel):
    """Normalized status callback shape used across all telephony providers.

    Per-provider converters live as classmethods (``from_twilio``, ``from_plivo``,
    ``from_vonage``, ``from_cloudonix_cdr``) so the route handler for each
    provider can map raw webhook payloads into this shape and hand off to
    :func:`_process_status_update`.
    """

    call_id: str
    status: str
    from_number: Optional[str] = None
    to_number: Optional[str] = None
    direction: Optional[str] = None
    duration: Optional[str] = None

    extra: dict = {}

    @classmethod
    def from_twilio(cls, data: dict):
        """Convert Twilio callback to generic format."""
        return cls(
            call_id=data.get("CallSid", ""),
            status=data.get("CallStatus", ""),
            from_number=data.get("From"),
            to_number=data.get("To"),
            direction=data.get("Direction"),
            duration=data.get("CallDuration") or data.get("Duration"),
            extra=data,
        )

    @classmethod
    def from_plivo(cls, data: dict):
        """Convert Plivo callback to generic format."""
        status_map = {
            "in-progress": "answered",
            "ringing": "ringing",
            "ring": "ringing",
            "completed": "completed",
            "hangup": "completed",
            "stopstream": "completed",
            "busy": "busy",
            "no-answer": "no-answer",
            "cancel": "canceled",
            "cancelled": "canceled",
            "timeout": "no-answer",
        }
        call_status = (data.get("CallStatus") or data.get("Event") or "").lower()
        return cls(
            call_id=data.get("CallUUID", "") or data.get("RequestUUID", ""),
            status=status_map.get(call_status, call_status),
            from_number=data.get("From"),
            to_number=data.get("To"),
            direction=data.get("Direction"),
            duration=data.get("Duration"),
            extra=data,
        )

    @classmethod
    def from_vonage(cls, data: dict):
        """Convert Vonage event to generic format."""
        status_map = {
            "started": "initiated",
            "ringing": "ringing",
            "answered": "answered",
            "complete": "completed",
            "failed": "failed",
            "busy": "busy",
            "timeout": "no-answer",
            "rejected": "busy",
        }

        return cls(
            call_id=data.get("uuid", ""),
            status=status_map.get(data.get("status", ""), data.get("status", "")),
            from_number=data.get("from"),
            to_number=data.get("to"),
            direction=data.get("direction"),
            duration=data.get("duration"),
            extra=data,
        )

    @classmethod
    def from_cloudonix_cdr(cls, data: dict):
        """Convert Cloudonix CDR to generic format."""
        disposition_map = {
            "ANSWER": "completed",
            "BUSY": "busy",
            "CANCEL": "canceled",
            "FAILED": "failed",
            "CONGESTION": "failed",
            "NOANSWER": "no-answer",
        }

        disposition = data.get("disposition", "")
        status = disposition_map.get(disposition.upper(), disposition.lower())

        return cls(
            call_id=data.get("session").get("token"),
            status=status,
            from_number=data.get("from"),
            to_number=data.get("to"),
            duration=str(data.get("billsec") or data.get("duration") or 0),
            extra=data,
        )


async def _release_inbound_slot(workflow_run_id: int, workflow_run) -> None:
    call_id = (workflow_run.gathered_context or {}).get("call_id")
    organization_id = getattr(
        getattr(workflow_run, "workflow", None), "organization_id", None
    )
    if not call_id or organization_id is None:
        await inbound_queue_manager.release_for_run(workflow_run_id)
        return

    await inbound_queue_manager.release_call(
        organization_id=organization_id,
        call_id=call_id,
        workflow_run_id=workflow_run_id,
    )


async def _process_status_update(workflow_run_id: int, status: StatusCallbackRequest):
    """Process status updates from telephony providers.

    Idempotent: handles repeated callbacks (e.g. from both webhook and CDR).
    """
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found in status update"
        )
        return

    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    telephony_callback_log = {
        "status": status.status,
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": status.call_id,
        "duration": status.duration,
        **status.extra,
    }
    telephony_callback_logs.append(telephony_callback_log)

    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    state_str = str(workflow_run.state.value if hasattr(workflow_run.state, "value") else workflow_run.state).lower()
    is_terminal = workflow_run.is_completed or state_str in ("completed", "failed", "cancelled")
    if is_terminal:
        logger.info(
            f"[run {workflow_run_id}] Call is already terminal (state={state_str}, disposition={workflow_run.call_disposition}); preserving terminal state against late event '{status.status}'"
        )
        return

    if status.status == "completed":
        logger.info(
            f"[run {workflow_run_id}] Call completed with duration: {status.duration}s"
        )

        try:
            await _release_inbound_slot(workflow_run_id, workflow_run)
        except Exception as e:
            logger.warning(
                f"[run {workflow_run_id}] Failed to release inbound slot: {e}"
            )

        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
            await circuit_breaker.record_and_evaluate(
                workflow_run.campaign_id, is_failure=False
            )

        # Determine call disposition
        is_voicemail = (
            status.extra.get("AnsweredBy") in ("machine_start", "machine_end_beep", "machine_end_other")
            or "machine" in str(status.extra.get("AnsweringMachineDetection", "")).lower()
            or "voicemail" in str(status.extra.get("amd_status", "")).lower()
        )
        try:
            dur_num = float(status.duration or 0)
        except (ValueError, TypeError):
            dur_num = 0.0

        if is_voicemail:
            disposition = "VOICEMAIL_DETECTED"
        elif dur_num > 0:
            disposition = "ANSWERED"
        else:
            disposition = "NO_ANSWER"

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            call_disposition=disposition,
        )

    elif status.status in ["failed", "busy", "no-answer", "canceled", "error"]:
        logger.warning(
            f"[run {workflow_run_id}] Call failed with status: {status.status}"
        )

        try:
            await _release_inbound_slot(workflow_run_id, workflow_run)
        except Exception as e:
            logger.warning(
                f"[run {workflow_run_id}] Failed to release inbound slot: {e}"
            )

        if workflow_run.campaign_id:
            await campaign_call_dispatcher.release_call_slot(workflow_run_id)
            is_failure = status.status in ("error", "failed")
            await circuit_breaker.record_and_evaluate(
                workflow_run.campaign_id,
                is_failure=is_failure,
                workflow_run_id=workflow_run_id if is_failure else None,
                reason=status.status if is_failure else None,
            )

        if status.status in ["busy", "no-answer"] and workflow_run.campaign_id:
            publisher = await get_campaign_event_publisher()
            await publisher.publish_retry_needed(
                workflow_run_id=workflow_run_id,
                reason=status.status.replace("-", "_"),
                campaign_id=workflow_run.campaign_id,
                queued_run_id=workflow_run.queued_run_id,
            )

        call_tags = (
            workflow_run.gathered_context.get("call_tags", [])
            if workflow_run.gathered_context
            else []
        )
        call_tags.extend(["not_connected", f"telephony_{status.status.lower()}"])

        if status.status in ["busy", "rejected"]:
            disposition = "BUSY"
        elif status.status in ["no-answer", "timeout"]:
            disposition = "NO_ANSWER"
        elif status.status == "canceled":
            disposition = "CANCELLED"
        else:
            disposition = "FAILED"

        await db_client.update_workflow_run(
            run_id=workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            call_disposition=disposition,
            gathered_context={"call_tags": call_tags},
        )
    elif status.status in ["in-progress", "initiated", "ringing"]:
        # No-op while the call is in flight.
        pass
    else:
        logger.warning(
            f"[run {workflow_run_id}] Unexpected status update: {status.status}"
        )


def map_carrier_status_to_disposition(
    status: str = "completed",
    extra: Optional[dict] = None,
    duration: float = 0.0,
    carrier_event: Optional[str] = None,
    sip_code: Optional[int] = None,
) -> str:
    """Helper to deterministically map carrier events, AMD, and status codes to disposition."""
    extra = extra or {}
    is_voicemail = (
        carrier_event in ("voicemail_detected", "machine")
        or extra.get("AnsweredBy") in ("machine_start", "machine_end_beep", "machine_end_other")
        or "machine" in str(extra.get("AnsweringMachineDetection", "")).lower()
        or "voicemail" in str(extra.get("amd_status", "")).lower()
    )
    if is_voicemail:
        return "VOICEMAIL_DETECTED"
    s = (status or "").lower()
    if s in ["busy", "rejected"] or sip_code in (486, 600):
        return "BUSY"
    elif s in ["no-answer", "timeout"] or sip_code in (408, 480):
        return "NO_ANSWER"
    elif s in ["canceled", "cancelled"]:
        return "CANCELLED"
    elif s in ["failed", "error"]:
        return "FAILED"
    elif s == "completed" or duration > 0 or sip_code == 200:
        return "ANSWERED"
    return "UNKNOWN"
