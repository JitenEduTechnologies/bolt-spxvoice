"""Health and Diagnostic Routes (Phase C3).

Implements:
- GET /health: Public liveness check (backward compatible with Docker/Coolify)
- GET /health/liveness: Minimal fast process liveness probe
- GET /health/readiness: Database and Redis readiness probe (returns 503 if data plane down)
- GET /health/status: Superadmin-only comprehensive diagnostic report
- GET /health/tenant: Tenant-scoped isolated health report
- POST /health/alerts/{incident_id}/acknowledge: Superadmin alert acknowledgment
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from loguru import logger
from pydantic import BaseModel

from api.constants import (
    APP_VERSION,
    AUTH_PROVIDER,
    BACKEND_API_ENDPOINT,
    DEPLOYMENT_MODE,
    DISABLE_HOSTED_CLOUD,
    FORCE_TURN_RELAY,
    TURN_SECRET,
)
from api.db import db_client
from api.db.models import UserModel
from api.services.auth.depends import get_superuser, get_user
from api.services.configuration.provider_readiness import deployment_provider_status
from api.services.health.alert_manager import alert_manager
from api.services.health.durable_incident_store import durable_incident_store
from api.services.health.external_notifier import external_notifier
from api.services.health.models import (
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    IncidentEvent,
    SystemHealthReport,
)
from api.services.health.observability_service import observability_service
from api.services.health.provider_monitor import provider_monitor
from api.services.health.system_monitor import system_monitor
from api.services.livekit.client import is_livekit_runtime, livekit_configured
from api.utils.common import get_backend_endpoints

router = APIRouter(prefix="/health", tags=["health"])


class LegacyHealthResponse(BaseModel):
    """Preserves full backward compatibility with existing HealthResponse in main.py."""
    status: str
    version: str
    backend_api_endpoint: str
    deployment_mode: str
    auth_provider: str
    turn_enabled: bool
    force_turn_relay: bool
    hosted_services_enabled: bool
    voice_runtime: str
    livekit_enabled: bool
    # Expected, non-failing state on a fresh install: "provider_unconfigured"
    # until a model-provider key is saved from the dashboard.
    provider_status: str = "unknown"


HealthResponse = LegacyHealthResponse


@router.get("", response_model=LegacyHealthResponse)
async def legacy_health() -> LegacyHealthResponse:
    """Fast, non-blocking liveness endpoint for Docker healthchecks and reverse proxies."""
    backend_endpoint = BACKEND_API_ENDPOINT or "http://localhost:8000"
    return LegacyHealthResponse(
        status="ok",
        version=APP_VERSION,
        backend_api_endpoint=backend_endpoint,
        deployment_mode=DEPLOYMENT_MODE,
        auth_provider=AUTH_PROVIDER,
        turn_enabled=bool(TURN_SECRET),
        force_turn_relay=FORCE_TURN_RELAY,
        hosted_services_enabled=not DISABLE_HOSTED_CLOUD,
        voice_runtime="livekit",
        livekit_enabled=is_livekit_runtime() and livekit_configured(),
        provider_status=await deployment_provider_status(db_client),
    )


@router.get("/liveness")
async def liveness() -> Dict[str, Any]:
    """Lightweight process liveness probe."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "service": "spx_voice_backend",
    }


@router.get("/readiness")
async def readiness(response: Response) -> Dict[str, Any]:
    """Verifies that core data dependencies (Postgres & Redis) are ready."""
    db_res = await system_monitor.check_database()
    redis_res = await system_monitor.check_redis()

    is_ready = (
        db_res.status == HealthStatus.HEALTHY
        and redis_res.status == HealthStatus.HEALTHY
    )

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # provider_unconfigured is informational and never flips readiness.
    return {
        "status": "ready" if is_ready else "not_ready",
        "providers": {"gemini_realtime": await deployment_provider_status(db_client)},
        "postgresql": {
            "status": db_res.status.value,
            "latency_ms": db_res.latency_ms,
        },
        "redis": {
            "status": redis_res.status.value,
            "latency_ms": redis_res.latency_ms,
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/status")
async def comprehensive_diagnostics(
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Superadmin-only full operational health report, host metrics, and active incidents."""
    # 1. System infrastructure health
    system_report = await system_monitor.get_system_health()

    # 2. Carrier and AI provider health
    providers = await provider_monitor.check_all_providers()

    # 3. Evaluate alert rules and record any new or resolved incidents
    await alert_manager.evaluate_system_report(
        report=system_report,
        provider_components=providers,
    )

    active_alerts = alert_manager.get_active_incidents()
    history = alert_manager.get_incident_history(limit=20)

    return {
        "overall_status": system_report.overall_status.value,
        "liveness": system_report.liveness,
        "readiness": system_report.readiness,
        "components": {k: v.model_dump() for k, v in system_report.components.items()},
        "host_metrics": system_report.host_metrics.model_dump() if system_report.host_metrics else None,
        "providers": {k: v.model_dump() for k, v in providers.items()},
        "active_alerts": [a.model_dump() for a in active_alerts],
        "recent_incidents": [h.model_dump() for h in history],
        "external_notifications": {
            "status": external_notifier.get_configuration_status(),
        },
        "evaluated_at": system_report.evaluated_at.isoformat(),
    }


@router.get("/tenant")
async def tenant_health(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Tenant-scoped isolated health report.
    
    Strictly isolated: returns ONLY customer's registered DIDs and sanitized status.
    Never exposes host CPU/RAM/Disk or platform-level details.
    """
    org_id = user.selected_organization_id
    if not org_id:
        raise HTTPException(
            status_code=400,
            detail="No organization selected for user",
        )

    from api.services.telephony.smart_did import get_smart_did_controller

    controller = get_smart_did_controller()
    dids = controller.get_dids_for_organization(org_id)

    did_summary = []
    for d in dids:
        did_summary.append({
            "phone_number": d.phone_number,
            "carrier": d.carrier,
            "state": d.state.value,
            "active_calls": d.active_calls,
            "total_calls": d.total_calls,
            "success_rate": round(d.successful_calls / max(1, d.total_calls), 2),
            "is_in_cooldown": d.is_in_cooldown(),
        })

    return {
        "organization_id": org_id,
        "phone_numbers": did_summary,
        "calling_ready": any(not d.get("is_in_cooldown") and d.get("state") == "active" for d in did_summary) if did_summary else False,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.post("/alerts/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: str,
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Acknowledges an active incident (Superadmin only)."""
    success = await alert_manager.acknowledge_incident(incident_id, actor_id=str(user.id))
    if not success:
        raise HTTPException(status_code=404, detail=f"Active incident {incident_id} not found")
    return {"status": "acknowledged", "incident_id": incident_id}


class PurgeRequest(BaseModel):
    days: int = 30
    confirm_token: str


class ExportRequest(BaseModel):
    format: str = "json"
    organization_id: Optional[int] = None
    severity: Optional[str] = None
    component: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@router.get("/incidents")
async def get_durable_incidents(
    severity: Optional[str] = None,
    component: Optional[str] = None,
    is_resolved: Optional[bool] = None,
    is_acknowledged: Optional[bool] = None,
    is_synthetic: Optional[bool] = None,
    correlation_id: Optional[str] = None,
    call_id: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Superadmin-only durable incident history query with server-side filtering."""
    limit = min(max(1, limit), 100)
    offset = (page - 1) * limit
    incidents, total_count = await durable_incident_store.query_incidents(
        severity=severity,
        component=component,
        is_resolved=is_resolved,
        is_acknowledged=is_acknowledged,
        is_synthetic=is_synthetic,
        correlation_id=correlation_id,
        call_id=call_id,
        limit=limit,
        offset=offset,
    )
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    return {
        "incidents": incidents,
        "total_count": total_count,
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
    }


@router.get("/logs")
async def search_centralized_logs(
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
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Superadmin-only centralized log search across C1 traces, C2 audits, and C3/C4 incidents."""
    try:
        return await observability_service.search_logs(
            user=user,
            start_time=start_time,
            end_time=end_time,
            severity=severity,
            component=component,
            organization_id=organization_id,
            call_id=call_id,
            request_id=request_id,
            run_id=run_id,
            event_type=event_type,
            page=page,
            limit=limit,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/calls/{call_id}/timeline")
async def get_call_timeline(
    call_id: str,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Correlates complete operational and financial timeline for an authorized call."""
    try:
        return await observability_service.get_call_timeline(call_identifier=call_id, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.get("/retention/preview")
async def get_retention_preview(
    days: int = 30,
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Previews records matching operational retention criteria (Superadmin only)."""
    return await observability_service.get_retention_preview(days=days, user=user)


@router.post("/retention/purge")
async def purge_operational_logs(
    req: PurgeRequest,
    user: UserModel = Depends(get_superuser),
) -> Dict[str, Any]:
    """Purges eligible operational logs with explicit confirmation (Superadmin only)."""
    try:
        return await observability_service.purge_operational_logs(
            days=req.days, confirm_token=req.confirm_token, user=user
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@router.post("/export")
async def export_logs(
    req: ExportRequest,
    user: UserModel = Depends(get_superuser),
) -> Response:
    """Exports redacted audit and incident events (Superadmin only)."""
    content, media_type = await observability_service.export_redacted_events(
        user=user,
        export_format=req.format,
        organization_id=req.organization_id,
        severity=req.severity,
        component=req.component,
        start_time=req.start_time,
        end_time=req.end_time,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=spx_logs_export.{req.format}"},
    )
