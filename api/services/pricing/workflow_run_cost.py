from datetime import UTC, datetime
from decimal import Decimal
import math
from typing import Any, Dict, Optional

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.pricing.cost_calculator import RATE_CARD_VERSION, cost_calculator, usd_to_inr


class BillingStatus:
    ESTIMATED = "ESTIMATED"
    PENDING_RECONCILIATION = "PENDING_RECONCILIATION"
    VERIFIED = "VERIFIED"
    RECONCILIATION_FAILED = "RECONCILIATION_FAILED"


def is_simulated_run(workflow_run) -> bool:
    """Accurately identify test and simulated calls to prevent charging them to commercial carriers."""
    if workflow_run is None:
        return False
    if getattr(workflow_run, "is_simulation", False):
        return True
    annotations = getattr(workflow_run, "annotations", None) or {}
    if annotations.get("is_simulation") or annotations.get("is_simulated"):
        return True
    initial_context = getattr(workflow_run, "initial_context", None) or {}
    if initial_context.get("is_simulation") or initial_context.get("is_simulated"):
        return True
    mode = (getattr(workflow_run, "mode", None) or "").lower()
    if "simulated" in mode or "simulation" in mode:
        return True
    name = getattr(workflow_run, "name", "") or ""
    if name.startswith("SIM-"):
        return True

    # Check if telephony call_id is absent or explicitly flagged as simulated
    cost_info = getattr(workflow_run, "cost_info", None) or {}
    gathered = getattr(workflow_run, "gathered_context", None) or {}
    call_id = (
        cost_info.get("call_id")
        or gathered.get("call_id")
        or gathered.get("CallUUID")
        or initial_context.get("call_id")
    )
    if not call_id or str(call_id).startswith(("sim_", "test_", "browser_")):
        return True
    return False


async def _get_telephony_provider_for_run(workflow_run, organization_id: int):
    from api.services.telephony.factory import get_telephony_provider_for_run

    return await get_telephony_provider_for_run(workflow_run, organization_id)


async def _fetch_telephony_cost(workflow_run) -> dict | None:
    """Fetch telephony call cost in the provider's native CDR currency."""
    if workflow_run.mode not in [
        WorkflowRunMode.TWILIO.value,
        WorkflowRunMode.VONAGE.value,
        WorkflowRunMode.VOBIZ.value,
    ]:
        return None

    cost_info = workflow_run.cost_info or {}
    gathered_context = workflow_run.gathered_context or {}
    initial_context = workflow_run.initial_context or {}
    call_id = (
        cost_info.get("call_id")
        or gathered_context.get("call_id")
        or gathered_context.get("CallUUID")
        or initial_context.get("call_id")
    )
    if not call_id:
        logger.warning("call_id not found in cost_info")
        return None

    provider_name = workflow_run.mode.lower() if workflow_run.mode else ""

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning("Workflow not found for workflow run")
        raise Exception("Workflow not found")

    provider = await _get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )
    call_cost_info = await provider.get_call_cost(call_id)

    if call_cost_info.get("status") == "error":
        logger.error(
            f"Failed to fetch {provider_name} call cost: {call_cost_info.get('error')}"
        )
        return None

    currency = str(
        call_cost_info.get("currency") or call_cost_info.get("price_unit") or "USD"
    ).upper()
    cost_usd = call_cost_info.get("cost_usd")
    cost_inr = call_cost_info.get("cost_inr")
    if cost_inr is None and currency == "INR":
        cost_inr = call_cost_info.get("cost")
    if cost_usd is None and currency == "USD":
        cost_usd = call_cost_info.get("cost")
    logged_cost = cost_inr if currency == "INR" else cost_usd
    logger.info(
        f"{provider_name.title()} call cost: {currency} "
        f"{float(logged_cost or 0):.6f} "
        f"for call {call_id}"
    )
    return {
        "cost_usd": float(cost_usd or 0),
        "cost_inr": float(cost_inr or 0),
        "currency": currency,
        "provider_name": provider_name,
        "call_id": call_id,
        "duration": call_cost_info.get("duration"),
        "status": call_cost_info.get("status"),
        "price_unit": currency,
        "call_rate": call_cost_info.get("call_rate"),
        "rate_inr_per_minute": call_cost_info.get("rate_inr_per_minute"),
        "rate_usd_per_minute": call_cost_info.get("rate_usd_per_minute"),
        "billing_increment_seconds": call_cost_info.get("billing_increment_seconds"),
        "minimum_duration_seconds": call_cost_info.get("minimum_duration_seconds"),
        "estimated": bool(call_cost_info.get("estimated")),
        "source": call_cost_info.get("source") or "provider_cdr",
        "source_url": call_cost_info.get("source_url"),
    }


def _runtime_configuration(workflow_run) -> dict | None:
    initial_context = getattr(workflow_run, "initial_context", None) or {}
    runtime_configuration = initial_context.get("runtime_configuration")
    return runtime_configuration if isinstance(runtime_configuration, dict) else None


def _add_telephony_component(actual_cost: dict, telephony_cost: dict) -> None:
    cost_usd = Decimal(str(telephony_cost.get("cost_usd") or 0))
    cost_inr = Decimal(str(telephony_cost.get("cost_inr") or 0))
    currency = str(telephony_cost.get("currency") or "USD").upper()
    if currency == "USD" and cost_inr == 0:
        cost_inr = usd_to_inr(cost_usd)
    provider_name = telephony_cost.get("provider_name") or "telephony"
    component = {
        "service": "telephony",
        "provider": provider_name,
        "model": "cdr",
        "label": f"{provider_name.title()} CDR",
        "currency": currency,
        "cost_usd": float(cost_usd),
        "cost_inr": float(cost_inr),
        "priced": True,
        "estimated": bool(telephony_cost.get("estimated")),
        "usage": {
            "call_id": telephony_cost.get("call_id"),
            "duration_seconds": telephony_cost.get("duration"),
            "status": telephony_cost.get("status"),
        },
        "pricing": {
            "currency": currency,
            "call_rate": telephony_cost.get("call_rate"),
            "rate_inr_per_minute": telephony_cost.get("rate_inr_per_minute"),
            "rate_usd_per_minute": telephony_cost.get("rate_usd_per_minute"),
            "billing_increment_seconds": telephony_cost.get(
                "billing_increment_seconds"
            ),
            "minimum_duration_seconds": telephony_cost.get("minimum_duration_seconds"),
        },
        "source_url": telephony_cost.get("source_url"),
        "note": (
            "Authoritative telephony CDR cost returned by the provider."
            if not telephony_cost.get("estimated")
            else "Estimated from provider account pricing because a CDR cost was not available."
        ),
    }
    actual_cost.setdefault("components", []).append(component)
    actual_cost["telephony_total_usd"] = float(
        Decimal(str(actual_cost.get("telephony_total_usd") or 0)) + cost_usd
    )
    actual_cost["total_usd"] = float(
        Decimal(str(actual_cost.get("total_usd") or 0)) + cost_usd
    )
    actual_cost["telephony_total_inr"] = float(
        Decimal(str(actual_cost.get("telephony_total_inr") or 0)) + cost_inr
    )
    actual_cost["total_inr"] = float(
        Decimal(str(actual_cost.get("total_inr") or 0)) + cost_inr
    )


async def _update_organization_usage(
    org,
    dograh_tokens: float,
    duration_seconds: float,
    *,
    idempotency_key: str | None = None,
    workflow_run_id: int | None = None,
) -> None:
    """Update organization usage after a workflow run."""
    org_id = org.id
    applied = await db_client.update_usage_after_run(
        org_id,
        dograh_tokens,
        duration_seconds,
        idempotency_key=idempotency_key,
        workflow_run_id=workflow_run_id,
    )
    if applied is False:
        logger.info(
            f"[Usage] organization usage already applied for {idempotency_key}; "
            f"org {org_id} unchanged"
        )
        return
    logger.info(
        f"Updated organization usage with {dograh_tokens} Dograh Tokens and {duration_seconds}s duration for org {org_id}"
    )


async def _get_pricing_organization(workflow_run):
    workflow = getattr(workflow_run, "workflow", None)
    organization_id = getattr(workflow, "organization_id", None)
    if organization_id is None and workflow and workflow.user:
        organization_id = workflow.user.selected_organization_id
    if organization_id is None:
        return None
    return await db_client.get_organization_by_id(organization_id)


async def _build_usage_cost_snapshot(
    usage_info: dict | None,
    *,
    workflow_run=None,
    include_telephony_cost: bool = False,
    organization=None,
    calculated_at: str | None = None,
) -> dict | None:
    if not usage_info:
        logger.warning("No usage info available for workflow run")
        return None

    cost_breakdown = cost_calculator.calculate_total_cost(usage_info)
    actual_cost = cost_calculator.calculate_actual_cost(
        usage_info,
        runtime_configuration=(
            _runtime_configuration(workflow_run) if workflow_run is not None else None
        ),
        calculated_at=calculated_at
        or (workflow_run.created_at.isoformat() if workflow_run is not None else None),
    )
    ai_breakdown_usd = (
        Decimal(str(cost_breakdown.get("llm_cost") or 0))
        + Decimal(str(cost_breakdown.get("tts_cost") or 0))
        + Decimal(str(cost_breakdown.get("stt_cost") or 0))
    )
    actual_ai_total_usd = Decimal(str(actual_cost.get("ai_total_usd") or 0))
    if actual_ai_total_usd > ai_breakdown_usd:
        realtime_estimate_usd = actual_ai_total_usd - ai_breakdown_usd
        cost_breakdown["realtime_duration_estimate"] = float(realtime_estimate_usd)
        cost_breakdown["total"] = float(
            Decimal(str(cost_breakdown["total"])) + realtime_estimate_usd
        )

    simulated = is_simulated_run(workflow_run) if workflow_run is not None else False
    billing_status = BillingStatus.ESTIMATED if simulated else BillingStatus.PENDING_RECONCILIATION

    if include_telephony_cost and workflow_run is not None and not simulated:
        try:
            telephony_cost = await _fetch_telephony_cost(workflow_run)
            if telephony_cost:
                telephony_cost_usd = float(telephony_cost.get("cost_usd") or 0)
                telephony_cost_inr = float(telephony_cost.get("cost_inr") or 0)
                provider_name = telephony_cost["provider_name"]
                if telephony_cost_usd:
                    cost_breakdown["telephony_call"] = telephony_cost_usd
                    cost_breakdown[f"{provider_name}_call"] = telephony_cost_usd
                    cost_breakdown["total"] = (
                        float(cost_breakdown["total"]) + telephony_cost_usd
                    )
                if telephony_cost_inr:
                    cost_breakdown["telephony_call_inr"] = telephony_cost_inr
                    cost_breakdown[f"{provider_name}_call_inr"] = telephony_cost_inr
                _add_telephony_component(actual_cost, telephony_cost)

                if telephony_cost.get("estimated"):
                    billing_status = BillingStatus.PENDING_RECONCILIATION
                elif telephony_cost.get("status") == "error":
                    billing_status = BillingStatus.RECONCILIATION_FAILED
                else:
                    billing_status = BillingStatus.VERIFIED
            else:
                billing_status = BillingStatus.PENDING_RECONCILIATION
        except Exception as e:
            logger.error(f"Failed to fetch telephony call cost: {e}")
            billing_status = BillingStatus.RECONCILIATION_FAILED
            # Don't fail the whole cost calculation if telephony API fails

    total_cost_usd = Decimal(str(cost_breakdown["total"]))
    dograh_tokens = float(total_cost_usd * Decimal("100"))

    dur = float(usage_info.get("call_duration_seconds", 0) or 0)
    billable_sec = int(math.ceil(dur / 60.0) * 60) if dur > 0 else 0
    billable_pulses = int(math.ceil(dur / 60.0)) if dur > 0 else 0
    mode_val = getattr(workflow_run, "mode", None) or "standard"

    cost_info = {
        "cost_breakdown": cost_breakdown,
        "total_cost_usd": float(total_cost_usd),
        "actual_cost": actual_cost,
        "dograh_token_usage": dograh_tokens,
        "calculated_at": calculated_at
        or (workflow_run.created_at.isoformat() if workflow_run is not None else None),
        "call_duration_seconds": dur,
        "billable_duration_seconds": billable_sec,
        "billable_pulses": billable_pulses,
        "billing_status": billing_status,
        "is_simulated": simulated,
        "rate_card_version": RATE_CARD_VERSION,
        "voice_mode": str(mode_val).lower(),
        "reconciliation_history": (workflow_run.cost_info or {}).get("reconciliation_history", []) if workflow_run else [],
    }

    return cost_info


async def build_workflow_run_cost_info(workflow_run) -> dict | None:
    cost_info = await _build_usage_cost_snapshot(
        workflow_run.usage_info,
        workflow_run=workflow_run,
        include_telephony_cost=True,
        calculated_at=workflow_run.created_at.isoformat(),
    )
    if cost_info is None:
        return None
    return {
        **(workflow_run.cost_info or {}),
        **cost_info,
    }


async def save_workflow_run_cost_info(
    workflow_run_id: int, cost_info: dict | None
) -> None:
    if cost_info is None:
        return
    await db_client.update_workflow_run(run_id=workflow_run_id, cost_info=cost_info)


async def apply_workflow_run_usage_to_organization(
    workflow_run, cost_info: dict | None
) -> None:
    if cost_info is None:
        return

    org = await _get_pricing_organization(workflow_run)
    if not org:
        return

    await _update_organization_usage(
        org,
        float(cost_info.get("dograh_token_usage") or 0),
        float(cost_info.get("call_duration_seconds") or 0),
        # Stable per-run key: a retried finalization replays this claim instead
        # of incrementing the organization's usage twice.
        idempotency_key=f"workflow_run_usage:{workflow_run.id}",
        workflow_run_id=workflow_run.id,
    )


async def apply_usage_delta_to_organization(
    workflow_run, usage_info: dict | None
) -> dict | None:
    org = await _get_pricing_organization(workflow_run)
    if not org:
        return None

    cost_info = await _build_usage_cost_snapshot(usage_info, organization=org)
    if cost_info is None:
        return None

    await _update_organization_usage(
        org,
        float(cost_info.get("dograh_token_usage") or 0),
        float(cost_info.get("call_duration_seconds") or 0),
    )
    return cost_info


async def calculate_workflow_run_cost(workflow_run_id: int):
    logger.debug("Calculating cost for workflow run")

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning("Workflow run not found")
        return

    try:
        cost_info = await build_workflow_run_cost_info(workflow_run)
        if cost_info is None:
            return

        await save_workflow_run_cost_info(workflow_run_id, cost_info)

        # The durable per-run claim makes retries safe. A failed increment
        # must reach the finalization coordinator so it can retry it.
        await apply_workflow_run_usage_to_organization(workflow_run, cost_info)

        logger.info(
            f"Calculated cost for workflow run: ${cost_info['total_cost_usd']:.6f} USD ({cost_info['dograh_token_usage']} Dograh Tokens)"
        )
    except Exception as e:
        logger.error(f"Error calculating cost for workflow run: {type(e).__name__}")
        raise


async def reconcile_workflow_run_cdr(
    workflow_run_id: int,
    cdr_data: dict,
    provider_name: str = "vobiz",
    source: str = "carrier_webhook",
) -> dict:
    """Reconcile carrier CDR with workflow run cost, enforcing idempotency and tenant safety.

    Guarantees:
    - Idempotency via ActionIdempotencyRecordModel (prevents duplicate charges on replayed CDRs)
    - Records audit trail of adjustments (never silently overwrites verified costs)
    - Emits durable audit event
    - Rejects commercial billing on simulated calls
    - Uses Decimal precision for currency arithmetic
    """
    from api.db.models import ActionIdempotencyRecordModel, WorkflowRunModel
    from api.services.audit.audit_service import audit_service

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        raise ValueError(f"Workflow run {workflow_run_id} not found")

    # Guard: never bill commercial carrier charges for simulated runs
    if is_simulated_run(workflow_run):
        logger.warning(
            f"[Billing] Refusing to reconcile carrier CDR on simulated run {workflow_run_id}"
        )
        return {
            "status": "REJECTED_SIMULATION",
            "reason": "Simulated calls cannot be billed to commercial carriers.",
            "workflow_run_id": workflow_run_id,
            "cost_info": workflow_run.cost_info,
        }

    org = await _get_pricing_organization(workflow_run)
    org_id = (
        org.id
        if org
        else (getattr(getattr(workflow_run, "workflow", None), "organization_id", None) or 1)
    )

    # 1. Reject mismatched carrier/call/tenant identities
    expected_call_id = (
        (workflow_run.cost_info or {}).get("call_id")
        or (workflow_run.gathered_context or {}).get("call_id")
        or (workflow_run.gathered_context or {}).get("CallUUID")
        or (workflow_run.initial_context or {}).get("call_id")
    )
    if (
        expected_call_id
        and cdr_data.get("call_id")
        and str(cdr_data["call_id"]) != str(expected_call_id)
    ):
        logger.warning(
            f"[Billing] Mismatched call_id on run {workflow_run_id}: expected {expected_call_id}, got {cdr_data['call_id']}"
        )
        return {
            "status": "REJECTED_MISMATCH",
            "reason": f"Call ID mismatch: expected {expected_call_id}, got {cdr_data['call_id']}",
            "workflow_run_id": workflow_run_id,
        }

    if cdr_data.get("organization_id") and int(cdr_data["organization_id"]) != int(org_id):
        logger.warning(
            f"[Billing] Mismatched organization_id on run {workflow_run_id}: expected {org_id}, got {cdr_data['organization_id']}"
        )
        return {
            "status": "REJECTED_MISMATCH",
            "reason": f"Organization mismatch: expected {org_id}, got {cdr_data['organization_id']}",
            "workflow_run_id": workflow_run_id,
        }

    call_id = (
        cdr_data.get("call_id")
        or cdr_data.get("call_uuid")
        or cdr_data.get("CallUUID")
        or cdr_data.get("CallSid")
        or str(expected_call_id or workflow_run_id)
    )

    cdr_event_id = (
        cdr_data.get("id")
        or cdr_data.get("record_id")
        or cdr_data.get("timestamp")
        or call_id
    )
    idempotency_key = f"cdr:{provider_name}:{call_id}:{cdr_event_id}"

    # 1. Check idempotency record to prevent duplicate reconciliation
    async with db_client.async_session() as session:
        stmt = select(ActionIdempotencyRecordModel).where(
            ActionIdempotencyRecordModel.idempotency_key == idempotency_key
        )
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing and existing.status == "COMPLETED":
            logger.info(
                f"[Billing] CDR {idempotency_key} already reconciled; returning existing result"
            )
            return {
                "status": "ALREADY_RECONCILED",
                "idempotent": True,
                "workflow_run_id": workflow_run_id,
                "cost_info": existing.result,
            }

        if not existing:
            idemp_record = ActionIdempotencyRecordModel(
                idempotency_key=idempotency_key,
                organization_id=org_id,
                workflow_run_id=workflow_run_id,
                action_type="cdr_reconciliation",
                status="IN_PROGRESS",
            )
            session.add(idemp_record)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                # Concurrently inserted, re-query
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                if existing and existing.status == "COMPLETED":
                    return {
                        "status": "ALREADY_RECONCILED",
                        "idempotent": True,
                        "workflow_run_id": workflow_run_id,
                        "cost_info": existing.result,
                    }

    # 2. Check for missing/error CDR
    raw_status = str(cdr_data.get("status") or "completed").lower()
    if raw_status in ("error", "failed_lookup", "missing") or cdr_data.get("missing"):
        current_cost = dict(workflow_run.cost_info or {})
        current_cost["billing_status"] = BillingStatus.PENDING_RECONCILIATION
        current_cost["reconciliation_error"] = str(
            cdr_data.get("error") or "CDR missing from carrier"
        )
        await save_workflow_run_cost_info(workflow_run_id, current_cost)
        return {
            "status": "PENDING_RECONCILIATION",
            "workflow_run_id": workflow_run_id,
            "cost_info": current_cost,
        }

    dur_sec = float(cdr_data.get("duration") or cdr_data.get("billsec") or 0.0)
    raw_cost = (
        cdr_data.get("total_cost")
        or cdr_data.get("cost")
        or cdr_data.get("cost_inr")
        or 0.0
    )
    cost_amount = Decimal(str(raw_cost))
    currency = str(cdr_data.get("currency") or "INR").upper()

    if currency == "USD":
        cost_usd = float(round(cost_amount, 4))
        cost_inr = float(round(usd_to_inr(cost_amount), 2))
    else:
        cost_inr = float(round(cost_amount, 2))
        cost_usd = float(round(cost_amount / Decimal("95.8945"), 4))

    # Do not mark unauthenticated or unverified provider data as VERIFIED
    is_unverified = (
        cdr_data.get("verified") is False
        or cdr_data.get("unverified") is True
        or raw_status in ("unverified", "pending", "simulated", "trial", "test")
    )
    target_status = (
        BillingStatus.PENDING_RECONCILIATION
        if is_unverified
        else BillingStatus.VERIFIED
    )

    # 3. Update cost_info and record adjustments in history under atomic call-level lock
    async with db_client.async_session() as session:
        stmt_lock = (
            select(WorkflowRunModel)
            .where(WorkflowRunModel.id == workflow_run_id)
            .with_for_update()
        )
        res_lock = await session.execute(stmt_lock)
        locked_run = res_lock.scalar_one_or_none()

        current_cost = (
            dict(locked_run.cost_info or {})
            if locked_run and locked_run.cost_info
            else dict(workflow_run.cost_info or {})
        )
        actual_cost = dict(current_cost.get("actual_cost") or {})
        prev_carrier_inr = float(actual_cost.get("telephony_total_inr") or 0.0)
        history = list(current_cost.get("reconciliation_history") or [])

        # Legitimate later CDR corrections: record adjustment history
        if (
            current_cost.get("billing_status") == BillingStatus.VERIFIED
            and prev_carrier_inr > 0
            and abs(prev_carrier_inr - cost_inr) > 0.001
        ):
            history.append({
                "original_value_inr": prev_carrier_inr,
                "new_value_inr": cost_inr,
                "difference_inr": round(cost_inr - prev_carrier_inr, 2),
                "reason": str(cdr_data.get("reason") or "Carrier CDR adjustment/update"),
                "timestamp": datetime.now(UTC).isoformat(),
                "provenance": provider_name,
                "cdr_event_id": str(cdr_event_id),
            })

        # Update or add telephony component
        components = list(actual_cost.get("components") or [])
        telephony_comp = next(
            (c for c in components if c.get("service") == "telephony"), None
        )
        if not telephony_comp:
            telephony_comp = {
                "service": "telephony",
                "provider": provider_name,
                "model": "cdr",
                "label": f"{provider_name.title()} CDR",
                "pricing": {},
            }
            components.append(telephony_comp)

        telephony_comp.update({
            "currency": currency,
            "cost_usd": cost_usd,
            "cost_inr": cost_inr,
            "priced": not is_unverified,
            "estimated": is_unverified,
            "source": source,
            "usage": {
                "call_id": call_id,
                "duration_seconds": dur_sec,
                "status": raw_status,
            },
            "note": (
                "Unverified carrier CDR payload; pending confirmation."
                if is_unverified
                else "Authoritative verified carrier CDR charge."
            ),
        })

        actual_cost["components"] = components
        actual_cost["telephony_total_usd"] = cost_usd
        actual_cost["telephony_total_inr"] = cost_inr

        # Recalculate totals
        ai_total_usd = float(actual_cost.get("ai_total_usd") or 0.0)
        ai_total_inr = float(actual_cost.get("ai_total_inr") or 0.0)
        actual_cost["total_usd"] = round(ai_total_usd + cost_usd, 4)
        actual_cost["total_inr"] = round(ai_total_inr + cost_inr, 2)

        current_cost["actual_cost"] = actual_cost
        current_cost["billing_status"] = target_status
        current_cost["reconciliation_history"] = history
        current_cost["reconciled_at"] = datetime.now(UTC).isoformat()
        current_cost["reconciled_provider"] = provider_name

        if locked_run:
            locked_run.cost_info = current_cost
        else:
            await save_workflow_run_cost_info(workflow_run_id, current_cost)

        # 4. Mark idempotency record COMPLETED in the same transaction
        stmt_idemp = select(ActionIdempotencyRecordModel).where(
            ActionIdempotencyRecordModel.idempotency_key == idempotency_key
        )
        res_idemp = await session.execute(stmt_idemp)
        rec = res_idemp.scalar_one_or_none()
        if rec:
            rec.status = "COMPLETED"
            rec.result = {"cost_inr": cost_inr, "status": target_status}

        await session.commit()

    cost_info = current_cost

    # 5. Emit durable audit event
    await audit_service.record_event(
        organization_id=org_id,
        action_type="audit.billing.cdr_reconciled",
        target_resource="workflow_run",
        target_id=workflow_run_id,
        actor_id=provider_name,
        actor_role="system",
        outcome="SUCCESS",
        changed_fields={
            "call_id": call_id,
            "telephony_spend_inr": cost_inr,
            "billing_status": BillingStatus.VERIFIED,
            "provenance": provider_name,
        },
        workflow_run_id=workflow_run_id,
        call_id=call_id,
    )

    return {
        "status": "VERIFIED",
        "workflow_run_id": workflow_run_id,
        "call_id": call_id,
        "cost_inr": cost_inr,
        "billing_status": BillingStatus.VERIFIED,
        "cost_info": cost_info,
    }
