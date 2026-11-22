import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import defer

from api.constants import AUTH_PROVIDER
from api.db import db_client
from api.db.filters import apply_workflow_run_filters
from api.db.models import UserModel, WorkflowModel, WorkflowRunModel
from api.schemas.auth import SignupRequest, UserResponse
from api.services.audit.audit_service import audit_service
from api.services.auth.depends import get_superuser
from api.services.auth.managed_org import assign_user_to_managed_organization
from api.services.auth.stack_auth import stackauth
from api.services.business.conversation_export import export_filename
from api.services.business.guardrails import check_rate_limit, require_feature
from api.services.business.conversation_query import _pstn_condition
from api.services.pricing.cost_analytics import (
    MAX_COST_EXPORT_ROWS,
    assemble_summary,
    build_cost_csv,
    build_cost_json,
    build_cost_zip,
    build_pricing_scan_stmt,
    build_status_stmt,
    build_summary_stmt,
    cost_row,
    summarize_pricing_inputs,
)
from api.services.pricing.workflow_run_cost import calculate_workflow_run_cost
from api.utils.auth import hash_password

router = APIRouter(prefix="/superuser", tags=["superuser"])


class ImpersonateRequest(BaseModel):
    """Request payload for superadmin impersonation.

    Either ``provider_user_id`` **or** ``user_id`` must be supplied. If both are
    provided, ``provider_user_id`` takes precedence.
    """

    provider_user_id: str | None = None
    user_id: int | None = None


class ImpersonateResponse(BaseModel):
    refresh_token: str
    access_token: str


class CreateManagedUserRequest(SignupRequest):
    is_superuser: bool = False


class SuperuserWorkflowRunResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: Optional[str]
    user_id: Optional[int]
    organization_id: Optional[int]
    organization_name: Optional[str]
    mode: str
    is_completed: bool
    recording_url: Optional[str]
    transcript_url: Optional[str]
    usage_info: Optional[dict]
    cost_info: Optional[dict]
    initial_context: Optional[dict]
    gathered_context: Optional[dict]
    created_at: datetime
    call_origin: Optional[str] = None
    cost_summary: Optional[dict] = None
    duration_seconds: Optional[float] = None
    conversation_state: Optional[str] = None


class SuperuserWorkflowRunsListResponse(BaseModel):
    workflow_runs: List[SuperuserWorkflowRunResponse]
    total_count: int
    page: int
    limit: int
    total_pages: int


class RecalculateWorkflowRunCostResponse(BaseModel):
    status: str
    workflow_run_id: int


@router.post("/impersonate")
async def impersonate(
    request: ImpersonateRequest, user: UserModel = Depends(get_superuser)
) -> ImpersonateResponse:
    """Impersonate a user as a super-admin.
    Internally, Stack Auth requires the **provider user ID** (a UUID-ish string)
    to create an impersonation session.
    """

    if AUTH_PROVIDER != "stack":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="User impersonation is only supported in hosted Stack Auth deployments. In local OSS mode, superadmins have direct access to all tenant workflows.",
        )

    provider_user_id: str | None = request.provider_user_id

    # ------------------------------------------------------------------
    # Fallback: resolve provider_user_id from internal ``user_id``
    # ------------------------------------------------------------------
    if provider_user_id is None:
        if request.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'provider_user_id' or 'user_id' must be provided.",
            )

        db_user = await db_client.get_user_by_id(request.user_id)

        if db_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {request.user_id} not found.",
            )

        provider_user_id = db_user.provider_id

    # ------------------------------------------------------------------
    # Call Stack Auth to create the impersonation session
    # ------------------------------------------------------------------
    session = await stackauth.impersonate(provider_user_id)

    return ImpersonateResponse(
        refresh_token=session["refresh_token"],
        access_token=session["access_token"],
    )


@router.post("/users", response_model=UserResponse)
async def create_managed_user(
    request: CreateManagedUserRequest,
    user: UserModel = Depends(get_superuser),
) -> UserResponse:
    """Create a local user in the single managed organization."""
    existing_user = await db_client.get_user_by_email(request.email)
    if existing_user:
        raise HTTPException(status_code=409, detail="Email already registered")

    new_user = await db_client.create_user_with_email(
        email=request.email,
        password_hash=hash_password(request.password),
        name=request.name,
    )

    if request.is_superuser:
        await db_client.update_user_superuser(new_user.id, True)
        new_user.is_superuser = True

    organization, _ = await assign_user_to_managed_organization(new_user)

    return UserResponse(
        id=new_user.id,
        email=new_user.email,
        name=request.name,
        organization_id=organization.id,
        provider_id=new_user.provider_id,
        is_superuser=new_user.is_superuser,
    )


@router.get("/workflow-runs")
async def get_workflow_runs(
    page: int = Query(1, ge=1, description="Page number (starts from 1)"),
    limit: int = Query(50, ge=1, le=100, description="Number of items per page"),
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_superuser),
) -> SuperuserWorkflowRunsListResponse:
    """
    Get paginated list of all workflow runs with organization information.
    Requires superuser privileges.

    Filters should be provided as a JSON-encoded array of filter criteria.
    Example: [{"field": "id", "type": "number", "value": {"value": 680}}]
    """
    offset = (page - 1) * limit

    # Parse filters if provided
    filter_criteria = None
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

    # Validate sort_order
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    workflow_runs, total_count = await db_client.get_workflow_runs_for_superadmin(
        limit=limit,
        offset=offset,
        filters=filter_criteria,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    total_pages = (total_count + limit - 1) // limit  # Ceiling division

    return SuperuserWorkflowRunsListResponse(
        workflow_runs=[SuperuserWorkflowRunResponse(**run) for run in workflow_runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.post(
    "/workflow-runs/{workflow_run_id}/recalculate-cost",
    response_model=RecalculateWorkflowRunCostResponse,
)
async def recalculate_workflow_run_cost(
    workflow_run_id: int,
    user: UserModel = Depends(get_superuser),
) -> RecalculateWorkflowRunCostResponse:
    """Rebuild provider cost info for a single run, including fresh CDR lookup."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    await calculate_workflow_run_cost(workflow_run_id)

    org_id = (
        workflow_run.workflow.organization_id
        if workflow_run and getattr(workflow_run, "workflow", None)
        else (user.selected_organization_id or 1)
    )
    await audit_service.record_event(
        organization_id=org_id,
        action_type="audit.billing.reprice_triggered",
        target_resource="workflow_run",
        target_id=str(workflow_run_id),
        actor_id=str(user.id),
        actor_role="superadmin",
        outcome="SUCCESS",
        changed_fields={"workflow_run_id": workflow_run_id, "triggered_by": user.email},
        workflow_run_id=workflow_run_id,
    )
    return RecalculateWorkflowRunCostResponse(
        status="success",
        workflow_run_id=workflow_run_id,
    )


def _parse_filters(filters: Optional[str]) -> Optional[list]:
    if not filters:
        return None
    try:
        parsed = json.loads(filters)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid filter format")
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise HTTPException(status_code=400, detail="filters must be a JSON array of objects")
    return parsed


@router.get("/cost-summary")
async def get_cost_summary(
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    user: UserModel = Depends(get_superuser),
) -> dict:
    """Aggregate cost over the *entire* filtered set, with explicit estimate/reconciled basis."""
    criteria = _parse_filters(filters)
    async with db_client.async_session() as session:
        totals = (await session.execute(build_summary_stmt(criteria))).one()
        status_rows = (await session.execute(build_status_stmt(criteria))).all()
        infos = [row[0] for row in (await session.execute(build_pricing_scan_stmt(criteria))).all()]
        pstn_stmt = apply_workflow_run_filters(
            select(func.count(WorkflowRunModel.id))
            .select_from(WorkflowRunModel)
            .where(_pstn_condition()),
            criteria,
        )
        pstn_runs = (await session.execute(pstn_stmt)).scalar() or 0
    return assemble_summary(totals, status_rows, summarize_pricing_inputs(infos), int(pstn_runs))


@router.get("/cost-export")
async def export_cost_records(
    format: str = Query("csv"),
    ids: Optional[str] = Query(None, description="Comma-separated run ids"),
    filters: Optional[str] = Query(None),
    user: UserModel = Depends(get_superuser),
) -> StreamingResponse:
    """CSV / JSON / ZIP export of selected or filtered financial records (estimates are labelled)."""
    require_feature("exports", "Exports are unavailable.")
    await check_rate_limit("export", getattr(user, "id", 0))
    fmt = (format or "csv").lower()
    if fmt not in ("csv", "json", "zip"):
        raise HTTPException(status_code=400, detail="format must be csv, json or zip")
    criteria = _parse_filters(filters)
    run_ids = None
    if ids:
        try:
            run_ids = list(dict.fromkeys(int(x) for x in ids.split(",") if x.strip()))
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be comma-separated integers")
        if not run_ids or len(run_ids) > 1000:
            raise HTTPException(status_code=400, detail="Select between 1 and 1000 runs")

    def _scoped(stmt):
        stmt = apply_workflow_run_filters(stmt, criteria)
        return stmt.where(WorkflowRunModel.id.in_(run_ids)) if run_ids else stmt

    async with db_client.async_session() as session:
        total = (
            await session.execute(
                _scoped(select(func.count(WorkflowRunModel.id)).select_from(WorkflowRunModel))
            )
        ).scalar() or 0
        if total > MAX_COST_EXPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"{total} runs match; export is limited to {MAX_COST_EXPORT_ROWS}. Narrow the filters.",
            )
        rows = (
            await session.execute(
                _scoped(
                    select(WorkflowRunModel, WorkflowModel.name, WorkflowModel.organization_id)
                    .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                    .options(defer(WorkflowRunModel.logs))
                ).order_by(WorkflowRunModel.created_at.desc(), WorkflowRunModel.id.desc())
            )
        ).all()
    records = [cost_row(run, workflow_name=name, organization_id=org_id) for run, name, org_id in rows]
    if fmt == "json":
        payload, media = build_cost_json(records, filters={"filtered": bool(criteria), "selected_ids": bool(run_ids)}), "application/json; charset=utf-8"
    elif fmt == "zip":
        payload, media = build_cost_zip(records, filters={"filtered": bool(criteria), "selected_ids": bool(run_ids)}), "application/zip"
    else:
        payload, media = build_cost_csv(records), "text/csv; charset=utf-8"

    audit_org = getattr(user, "selected_organization_id", None)
    if isinstance(audit_org, int) and not isinstance(audit_org, bool):
        try:
            await audit_service.record_event(
                organization_id=audit_org,
                action_type="audit.export.cost_records",
                target_resource="cost_export",
                target_id=str(len(records)),
                actor_id=str(user.id),
                actor_role="superadmin",
                changed_fields={"count": len(records), "selected_ids": bool(run_ids), "format": fmt},
            )
        except Exception as exc:
            logger.warning(f"[Audit] cost export audit write failed: {type(exc).__name__}")
    return StreamingResponse(
        iter([payload]),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{export_filename("spx-cost-records", fmt)}"',
            "Cache-Control": "no-store",
            "X-Export-Count": str(len(records)),
        },
    )
