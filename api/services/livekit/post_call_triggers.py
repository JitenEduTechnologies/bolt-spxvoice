"""Post-Call Intelligence Analysis and Action Trigger Dispatch for SPX Voice.

Connects live call completion to:
1. Lead intelligence analysis (title, intent, summary, requirements, score,
   classification, outcome, next action) derived from caller speech.
2. Idempotent, atomic persistence on the workflow run: AI evidence and owner
   overrides are stored separately and a retry never clobbers owner corrections.
3. Durable, tenant-scoped real-time events (no caller PII in the new events).
4. Asynchronous transactional action triggers (brochure delivery, verified
   appointment notifications) through the idempotent outbox.

The organization is always resolved from the run's workflow. If it cannot be
verified the whole step fails closed: nothing is persisted, published or enqueued.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from api.db import db_client
from api.services.business.conversation_view import (
    apply_ai_analysis,
    apply_analysis_failure,
    recording_status,
    transcript_hash,
)
from api.services.business.lead_intelligence import (
    analyze_lead_from_transcript,
    extract_transcript_from_logs,
    parse_transcript_turns,
)
from api.services.chatwoot.outbox import chatwoot_outbox_service
from api.services.events.realtime_bus import realtime_dashboard_bus


async def _resolve_organization_id(run: Any) -> Optional[int]:
    """Verified organization of the run's workflow, or ``None``. Never defaults."""
    workflow = getattr(run, "workflow", None)
    org_id = getattr(workflow, "organization_id", None) if workflow is not None else None
    if isinstance(org_id, int) and not isinstance(org_id, bool):
        return org_id
    try:
        wf = await db_client.get_workflow_by_id(run.workflow_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            f"[PostCall] Could not load workflow {getattr(run, 'workflow_id', None)}: {type(exc).__name__}"
        )
        return None
    org_id = getattr(wf, "organization_id", None) if wf is not None else None
    if isinstance(org_id, int) and not isinstance(org_id, bool):
        return org_id
    return None


def _storage_configured() -> bool:
    try:
        from api.services.livekit import post_call

        return post_call._recording_config_from_env() is not None
    except Exception:
        return False


async def _publish(
    organization_id: int,
    event_type: str,
    run_id: int,
    data: Dict[str, Any],
    resource_type: str = "conversation",
) -> None:
    try:
        await realtime_dashboard_bus.publish_event(
            organization_id=organization_id,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=str(run_id),
            data=data,
        )
    except Exception as exc:
        logger.warning(f"[PostCall] Failed to publish {event_type}: {type(exc).__name__}")


async def process_post_call_intelligence_and_triggers(
    workflow_run_id: int,
    livekit_history: Any = None,
    gathered_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Processes post-call intelligence analysis and transactional triggers idempotently."""
    logger.info(f"[PostCall] Processing post-call triggers for workflow run {workflow_run_id}")
    gathered_context = gathered_context or {}
    organization_id: Optional[int] = None  # set once verified; used by the failure path

    try:
        run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if not run:
            logger.warning(f"[PostCall] Workflow run {workflow_run_id} not found; skipping.")
            return {"status": "not_found", "workflow_run_id": workflow_run_id}

        organization_id = await _resolve_organization_id(run)
        if organization_id is None:
            logger.error(
                f"[PostCall] Organization for run {workflow_run_id} could not be verified; failing closed."
            )
            return {
                "status": "error",
                "reason": "organization_unresolved",
                "workflow_run_id": workflow_run_id,
            }

        init_ctx = run.initial_context or {}
        gath_ctx = run.gathered_context or {}
        annots = dict(run.annotations or {})

        caller_phone = (
            init_ctx.get("caller_number")
            or init_ctx.get("phone_number")
            or init_ctx.get("customer_phone")
            or gath_ctx.get("caller_number")
            or gath_ctx.get("phone_number")
            or gathered_context.get("caller_number")
            or gathered_context.get("phone_number")
            or annots.get("caller_phone")
        )
        customer_name = (
            init_ctx.get("customer_name")
            or gath_ctx.get("customer_name")
            or gathered_context.get("customer_name")
            or annots.get("customer_name")
        )

        # 1. Transcript from the committed history (or the stored run logs).
        raw_logs = {"livekit_history": livekit_history} if livekit_history else run.logs
        transcript = extract_transcript_from_logs(raw_logs)
        th = transcript_hash(transcript)

        # 2. Analyse (deterministic; an empty transcript yields an explicit
        #    "no transcript" analysis, never an invented lead).
        biz_ctx = None
        try:
            wf = await db_client.get_workflow_by_id(run.workflow_id)
            biz_ctx = (wf.workflow_configurations or {}).get("agent_training") if wf else None
        except Exception as exc:
            logger.debug(f"[PostCall] business context unavailable: {type(exc).__name__}")
        intel = analyze_lead_from_transcript(
            transcript,
            business_context=biz_ctx if isinstance(biz_ctx, dict) else None,
            gathered_context={**gath_ctx, **gathered_context},
        )
        if customer_name and not intel.get("customer_name"):
            intel["customer_name"] = customer_name

        # 3. Atomic, idempotent persistence. Owner overrides are preserved.
        def _persist(current: Dict[str, Any]):
            updated, changed = apply_ai_analysis(
                current,
                intel,
                transcript_hash_value=th,
                search_text="\n".join(
                    [str(intel.get("title", "")), str(intel.get("summary", "")), transcript]
                ),
            )
            if not updated.get("post_call_processed"):
                updated["post_call_processed"] = True
                updated["post_call_processed_at"] = datetime.now(UTC).isoformat()
                changed = True
            if caller_phone and not updated.get("caller_phone"):
                updated["caller_phone"] = caller_phone
                changed = True
            return updated, changed

        persisted, changed = await db_client.mutate_workflow_run_annotations(
            workflow_run_id, _persist, organization_id=organization_id
        )
        if persisted is None:
            return {
                "status": "error",
                "reason": "run_not_in_organization",
                "workflow_run_id": workflow_run_id,
            }

        # 4. Durable real-time events (only when something actually changed).
        if changed:
            rec = recording_status(run, storage_configured=_storage_configured())
            await _publish(
                organization_id,
                "conversation_finalized",
                workflow_run_id,
                {"workflow_run_id": workflow_run_id},
            )
            await _publish(
                organization_id,
                "conversation_analysis_completed",
                workflow_run_id,
                {
                    "workflow_run_id": workflow_run_id,
                    "analysis_status": (persisted.get("analysis") or {}).get("status"),
                    "lead_classification": (persisted.get("lead_intelligence") or {}).get(
                        "lead_classification"
                    ),
                },
            )
            await _publish(
                organization_id,
                "recording_status_changed",
                workflow_run_id,
                {"workflow_run_id": workflow_run_id, "recording_status": rec["status"]},
            )
            # Legacy event consumed by the Overview page.
            await _publish(
                organization_id,
                "lead_intelligence_updated",
                workflow_run_id,
                {
                    "workflow_run_id": workflow_run_id,
                    "lead_score": intel.get("lead_score"),
                    "lead_classification": intel.get("lead_classification"),
                    "customer_name": customer_name,
                    "phone_number": caller_phone,
                },
                resource_type="workflow_run",
            )

        # 5. Dispatch action triggers (non-blocking, idempotent outbox keys).
        actions_triggered: List[str] = []
        failed_actions: List[str] = []
        caller_text = " ".join(
            t["text"] for t in parse_transcript_turns(transcript) if t["speaker"] == "caller"
        ).lower()

        if caller_phone and intel.get("analysis_status") == "complete":
            # Trigger A: caller-requested document / brochure delivery
            brochure_requested = bool(intel.get("brochure_requested")) or ("brochure" in caller_text)
            brochure_url = (
                gathered_context.get("brochure_url")
                or gath_ctx.get("brochure_url")
                or init_ctx.get("brochure_url")
                or annots.get("brochure_url")
            )
            if brochure_requested and brochure_url:
                try:
                    await chatwoot_outbox_service.enqueue_message(
                        organization_id=organization_id,
                        recipient_phone=str(caller_phone),
                        message_type="document_delivery",
                        payload={
                            "customer_name": customer_name or "Valued Customer",
                            "document_name": "Brochure.pdf",
                            "document_url": brochure_url,
                        },
                        workflow_run_id=workflow_run_id,
                        idempotency_key=f"postcall_doc_{workflow_run_id}",
                    )
                    actions_triggered.append("document_delivery")
                    logger.info(f"[PostCall] Enqueued brochure document delivery (run {workflow_run_id})")
                except Exception:
                    failed_actions.append("document_delivery")
                    logger.error("[PostCall] Failed to enqueue brochure delivery")

            # Trigger B: confirmed appointment notification. Strictly requires an
            # authoritative booking verification; otherwise it stays "Requested".
            appt = intel.get("appointment") or {}
            auth_verified = bool(
                appt.get("authoritative_booking_verified")
                or gathered_context.get("authoritative_booking_verified")
                or gath_ctx.get("authoritative_booking_verified")
                or init_ctx.get("authoritative_booking_verified")
            )
            booking_id = (
                appt.get("booking_id")
                or gathered_context.get("booking_id")
                or gath_ctx.get("booking_id")
                or init_ctx.get("booking_id")
            )
            is_confirmed = (
                bool(appt.get("is_confirmed"))
                or str(appt.get("status", "")).strip().lower() == "confirmed"
                or bool(gathered_context.get("is_confirmed"))
                or (auth_verified and bool(booking_id))
            )
            if appt.get("requested") and is_confirmed and auth_verified and booking_id:
                try:
                    await chatwoot_outbox_service.enqueue_message(
                        organization_id=organization_id,
                        recipient_phone=str(caller_phone),
                        message_type="appointment_confirmation",
                        payload={
                            "customer_name": customer_name or "Valued Customer",
                            "status": "Confirmed",
                            "is_confirmed": True,
                            "authoritative_booking_verified": True,
                            "booking_id": str(booking_id),
                            "appointment_date": appt.get("preferred_slot") or "Upcoming",
                            "appointment_datetime": appt.get("preferred_slot") or "Upcoming",
                            "service_name": appt.get("appointment_type")
                            or appt.get("type")
                            or "Consultation",
                        },
                        workflow_run_id=workflow_run_id,
                        idempotency_key=f"postcall_appt_{workflow_run_id}",
                    )
                    actions_triggered.append("appointment_confirmation")
                    logger.info(
                        f"[PostCall] Enqueued verified appointment confirmation (run {workflow_run_id})"
                    )
                except Exception:
                    failed_actions.append("appointment_confirmation")
                    logger.error("[PostCall] Failed to enqueue appointment confirmation")
            elif appt.get("requested"):
                logger.info(
                    f"[PostCall] Appointment requested for run {workflow_run_id} but authoritative "
                    f"verification is pending (verified={auth_verified}). Not sending unverified confirmation."
                )

            # Trigger C: hot-lead owner notification
            is_hot = intel.get("lead_classification") == "Hot" or (intel.get("lead_score") or 0) >= 80
            owner_phone = (
                gathered_context.get("owner_phone")
                or gath_ctx.get("owner_phone")
                or init_ctx.get("owner_phone")
            )
            if is_hot and owner_phone:
                try:
                    await chatwoot_outbox_service.enqueue_message(
                        organization_id=organization_id,
                        recipient_phone=str(owner_phone),
                        message_type="booking_details",
                        payload={
                            "customer_name": f"Hot Lead: {customer_name or caller_phone}",
                            "booking_reference": f"LEAD-{workflow_run_id}",
                            "booking_summary": (
                                f"Score {intel.get('lead_score', 0)}/100. "
                                f"Intent: {intel.get('customer_intent', 'Inquiry')}. "
                                f"Contact: {caller_phone}"
                            ),
                        },
                        workflow_run_id=workflow_run_id,
                        idempotency_key=f"postcall_hotlead_{workflow_run_id}",
                    )
                    actions_triggered.append("owner_notification")
                    logger.info(f"[PostCall] Enqueued hot lead owner alert (run {workflow_run_id})")
                except Exception:
                    failed_actions.append("owner_notification")
                    logger.error("[PostCall] Failed to enqueue owner alert")

        return {
            "status": "error" if failed_actions else "success",
            "reason": "outbox_enqueue_failed" if failed_actions else None,
            "failed_actions": failed_actions,
            "workflow_run_id": workflow_run_id,
            "intel": intel,
            "analysis_changed": changed,
            "actions_triggered": actions_triggered,
        }

    except Exception as exc:
        logger.error(
            f"[PostCall] Error processing post-call triggers for run {workflow_run_id}: {type(exc).__name__}"
        )
        # Record a visible, retryable failure without disturbing AI evidence/overrides.
        try:
            await db_client.mutate_workflow_run_annotations(
                workflow_run_id,
                lambda current: (
                    apply_analysis_failure(current, f"post_call_failed:{type(exc).__name__}"),
                    True,
                ),
            )
        except Exception:
            logger.warning(f"[PostCall] Could not record analysis failure for run {workflow_run_id}")
        if organization_id is not None:
            # Only a verified organization ever receives the event (no default tenant).
            await _publish(
                organization_id,
                "conversation_analysis_failed",
                workflow_run_id,
                {"workflow_run_id": workflow_run_id, "analysis_status": "failed", "retryable": True},
            )
        return {"status": "error", "reason": "post_call_failed", "workflow_run_id": workflow_run_id}
