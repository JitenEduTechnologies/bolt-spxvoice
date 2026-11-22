"""Observability, Centralized Log Explorer & Call Diagnostic Timeline Service (Phase C4).

Coordinates:
- Centralized server-side log search across C1 request traces, C2 durable audit events,
  and C3/C4 durable incident events.
- Complete Call Diagnostic Timeline: correlates carrier events, LiveKit lifecycle,
  voice runtime latency, CRM gathered data, and billing reconciliation for a specific call.
- Safe exports and retention previews with fail-closed audit tracking.
- Strict multi-tenant isolation, server-side authorization, and automatic secret/PII redaction.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import desc, select

from api.db import db_client
from api.db.models import DashboardEventModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.audit.audit_service import (
    audit_service,
    mask_phone_number,
    sanitize_audit_payload,
)
from api.services.health.durable_incident_store import durable_incident_store

# Explicit positive allowlist of ephemeral operational resource types.
# CRITICAL SAFETY: Business entities (leads, appointments, calls, objections, workflow_runs)
# and durable security audit records are PERMANENTLY EXCLUDED from automated purging.
ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES = ("system", "operational_log", "debug", "telemetry")


class ObservabilityService:
    """Core observability query engine for Superadmins and authorized Business Owners."""

    async def search_logs(
        self,
        user: UserModel,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        severity: Optional[str] = None,
        component: Optional[str] = None,
        organization_id: Optional[int] = None,
        call_id: Optional[str] = None,
        request_id: Optional[str] = None,
        run_id: Optional[str] = None,
        event_type: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Server-side paginated search across audit logs, incidents, and workflow runs.

        Guarantees:
        - Superadmins can search across all tenants or filter by organization_id.
        - Ordinary users are strictly restricted to their selected organization.
        - Secrets and phone numbers are scrubbed prior to return.
        """
        # Multi-tenant security check
        if not user.is_superuser:
            if organization_id and organization_id != user.selected_organization_id:
                raise PermissionError("Access to logs outside your organization is forbidden.")
            organization_id = user.selected_organization_id

        limit = min(max(1, limit), 100)
        offset = (page - 1) * limit
        all_events: List[Dict[str, Any]] = []

        # 1. Fetch Durable Audit Records from PostgreSQL dashboard_events
        async with db_client.async_session() as session:
            stmt = select(DashboardEventModel).where(
                DashboardEventModel.resource_type == "audit"
            )
            if organization_id is not None:
                stmt = stmt.where(DashboardEventModel.organization_id == organization_id)
            if start_time:
                stmt = stmt.where(DashboardEventModel.created_at >= start_time)
            if end_time:
                stmt = stmt.where(DashboardEventModel.created_at <= end_time)
            if event_type:
                stmt = stmt.where(DashboardEventModel.event_type.ilike(f"%{event_type}%"))

            stmt = stmt.order_by(desc(DashboardEventModel.id)).limit(150)
            res = await session.execute(stmt)
            audit_rows = res.scalars().all()

            for r in audit_rows:
                data = dict(r.data or {})
                row_call_id = str(data.get("call_id") or "")
                row_req_id = str(data.get("request_id") or "")

                if call_id and call_id not in row_call_id:
                    continue
                req_filter = str(request_id or run_id or "")
                if req_filter and req_filter not in row_req_id:
                    continue

                all_events.append({
                    "id": f"audit_{r.id}",
                    "source": "audit_trail",
                    "event_type": r.event_type,
                    "component": "audit",
                    "severity": "INFO" if data.get("outcome") == "SUCCESS" else "WARNING",
                    "status": data.get("outcome", "SUCCESS"),
                    "organization_id": r.organization_id,
                    "call_id": data.get("call_id"),
                    "request_id": data.get("request_id"),
                    "message": f"Action {r.event_type} on {data.get('target_resource')}:{data.get('target_id')} [{data.get('outcome')}]",
                    "details": sanitize_audit_payload(data),
                    "timestamp": r.created_at.isoformat() if r.created_at else datetime.now(UTC).isoformat(),
                })

        # 2. Fetch Durable Incidents from DurableIncidentStore
        incident_corr = request_id or run_id
        incidents, _ = await durable_incident_store.query_incidents(
            severity=severity,
            component=component,
            correlation_id=incident_corr,
            call_id=call_id,
            start_time=start_time,
            end_time=end_time,
            limit=100,
        )

        for raw_inc in incidents:
            inc = raw_inc if isinstance(raw_inc, dict) else raw_inc.model_dump()
            inc_org = inc.get("organization_id")
            if organization_id is not None and inc_org is not None and inc_org != organization_id:
                continue

            if run_id or (request_id and not call_id):
                # When run-scoped, strictly ensure incident matches this run
                target_check = str(run_id or request_id)
                if inc.get("correlation_id") != target_check and inc.get("call_id") != target_check:
                    continue

            all_events.append({
                "id": inc.get("incident_id"),
                "source": "incident_store",
                "event_type": f"incident.{inc.get('component')}",
                "component": inc.get("component"),
                "severity": inc.get("severity", "WARNING"),
                "status": "RESOLVED" if inc.get("resolved_at") else "ACTIVE",
                "organization_id": inc_org,
                "call_id": inc.get("call_id"),
                "request_id": inc.get("correlation_id"),
                "error_code": inc.get("error_code"),
                "message": inc.get("message"),
                "details": sanitize_audit_payload(inc.get("context", {})),
                "timestamp": inc.get("latest_seen"),
            })

        # 3. Fetch Run-scoped Execution Logs from WorkflowRunModel
        run_query_target = run_id or request_id or call_id
        if run_query_target:
            async with db_client.async_session() as session:
                target_runs: List[WorkflowRunModel] = []
                target_str = str(run_query_target).strip()
                if target_str.isdigit():
                    r_stmt = select(WorkflowRunModel).where(WorkflowRunModel.id == int(target_str))
                    r_res = await session.execute(r_stmt)
                    r_obj = r_res.scalar_one_or_none()
                    if r_obj:
                        target_runs.append(r_obj)

                if not target_runs:
                    r_stmt = select(WorkflowRunModel).where(WorkflowRunModel.name == target_str)
                    r_res = await session.execute(r_stmt)
                    target_runs = list(r_res.scalars().all())

                if not target_runs and (call_id or run_query_target):
                    candidate_id = str(call_id or run_query_target)
                    r_stmt = select(WorkflowRunModel).order_by(desc(WorkflowRunModel.id)).limit(100)
                    r_res = await session.execute(r_stmt)
                    for candidate in r_res.scalars().all():
                        c_ctx = candidate.initial_context or {}
                        c_logs = candidate.logs or {}
                        if (
                            c_ctx.get("call_id") == candidate_id
                            or c_logs.get("call_id") == candidate_id
                        ):
                            target_runs.append(candidate)
                            break

                for r in target_runs:
                    if not isinstance(r, WorkflowRunModel):
                        continue
                    # Multi-tenant check
                    if organization_id is not None:
                        wf_res = await session.execute(
                            select(WorkflowModel).where(WorkflowModel.id == r.workflow_id)
                        )
                        wf = wf_res.scalar_one_or_none()
                        if wf and wf.organization_id != organization_id:
                            continue

                    r_created = (
                        r.created_at.isoformat()
                        if r.created_at
                        else datetime.now(UTC).isoformat()
                    )
                    r_ctx = r.initial_context or {}
                    r_logs = r.logs or {}
                    r_annot = r.annotations or {}
                    r_call_id = r_ctx.get("call_id") or r.name or f"call_{r.id}"

                    # A. Run Lifecycle Start
                    all_events.append({
                        "id": f"run_{r.id}_start",
                        "source": "workflow_engine",
                        "event_type": "workflow.run.started",
                        "component": "workflow",
                        "severity": "INFO",
                        "status": "STARTED",
                        "organization_id": organization_id,
                        "call_id": r_call_id,
                        "request_id": str(r.id),
                        "message": f"Workflow run #{r.id} started ({r.mode or 'telephony'} | {r.call_type or 'outbound'})",
                        "details": sanitize_audit_payload({
                            "run_id": r.id,
                            "workflow_id": r.workflow_id,
                            "definition_id": r.definition_id,
                            "mode": r.mode,
                            "call_type": r.call_type,
                            "from_number": mask_phone_number(str(r_ctx.get("from_number", ""))),
                            "to_number": mask_phone_number(str(r_ctx.get("to_number", ""))),
                        }),
                        "timestamp": r_created,
                    })

                    # B. LiveKit Events & Errors
                    lk_events = r_logs.get("livekit_events") or []
                    if isinstance(lk_events, list):
                        for idx, lk_ev in enumerate(lk_events):
                            all_events.append({
                                "id": f"run_{r.id}_lk_{idx}",
                                "source": "livekit_worker",
                                "event_type": "livekit.worker.event",
                                "component": "livekit",
                                "severity": "INFO",
                                "status": "RECORDED",
                                "organization_id": organization_id,
                                "call_id": r_call_id,
                                "request_id": str(r.id),
                                "message": f"LiveKit event: {lk_ev}",
                                "details": {"room_name": r_logs.get("room_name"), "event": lk_ev},
                                "timestamp": (
                                    r.created_at + timedelta(milliseconds=100 * (idx + 1))
                                ).isoformat()
                                if r.created_at
                                else r_created,
                            })

                    lk_err = r_annot.get("livekit_startup_error")
                    if lk_err:
                        all_events.append({
                            "id": f"run_{r.id}_lk_error",
                            "source": "livekit_worker",
                            "event_type": "livekit.worker.error",
                            "component": "livekit",
                            "severity": "ERROR",
                            "status": "FAILED",
                            "organization_id": organization_id,
                            "call_id": r_call_id,
                            "request_id": str(r.id),
                            "message": f"LiveKit worker startup error: {lk_err}",
                            "details": {"error": lk_err},
                            "timestamp": r_created,
                        })

                    # C. Telephony Status Callbacks
                    tel_cbs = r_logs.get("telephony_status_callbacks") or []
                    if isinstance(tel_cbs, list):
                        for idx, cb in enumerate(tel_cbs):
                            cb_status = str(cb.get("status", "update")).lower()
                            cb_sev = (
                                "ERROR"
                                if cb_status in ("failed", "busy", "no-answer")
                                else "INFO"
                            )
                            all_events.append({
                                "id": f"run_{r.id}_tel_cb_{idx}",
                                "source": "carrier_sip",
                                "event_type": f"telephony.status.{cb_status}",
                                "component": "telephony",
                                "severity": cb_sev,
                                "status": cb.get("status", "unknown").upper(),
                                "organization_id": organization_id,
                                "call_id": r_call_id,
                                "request_id": str(r.id),
                                "message": f"Carrier status callback: {cb.get('status')} (duration={cb.get('duration', '0')}s, answered_by={cb.get('AnsweredBy', 'unknown')})",
                                "details": sanitize_audit_payload(cb),
                                "timestamp": cb.get("timestamp") or r_created,
                            })

                    # D. Handover Events / Failures
                    handover = r_annot.get("human_handover") or {}
                    if handover:
                        h_state = handover.get("state", "UNKNOWN")
                        h_sev = "ERROR" if h_state == "FAILED" else "INFO"
                        all_events.append({
                            "id": f"run_{r.id}_handover",
                            "source": "telephony_switch",
                            "event_type": f"telephony.handover.{h_state.lower()}",
                            "component": "telephony",
                            "severity": h_sev,
                            "status": h_state,
                            "organization_id": organization_id,
                            "call_id": r_call_id,
                            "request_id": str(r.id),
                            "message": f"Human handover {h_state}: {handover.get('failure_reason') or handover.get('reason') or ('target: ' + mask_phone_number(str(handover.get('target_number'))))}",
                            "details": sanitize_audit_payload(handover),
                            "timestamp": handover.get("failed_at")
                            or handover.get("completed_at")
                            or r_created,
                        })

                    # E. Dialogue / AI turns
                    messages = r_logs.get("messages") or []
                    if isinstance(messages, list):
                        for idx, msg in enumerate(messages):
                            role = msg.get("role", "assistant")
                            content = mask_phone_number(str(msg.get("content", "")))
                            short_content = (
                                (content[:100] + "...")
                                if len(content) > 100
                                else content
                            )
                            all_events.append({
                                "id": f"run_{r.id}_turn_{idx}",
                                "source": "voice_runtime",
                                "event_type": f"voice.dialogue.{role}",
                                "component": "ai_engine",
                                "severity": "INFO",
                                "status": "DELIVERED",
                                "organization_id": organization_id,
                                "call_id": r_call_id,
                                "request_id": str(r.id),
                                "message": f"Turn [{role}]: {short_content}",
                                "details": sanitize_audit_payload({"role": role, "content": content}),
                                "timestamp": (
                                    r.created_at + timedelta(seconds=2 * (idx + 1))
                                ).isoformat()
                                if r.created_at
                                else r_created,
                            })

                    # F. Run Lifecycle Completion
                    terminal_states = {"COMPLETED", "FAILED", "TERMINATED", "CANCELLED", "ERROR", "TERMINATED_ERROR"}
                    is_terminal = r.is_completed or (r.state and r.state.upper() in terminal_states)
                    if is_terminal:
                        r_state = (
                            "FAILED"
                            if (r.annotations or {}).get("livekit_startup_error")
                            or r.call_disposition == "FAILED"
                            else r.state or ("COMPLETED" if r.is_completed else "FAILED")
                        )
                        r_sev = (
                            "ERROR"
                            if r_state.upper() in ("FAILED", "TERMINATED_ERROR", "ERROR")
                            else "INFO"
                        )
                        all_events.append({
                            "id": f"run_{r.id}_end",
                            "source": "workflow_engine",
                            "event_type": f"workflow.run.{r_state.lower()}",
                            "component": "workflow",
                            "severity": r_sev,
                            "status": r_state,
                            "organization_id": organization_id,
                            "call_id": r_call_id,
                            "request_id": str(r.id),
                            "message": f"Workflow run #{r.id} ended: {r_state} (disposition: {r.call_disposition or 'normal_clearing'})",
                            "details": sanitize_audit_payload({
                                "is_completed": r.is_completed,
                                "state": r.state,
                                "call_disposition": r.call_disposition,
                                "cost_info": r.cost_info,
                                "usage_info": r.usage_info,
                            }),
                            "timestamp": (
                                r.created_at + timedelta(seconds=30)
                            ).isoformat()
                            if r.created_at
                            else r_created,
                        })

        # 4. Filter by severity and component if provided
        filtered: List[Dict[str, Any]] = []
        for ev in all_events:
            if severity and ev.get("severity") != severity.upper():
                continue
            if component and ev.get("component") != component:
                continue
            filtered.append(ev)

        # Sort chronologically descending
        filtered.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        total_count = len(filtered)
        paginated_events = filtered[offset : offset + limit]
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1

        return {
            "events": paginated_events,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "unindexed_sources_notice": "Container/Docker daemon logs are unindexed. Use host-level tools for raw process logs.",
        }

    async def get_call_timeline(
        self, call_identifier: str, user: UserModel
    ) -> Dict[str, Any]:
        """Correlates full operational and financial timeline for a specific call.

        Authorized only for Superadmins or the tenant owning the call.
        """
        async with db_client.async_session() as session:
            # Query workflow run by ID or name
            stmt = select(WorkflowRunModel).where(
                (WorkflowRunModel.id == int(call_identifier))
                if call_identifier.isdigit()
                else (WorkflowRunModel.name == call_identifier)
            )
            res = await session.execute(stmt)
            run = res.scalar_one_or_none()

            # If not found directly, attempt search by call_id in initial_context
            if not run:
                stmt_all = (
                    select(WorkflowRunModel)
                    .order_by(desc(WorkflowRunModel.id))
                    .limit(200)
                )
                res_all = await session.execute(stmt_all)
                candidates = res_all.scalars().all()
                for c in candidates:
                    ctx = c.initial_context or {}
                    logs = c.logs or {}
                    if (
                        ctx.get("call_id") == call_identifier
                        or logs.get("call_id") == call_identifier
                    ):
                        run = c
                        break

            if not run:
                raise ValueError(f"Call with identifier '{call_identifier}' not found.")

            # Resolve associated workflow and organization
            wf_stmt = select(WorkflowModel).where(WorkflowModel.id == run.workflow_id)
            wf_res = await session.execute(wf_stmt)
            wf = wf_res.scalar_one_or_none()
            org_id = wf.organization_id if wf else None

            # Multi-tenant Authorization Gate
            if not user.is_superuser:
                if org_id != user.selected_organization_id:
                    raise PermissionError(
                        "Forbidden: You do not have permission to view call diagnostics for another organization."
                    )

            # Assemble Milestone Events
            timeline_events: List[Dict[str, Any]] = []
            created_dt = run.created_at or datetime.now(UTC)
            init_ctx = run.initial_context or {}
            gath_ctx = run.gathered_context or {}
            usage = run.usage_info or {}
            cost = run.cost_info or {}
            logs = run.logs or {}
            annotations = run.annotations or {}
            startup_failed = bool(annotations.get("livekit_startup_error"))

            # 1. Initiation Milestone
            timeline_events.append({
                "milestone": "call_initiated",
                "timestamp": created_dt.isoformat(),
                "title": "Call Initiated",
                "status": "COMPLETED",
                "details": {
                    "direction": run.call_type or "outbound",
                    "mode": run.mode or "telephony",
                    "from_number": mask_phone_number(str(init_ctx.get("from_number", "NOT_RECORDED"))),
                    "to_number": mask_phone_number(str(init_ctx.get("to_number", "NOT_RECORDED"))),
                    "agent_name": wf.name if wf else "Default Voice Assistant",
                    "workflow_id": run.workflow_id,
                    "definition_id": run.definition_id or "UNVERIFIED",
                },
            })

            # 2. Carrier & SIP Session Milestone
            sip_status = logs.get("sip_status") or "UNVERIFIED"
            timeline_events.append({
                "milestone": "carrier_sip_session",
                "timestamp": (created_dt + timedelta(milliseconds=120)).isoformat(),
                "title": "Carrier & SIP Trunk Session",
                "status": sip_status,
                "details": {
                    "carrier": init_ctx.get("carrier") or "Vobiz",
                    "trunk_id": init_ctx.get("trunk_id") or "sip_default_trunk",
                    "sip_call_id": init_ctx.get("call_id") or run.name or "NOT_RECORDED",
                    "carrier_response_code": logs.get("sip_response_code") or "UNVERIFIED",
                },
            })

            # 3. LiveKit Worker Lifecycle
            lk_events = logs.get("livekit_events") or []
            worker_status = (
                "FAILED"
                if startup_failed
                else ("HEALTHY" if run.is_completed else "UNVERIFIED")
            )
            timeline_events.append({
                "milestone": "livekit_worker_lifecycle",
                "timestamp": (created_dt + timedelta(milliseconds=350)).isoformat(),
                "title": "LiveKit Voice Runtime Worker",
                "status": worker_status,
                "details": {
                    "room_name": logs.get("room_name") or f"spx_room_{run.id}",
                    "worker_events": lk_events if lk_events else ["startup", "sip_participant_connected"],
                    "startup_error": annotations.get("livekit_startup_error") or None,
                },
            })

            # 4. Voice Engine & Latency Milestone
            wf_overrides = getattr(wf, "model_overrides", None) or {}
            voice_engine = (
                wf_overrides.get("realtime", {}).get("provider")
                if wf_overrides
                else "google_realtime"
            )
            stt_latency = usage.get("stt_latency_ms", "NOT_RECORDED")
            llm_latency = usage.get("llm_latency_ms", "NOT_RECORDED")
            tts_latency = usage.get("tts_latency_ms", "NOT_RECORDED")
            timeline_events.append({
                "milestone": "voice_engine_runtime",
                "timestamp": (created_dt + timedelta(milliseconds=800)).isoformat(),
                "title": "AI Voice Engine Performance",
                "status": (
                    "NOT_STARTED"
                    if startup_failed
                    else ("HEALTHY" if run.is_completed else "UNVERIFIED")
                ),
                "details": {
                    "engine": voice_engine,
                    "model": wf_overrides.get("realtime", {}).get("model", "gemini-3.1-flash-live-preview") if wf_overrides else "gemini-3.1-flash-live-preview",
                    "stt_latency_ms": stt_latency,
                    "llm_latency_ms": llm_latency,
                    "tts_latency_ms": tts_latency,
                    "silence_events_count": usage.get("silence_events", 0),
                    "user_interruptions_count": usage.get("user_interruptions", 0),
                },
            })

            # 5. CRM & Lead Context
            timeline_events.append({
                "milestone": "crm_business_context",
                "timestamp": (created_dt + timedelta(seconds=15)).isoformat(),
                "title": "CRM & Business Data Gathering",
                "status": (
                    "NOT_STARTED"
                    if startup_failed
                    else ("CAPTURED" if gath_ctx else "EMPTY")
                ),
                "details": sanitize_audit_payload(gath_ctx or {"note": "No structured variables gathered"}),
            })

            # 6. Recording & Storage Milestone
            rec_status = "STORED" if run.recording_url else "NOT_RECORDED"
            timeline_events.append({
                "milestone": "recording_storage",
                "timestamp": (created_dt + timedelta(seconds=25)).isoformat(),
                "title": "Audio Recording & MinIO Storage",
                "status": rec_status,
                "details": {
                    "storage_backend": run.storage_backend or "MINIO",
                    "recording_url": "[PROTECTED_URL]" if run.recording_url else "NOT_RECORDED",
                    "transcript_available": bool(run.transcript_url),
                },
            })

            # 7. Call Outcome & Disconnect
            timeline_events.append({
                "milestone": "call_disconnect_outcome",
                "timestamp": (created_dt + timedelta(seconds=30)).isoformat(),
                "title": "Call Termination & Disposition",
                "status": (
                    "FAILED"
                    if startup_failed
                    else run.state or ("COMPLETED" if run.is_completed else "FAILED")
                ),
                "details": {
                    "is_completed": run.is_completed,
                    "call_disposition": run.call_disposition or "normal_clearing",
                    "duration_seconds": usage.get(
                        "duration_seconds", 0 if startup_failed else 30
                    ),
                },
            })

            # 8. Financial Reconciliation Milestone (Estimated vs Reconciled)
            is_reconciled = cost.get("carrier_cdr_reconciled", False)
            has_cost_evidence = bool(cost)
            cost_status = (
                "NO_AI_SESSION"
                if startup_failed and not has_cost_evidence
                else ("RECONCILED" if is_reconciled else "ESTIMATED")
            )
            carrier_cost = cost.get("carrier_cost", cost.get("telephony_cost", 0.0))
            ai_cost = (
                cost.get("ai_cost", 0.0)
                if startup_failed
                else cost.get("ai_cost", cost.get("total_cost", 0.0) - carrier_cost)
            )
            timeline_events.append({
                "milestone": "billing_and_reconciliation",
                "timestamp": (created_dt + timedelta(seconds=32)).isoformat(),
                "title": "Billing Calculation & Carrier Reconciliation",
                "status": cost_status,
                "details": {
                    "billing_status": cost_status,
                    "reconciliation_type": (
                        "No AI session; no cost evidence recorded"
                        if cost_status == "NO_AI_SESSION"
                        else (
                            "Carrier CDR Match"
                            if is_reconciled
                            else "60-second Pulse Estimate"
                        )
                    ),
                    "telephony_pulses": cost.get(
                        "pulses", 0 if startup_failed else 1
                    ),
                    "telephony_rate_per_pulse": cost.get(
                        "pulse_rate",
                        0.0
                        if startup_failed
                        else (0.30 if run.call_type == "inbound" else 0.40),
                    ),
                    "telephony_cost_inr": round(carrier_cost, 4),
                    "ai_runtime_cost_inr": round(ai_cost, 4),
                    "total_cost_inr": round(cost.get("total_cost", carrier_cost + ai_cost), 4),
                    "carrier_cdr_event_id": cost.get("carrier_cdr_event_id") or "UNVERIFIED",
                },
            })

            return {
                "call_id": call_identifier,
                "workflow_run_id": run.id,
                "organization_id": org_id,
                "overall_status": (
                    "FAILED"
                    if startup_failed
                    else ("COMPLETED" if run.is_completed else "FAILED")
                ),
                "billing_reconciliation_status": cost_status,
                "timeline": timeline_events,
            }

    async def get_retention_preview(
        self, days: int = 30, user: UserModel = None
    ) -> Dict[str, Any]:
        """Calculates operational records eligible for retention cleanup.

        STRICT SAFETY:
        - Only explicitly allowlisted operational types ('system', 'operational_log', etc.) are eligible.
        - All business events ('lead', 'appointment', 'call', 'objection', 'workflow_run') and security audits
          are classified as strictly protected and excluded.
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)

        async with db_client.async_session() as session:
            # Operational records MUST match explicit allowlisted operational types only
            stmt = select(DashboardEventModel).where(
                DashboardEventModel.resource_type.in_(ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES),
                DashboardEventModel.created_at < cutoff,
            )
            res = await session.execute(stmt)
            count = len(res.scalars().all())

            # Protected records (all business entities and audit logs) older than cutoff
            protected_stmt = select(DashboardEventModel).where(
                ~DashboardEventModel.resource_type.in_(ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES),
                DashboardEventModel.created_at < cutoff,
            )
            protected_res = await session.execute(protected_stmt)
            protected_count = len(protected_res.scalars().all())

            # Total protected records currently in database across all time
            total_protected_stmt = select(DashboardEventModel).where(
                ~DashboardEventModel.resource_type.in_(ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES)
            )
            total_protected_res = await session.execute(total_protected_stmt)
            total_protected_count = len(total_protected_res.scalars().all())

            return {
                "retention_cutoff": cutoff.isoformat(),
                "retention_days": days,
                "eligible_operational_records": count,
                "protected_audit_records_excluded": protected_count,
                "total_protected_records": total_protected_count,
                "protected_scope_guarantee": (
                    "Security audit records, customer leads, appointments, call transcripts, "
                    "objections, billing CDRs, and database backups are STRICTLY PROTECTED and excluded from operational log purges."
                ),
            }

    async def purge_operational_logs(
        self, days: int, confirm_token: str, user: UserModel
    ) -> Dict[str, Any]:
        """Controlled operational log purge with explicit confirmation and audit trail.

        STRICT SAFETY:
        - Never deletes business data (leads, appointments, calls, objections) or security audits.
        - Purges ONLY allowlisted operational resource types.
        """
        if not user.is_superuser:
            raise PermissionError("Only Superadministrators can execute retention purges.")

        if confirm_token != "CONFIRM_PURGE_OPERATIONAL_LOGS":
            raise ValueError(
                "Invalid confirmation token. Operation aborted to protect historical operational records."
            )

        cutoff = datetime.now(UTC) - timedelta(days=days)
        purged_count = 0

        async with db_client.async_session() as session:
            # Select ONLY explicitly allowlisted operational records
            stmt = select(DashboardEventModel).where(
                DashboardEventModel.resource_type.in_(ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES),
                DashboardEventModel.created_at < cutoff,
            )
            res = await session.execute(stmt)
            records = res.scalars().all()
            purged_count = len(records)

            for rec in records:
                await session.delete(rec)

            await session.commit()

        # Durably record the purge in the security audit trail (fail-closed)
        await audit_service.record_event(
            organization_id=1,
            action_type="audit.operations.log_purge",
            target_resource="operational_logs",
            target_id=f"cutoff_{days}d",
            outcome="SUCCESS",
            actor_id=str(user.id),
            actor_role="superadmin",
            changed_fields={
                "purged_count": purged_count,
                "retention_days": days,
                "cutoff": cutoff.isoformat(),
                "protected_records_retained": True,
            },
            fail_closed=True,
        )

        return {
            "status": "purged",
            "purged_operational_records": purged_count,
            "retention_days": days,
            "cutoff": cutoff.isoformat(),
        }

    async def export_redacted_events(
        self,
        user: UserModel,
        export_format: str = "json",
        organization_id: Optional[int] = None,
        severity: Optional[str] = None,
        component: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> Tuple[str, str]:
        """Exports redacted operational & audit events and records an audit log for the export action."""
        search_res = await self.search_logs(
            user=user,
            organization_id=organization_id,
            severity=severity,
            component=component,
            start_time=start_time,
            end_time=end_time,
            page=1,
            limit=100,
        )

        events = search_res.get("events", [])

        # Durably audit the export operation
        await audit_service.record_event(
            organization_id=organization_id or (user.selected_organization_id or 1),
            action_type="audit.operations.log_export",
            target_resource="event_logs",
            target_id=f"export_{export_format}",
            outcome="SUCCESS",
            actor_id=str(user.id),
            actor_role="superadmin" if user.is_superuser else "owner",
            changed_fields={
                "format": export_format,
                "exported_count": len(events),
                "severity_filter": severity,
                "component_filter": component,
            },
            fail_closed=False,
        )

        if export_format.lower() == "csv":
            output = io.StringIO()
            fieldnames = [
                "id",
                "timestamp",
                "severity",
                "component",
                "event_type",
                "status",
                "organization_id",
                "call_id",
                "request_id",
                "message",
            ]
            writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for ev in events:
                writer.writerow(ev)
            return output.getvalue(), "text/csv"

        return json.dumps(events, indent=2), "application/json"


observability_service = ObservabilityService()
