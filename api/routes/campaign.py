import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from api.constants import (
    DEFAULT_CAMPAIGN_RETRY_CONFIG,
    DEFAULT_ORG_CONCURRENCY_LIMIT,
    REDIS_URL,
)
from api.db import db_client
from api.db.models import QueuedRunModel, UserModel, WorkflowRunModel
from api.enums import OrganizationConfigurationKey
from api.services.auth.depends import get_user
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.campaign.source_sync_factory import get_sync_service
from api.services.quota_service import check_dograh_quota
from api.services.reports import generate_campaign_report_csv
from api.services.storage import storage_fs

router = APIRouter(prefix="/campaign")


async def _get_org_concurrent_limit(organization_id: int) -> int:
    """Get the concurrent call limit for an organization."""
    try:
        config = await db_client.get_configuration(
            organization_id,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value,
        )
        if config and config.value:
            return int(config.value.get("value", DEFAULT_ORG_CONCURRENCY_LIMIT))
    except Exception:
        pass
    return DEFAULT_ORG_CONCURRENCY_LIMIT


async def _get_from_numbers_count(organization_id: int) -> int:
    """Active phone-number count from the org's default telephony config.
    Used to validate ``max_concurrency`` against caller-id supply."""
    try:
        default_cfg = await db_client.get_default_telephony_configuration(
            organization_id
        )
        if default_cfg:
            addresses = await db_client.list_active_normalized_addresses_for_config(
                default_cfg.id
            )
            return len(addresses)
    except Exception:
        pass
    return 0


async def _validate_max_concurrency(max_concurrency: int, organization_id: int) -> None:
    """Validate max_concurrency against org limit and configured phone numbers.

    Raises HTTPException(400) if the value exceeds the effective limit.
    """
    org_limit = await _get_org_concurrent_limit(organization_id)
    from_numbers_count = await _get_from_numbers_count(organization_id)
    effective_limit = (
        min(org_limit, from_numbers_count) if from_numbers_count > 0 else org_limit
    )
    if max_concurrency > effective_limit:
        if from_numbers_count > 0 and from_numbers_count < org_limit:
            raise HTTPException(
                status_code=400,
                detail=f"max_concurrency ({max_concurrency}) cannot exceed {effective_limit}. You have {from_numbers_count} phone number(s) configured. Add more CLIs in telephony configuration to increase concurrency.",
            )
        raise HTTPException(
            status_code=400,
            detail=f"max_concurrency ({max_concurrency}) cannot exceed organization limit ({effective_limit})",
        )


class RetryConfigRequest(BaseModel):
    enabled: bool = True
    max_retries: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: int = Field(default=120, ge=30, le=3600)
    retry_on_busy: bool = True
    retry_on_no_answer: bool = True
    retry_on_voicemail: bool = True


class RetryConfigResponse(BaseModel):
    enabled: bool = True
    max_retries: int = 2
    retry_delay_seconds: int = 120
    retry_on_busy: bool = True
    retry_on_no_answer: bool = True
    retry_on_voicemail: bool = True


class TimeSlotRequest(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6)
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")

    @model_validator(mode="after")
    def validate_times(self):
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ScheduleConfigRequest(BaseModel):
    enabled: bool = True
    timezone: str = "UTC"
    slots: List[TimeSlotRequest] = Field(..., min_length=1, max_length=50)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (KeyError, Exception):
            raise ValueError(f"Invalid timezone: {v}")
        return v


class TimeSlotResponse(BaseModel):
    day_of_week: int = 0
    start_time: str = "09:00"
    end_time: str = "17:00"


class ScheduleConfigResponse(BaseModel):
    enabled: bool = False
    timezone: str = "UTC"
    slots: List[TimeSlotResponse] = Field(default_factory=list)


class CircuitBreakerConfigRequest(BaseModel):
    enabled: bool = True
    failure_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    window_seconds: int = Field(default=120, ge=30, le=600)
    min_calls_in_window: int = Field(default=5, ge=1, le=100)


class CircuitBreakerConfigResponse(BaseModel):
    enabled: bool = False
    failure_threshold: float = 0.5
    window_seconds: int = 120
    min_calls_in_window: int = 5


class CreateCampaignRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    workflow_id: int
    source_type: str = Field(..., pattern="^csv$")
    source_id: str  # CSV file key
    telephony_configuration_id: Optional[int] = None
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=100)
    schedule_config: Optional[ScheduleConfigRequest] = None
    circuit_breaker: Optional[CircuitBreakerConfigRequest] = None
    field_mapping: Optional[Dict[str, str]] = None


class UpdateCampaignRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    retry_config: Optional[RetryConfigRequest] = None
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=100)
    schedule_config: Optional[ScheduleConfigRequest] = None
    circuit_breaker: Optional[CircuitBreakerConfigRequest] = None
    field_mapping: Optional[Dict[str, str]] = None


class CampaignLogEntryResponse(BaseModel):
    ts: str = ""
    level: str = "info"
    event: str = "log"
    message: str = ""
    details: Optional[Dict[str, Any]] = None


class CampaignResponse(BaseModel):
    id: int
    name: str
    workflow_id: int
    workflow_name: str
    state: str
    source_type: str
    source_id: str
    total_rows: Optional[int]
    processed_rows: int
    failed_rows: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    retry_config: RetryConfigResponse
    max_concurrency: Optional[int] = None
    schedule_config: Optional[ScheduleConfigResponse] = None
    circuit_breaker: Optional[CircuitBreakerConfigResponse] = None
    executed_count: int = 0
    total_queued_count: int = 0
    parent_campaign_id: Optional[int] = None
    redialed_campaign_id: Optional[int] = None
    telephony_configuration_id: Optional[int] = None
    telephony_configuration_name: Optional[str] = None
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    logs: List[CampaignLogEntryResponse] = Field(default_factory=list)


class CampaignsResponse(BaseModel):
    campaigns: List[CampaignResponse]
    total_count: int = 0
    page: int = 1
    limit: Optional[int] = None
    total_pages: int = 1


class WorkflowRunResponse(BaseModel):
    id: int
    workflow_id: int
    state: str
    created_at: datetime
    completed_at: Optional[datetime]


class CampaignRunsResponse(BaseModel):
    """Paginated response for campaign workflow runs"""

    runs: List[dict]
    total_count: int
    page: int
    limit: int
    total_pages: int


class CampaignProgressResponse(BaseModel):
    campaign_id: int
    state: str
    total_rows: int
    processed_rows: int
    failed_calls: int
    progress_percentage: float
    source_sync: dict
    rate_limit: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


class CampaignOwnerSummaryResponse(BaseModel):
    campaign_id: int
    name: str
    state: str
    workflow_id: int
    workflow_name: str
    is_archived: bool = False
    total_contacts: int = 0
    attempted_contacts: int = 0
    remaining_contacts: int = 0
    completion_percentage: float = 0.0
    connected_calls: int = 0
    no_answer_calls: int = 0
    busy_calls: int = 0
    failed_calls: int = 0
    hot_leads_count: int = 0
    warm_leads_count: int = 0
    cold_leads_count: int = 0
    callback_requested_count: int = 0
    appointments_booked_count: int = 0
    retries_pending: int = 0
    retries_completed: int = 0
    retries_exhausted: int = 0
    estimated_spend_inr: float = 0.0
    is_simulation: bool = False
    emergency_stop_active: bool = False


class RedialCandidatePreview(BaseModel):
    source_uuid: str
    reason: str = "Unreachable"
    phone_number: Optional[str] = None
    contact_name: Optional[str] = None


class RedialPreviewResponse(BaseModel):
    campaign_id: int
    total_eligible: int = 0
    voicemail_count: int = 0
    no_answer_count: int = 0
    busy_count: int = 0
    sample_candidates: List[RedialCandidatePreview] = Field(default_factory=list)


def _build_campaign_response(
    campaign,
    workflow_name: str,
    executed_count: int = 0,
    total_queued_count: int = 0,
    telephony_configuration_name: Optional[str] = None,
) -> CampaignResponse:
    """Build a CampaignResponse from a campaign model."""
    retry_config = (
        campaign.retry_config
        if campaign.retry_config
        else DEFAULT_CAMPAIGN_RETRY_CONFIG
    )

    max_concurrency = None
    schedule_config = None
    circuit_breaker_config = CircuitBreakerConfigResponse()
    parent_campaign_id = None
    redialed_campaign_id = None
    if campaign.orchestrator_metadata:
        max_concurrency = campaign.orchestrator_metadata.get("max_concurrency")
        sc = campaign.orchestrator_metadata.get("schedule_config")
        if sc:
            schedule_config = ScheduleConfigResponse(
                enabled=sc.get("enabled", False),
                timezone=sc.get("timezone", "UTC"),
                slots=[TimeSlotResponse(**slot) for slot in sc.get("slots", [])],
            )
        cb = campaign.orchestrator_metadata.get("circuit_breaker")
        if cb:
            circuit_breaker_config = CircuitBreakerConfigResponse(**cb)
        parent_campaign_id = campaign.orchestrator_metadata.get("parent_campaign_id")
        redialed_campaign_id = campaign.orchestrator_metadata.get(
            "redialed_campaign_id"
        )

    effective_state = campaign.state
    meta = campaign.orchestrator_metadata or {}
    if campaign.state == "completed" and (
        meta.get("cancellation_reason")
        or any(
            entry.get("event") == "campaign_cancelled"
            for entry in (campaign.logs or [])
            if isinstance(entry, dict)
        )
    ):
        effective_state = "cancelled"

    is_archived = bool(meta.get("is_archived", False))
    archived_at = None
    raw_archived_at = meta.get("archived_at")
    if raw_archived_at:
        try:
            archived_at = datetime.fromisoformat(raw_archived_at)
        except Exception:
            pass

    return CampaignResponse(
        id=campaign.id,
        name=campaign.name,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name,
        state=effective_state,
        source_type=campaign.source_type,
        source_id=campaign.source_id,
        total_rows=campaign.total_rows,
        processed_rows=campaign.processed_rows,
        failed_rows=campaign.failed_rows,
        created_at=campaign.created_at,
        started_at=campaign.started_at,
        completed_at=campaign.completed_at,
        retry_config=RetryConfigResponse(**retry_config),
        max_concurrency=max_concurrency,
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        executed_count=executed_count,
        total_queued_count=total_queued_count,
        parent_campaign_id=parent_campaign_id,
        redialed_campaign_id=redialed_campaign_id,
        telephony_configuration_id=campaign.telephony_configuration_id,
        telephony_configuration_name=telephony_configuration_name,
        is_archived=is_archived,
        archived_at=archived_at,
        logs=[
            CampaignLogEntryResponse(**entry)
            for entry in (campaign.logs or [])
            if isinstance(entry, dict)
        ],
    )


async def _get_campaign_stats(campaign_id: int) -> tuple[int, int]:
    """Return (executed_count, total_queued_count) for a campaign."""
    stats_map = await db_client.get_queued_runs_stats_for_campaigns([campaign_id])
    s = stats_map.get(campaign_id, {})
    return s.get("executed", 0), s.get("total", 0)


async def _get_telephony_configuration_name(
    config_id: Optional[int], organization_id: int
) -> Optional[str]:
    """Resolve the display name for a campaign's telephony configuration."""
    if config_id is None:
        return None
    cfg = await db_client.get_telephony_configuration_for_org(
        config_id, organization_id
    )
    return cfg.name if cfg else None


@router.post("/create")
async def create_campaign(
    request: CreateCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Create a new campaign with optional retry, concurrency, and schedule configs."""
    workflow = await db_client.get_workflow(
        request.workflow_id, user.selected_organization_id
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow_name = workflow.name

    if not request.source_id.startswith(f"campaigns/{user.selected_organization_id}/"):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Source file does not belong to your organization",
        )

    if request.max_concurrency is not None:
        await _validate_max_concurrency(
            request.max_concurrency, user.selected_organization_id
        )

    telephony_configuration_id = request.telephony_configuration_id
    if telephony_configuration_id is not None:
        cfg = await db_client.get_telephony_configuration_for_org(
            telephony_configuration_id, user.selected_organization_id
        )
        if not cfg:
            raise HTTPException(
                status_code=404,
                detail=f"Telephony configuration {telephony_configuration_id} not found in this organization",
            )
    else:
        default_cfg = await db_client.get_default_telephony_configuration(
            user.selected_organization_id
        )
        if default_cfg:
            telephony_configuration_id = default_cfg.id

    retry_config = None
    if request.retry_config:
        retry_config = request.retry_config.model_dump()

    schedule_config = None
    if request.schedule_config:
        schedule_config = request.schedule_config.model_dump()

    circuit_breaker_config = None
    if request.circuit_breaker:
        circuit_breaker_config = request.circuit_breaker.model_dump()

    campaign = await db_client.create_campaign(
        name=request.name,
        workflow_id=request.workflow_id,
        source_type=request.source_type,
        source_id=request.source_id,
        user_id=user.id,
        organization_id=user.selected_organization_id,
        retry_config=retry_config,
        max_concurrency=request.max_concurrency,
        schedule_config=schedule_config,
        circuit_breaker=circuit_breaker_config,
        telephony_configuration_id=telephony_configuration_id,
        field_mapping=request.field_mapping,
    )

    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign, workflow_name, telephony_configuration_name=cfg_name
    )


@router.get("/")
async def get_campaigns(
    search: Optional[str] = Query(None, description="Search campaign name or ID"),
    status: Optional[str] = Query(None, description="Filter by status or 'archived'"),
    workflow_id: Optional[int] = Query(None, description="Filter by workflow ID"),
    sort_by: Optional[str] = Query("created_at", description="Sort by: created_at, name, id, progress"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc or desc"),
    include_archived: bool = Query(False, description="Whether to include archived campaigns"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: Optional[int] = Query(None, ge=1, le=200, description="Items per page"),
    user: UserModel = Depends(get_user),
) -> CampaignsResponse:
    """Get campaigns for user's organization with search, filtering, and pagination"""
    campaigns = await db_client.get_campaigns(user.selected_organization_id)

    workflow_ids = list(set(c.workflow_id for c in campaigns))
    workflows = await db_client.get_workflows_by_ids(
        workflow_ids, user.selected_organization_id
    )
    workflow_map = {w.id: w.name for w in workflows}

    stats_map = await db_client.get_queued_runs_stats_for_campaigns(
        [c.id for c in campaigns]
    )

    org_configs = await db_client.list_telephony_configurations(
        user.selected_organization_id
    )
    config_name_map = {cfg.id: cfg.name for cfg in org_configs}

    all_responses = [
        _build_campaign_response(
            c,
            workflow_map.get(c.workflow_id, "Unknown"),
            executed_count=stats_map.get(c.id, {}).get("executed", 0),
            total_queued_count=stats_map.get(c.id, {}).get("total", 0),
            telephony_configuration_name=config_name_map.get(
                c.telephony_configuration_id
            ),
        )
        for c in campaigns
    ]

    filtered = all_responses

    if not include_archived and status != "archived":
        filtered = [c for c in filtered if not c.is_archived]
    elif status == "archived":
        filtered = [c for c in filtered if c.is_archived]

    if status and status.lower() not in ("all", "archived"):
        filtered = [c for c in filtered if c.state.lower() == status.lower()]

    if workflow_id is not None:
        filtered = [c for c in filtered if c.workflow_id == workflow_id]

    if search:
        s_term = search.strip().lower()
        filtered = [
            c for c in filtered
            if s_term in c.name.lower() or s_term in str(c.id) or s_term in c.workflow_name.lower()
        ]

    reverse = (sort_order or "desc").lower() == "desc"
    if sort_by == "name":
        filtered.sort(key=lambda c: (c.name or "").lower(), reverse=reverse)
    elif sort_by == "id":
        filtered.sort(key=lambda c: c.id, reverse=reverse)
    elif sort_by == "progress":
        filtered.sort(key=lambda c: (c.executed_count, c.total_queued_count), reverse=reverse)
    else:
        filtered.sort(key=lambda c: c.created_at or datetime.min, reverse=reverse)

    total_count = len(filtered)

    if limit is not None:
        total_pages = max(1, (total_count + limit - 1) // limit)
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        page_items = filtered[start_idx:end_idx]
    else:
        page_items = filtered
        total_pages = 1
        limit = total_count

    return CampaignsResponse(
        campaigns=page_items,
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@router.get("/{campaign_id}")
async def get_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Get campaign details"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/start")
async def start_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Start campaign execution with strict safety, compliance, and telephony checks."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # 1. Emergency Stop Check (Fail-closed)
    try:
        import redis.asyncio as aioredis
        r_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            stop_val = await r_client.get(f"emergency_stop:{user.selected_organization_id}")
            if stop_val:
                raise HTTPException(
                    status_code=403,
                    detail="Cannot start campaign: Emergency Stop is currently ACTIVE for this organization. All outbound calls are blocked.",
                )
        finally:
            await r_client.aclose()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Emergency stop check error: {e}")

    # 2. Telephony Configuration & Caller ID Check
    if not campaign.telephony_configuration_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot start campaign: Telephony configuration is not assigned. Please edit the campaign and assign a telephony provider & caller ID.",
        )

    quota_result = await check_dograh_quota(user, workflow_id=campaign.workflow_id)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    try:
        await campaign_runner_service.start_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/pause")
async def pause_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Pause campaign execution"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        await campaign_runner_service.pause_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/resume")
async def resume_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Resume a paused campaign"""
    configs = await db_client.list_telephony_configurations(
        user.selected_organization_id
    )
    if not configs:
        raise HTTPException(
            status_code=401,
            detail="You must configure telephony first by going to APP_URL/configure-telephony",
        )

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    quota_result = await check_dograh_quota(user, workflow_id=campaign.workflow_id)
    if not quota_result.has_quota:
        raise HTTPException(status_code=402, detail=quota_result.error_message)

    try:
        await campaign_runner_service.resume_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/cancel")
async def cancel_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Cancel campaign execution and stop all pending dispatches"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        await campaign_runner_service.cancel_campaign(campaign_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)
    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    request: UpdateCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Update campaign settings (name, retry config, max concurrency, schedule)"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.state in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot update a {campaign.state} campaign",
        )

    if request.max_concurrency is not None:
        await _validate_max_concurrency(
            request.max_concurrency, user.selected_organization_id
        )

    update_kwargs = {}

    if request.name is not None:
        update_kwargs["name"] = request.name

    if request.retry_config is not None:
        update_kwargs["retry_config"] = request.retry_config.model_dump()

    metadata = campaign.orchestrator_metadata or {}
    metadata_changed = False

    if request.max_concurrency is not None:
        metadata["max_concurrency"] = request.max_concurrency
        metadata_changed = True

    if request.schedule_config is not None:
        metadata["schedule_config"] = request.schedule_config.model_dump()
        metadata_changed = True

    if request.circuit_breaker is not None:
        metadata["circuit_breaker"] = request.circuit_breaker.model_dump()
        metadata_changed = True

    if metadata_changed:
        update_kwargs["orchestrator_metadata"] = metadata

    if update_kwargs:
        await db_client.update_campaign(campaign_id=campaign_id, **update_kwargs)

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)

    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/archive")
async def archive_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Archive a campaign. Only non-running campaigns can be archived."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.state in ["running", "paused"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot archive a campaign in '{campaign.state}' state. Stop or cancel it first.",
        )

    meta = dict(campaign.orchestrator_metadata or {})
    meta["is_archived"] = True
    meta["archived_at"] = datetime.now(timezone.utc).isoformat()

    now_iso = datetime.now(timezone.utc).isoformat()
    logs = list(campaign.logs or [])
    logs.append({
        "ts": now_iso,
        "level": "info",
        "event": "campaign_archived",
        "message": f"Campaign archived by user {user.email}",
    })

    await db_client.update_campaign(
        campaign_id=campaign_id,
        orchestrator_metadata=meta,
        logs=logs,
    )

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)
    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.post("/{campaign_id}/unarchive")
async def unarchive_campaign(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Unarchive an archived campaign."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    meta = dict(campaign.orchestrator_metadata or {})
    meta.pop("is_archived", None)
    meta.pop("archived_at", None)

    now_iso = datetime.now(timezone.utc).isoformat()
    logs = list(campaign.logs or [])
    logs.append({
        "ts": now_iso,
        "level": "info",
        "event": "campaign_unarchived",
        "message": f"Campaign unarchived by user {user.email}",
    })

    await db_client.update_campaign(
        campaign_id=campaign_id,
        orchestrator_metadata=meta,
        logs=logs,
    )

    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)
    executed, total = await _get_campaign_stats(campaign.id)
    cfg_name = await _get_telephony_configuration_name(
        campaign.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        campaign,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.get("/{campaign_id}/owner-summary")
async def get_campaign_owner_summary(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignOwnerSummaryResponse:
    """Return authoritative business-owner KPI metrics for the campaign."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    workflow_name = await db_client.get_workflow_name(campaign.workflow_id, user.id)
    meta = campaign.orchestrator_metadata or {}
    is_archived = bool(meta.get("is_archived", False))

    async with db_client.async_session() as session:
        # Queued runs breakdown
        q_res = await session.execute(
            select(QueuedRunModel.state, QueuedRunModel.retry_count)
            .where(QueuedRunModel.campaign_id == campaign_id)
        )
        q_rows = q_res.fetchall()

        total_queued = len(q_rows)
        remaining = sum(1 for r in q_rows if r[0] == "queued")
        attempted = sum(1 for r in q_rows if r[0] in ("processed", "processing", "completed", "failed"))
        retries_pending = sum(1 for r in q_rows if r[0] == "queued" and (r[1] or 0) > 0)
        retries_completed = sum(1 for r in q_rows if r[0] in ("processed", "completed") and (r[1] or 0) > 0)

        max_retries = (campaign.retry_config or {}).get("max_retries", 2)
        retries_exhausted = sum(1 for r in q_rows if (r[1] or 0) >= max_retries)

        # Workflow runs breakdown
        w_res = await session.execute(
            select(
                WorkflowRunModel.call_disposition,
                WorkflowRunModel.state,
                WorkflowRunModel.annotations,
                WorkflowRunModel.cost_info,
                WorkflowRunModel.is_simulation,
            ).where(WorkflowRunModel.campaign_id == campaign_id)
        )
        w_rows = w_res.fetchall()

        connected = 0
        no_answer = 0
        busy = 0
        failed = 0
        hot_leads = 0
        warm_leads = 0
        cold_leads = 0
        callbacks = 0
        appointments = 0
        total_paise = 0
        is_simulation_all = True if w_rows else False

        for disp, state, ann, cost, is_sim in w_rows:
            disp_lower = (disp or "").lower()
            state_lower = (state or "").lower()

            if disp_lower in ("completed", "answered", "connected") or state_lower == "completed":
                connected += 1
            elif "no-answer" in disp_lower or "no_answer" in disp_lower:
                no_answer += 1
            elif "busy" in disp_lower or "voicemail" in disp_lower:
                busy += 1
            elif state_lower == "failed" or "failed" in disp_lower:
                failed += 1

            if not is_sim:
                is_simulation_all = False

            if isinstance(ann, dict):
                lead_data = ann.get("lead", {}) or {}
                score = lead_data.get("score")
                qual = str(lead_data.get("qualification", "")).lower()
                if score is not None:
                    try:
                        s_val = float(score)
                        if s_val >= 70 or qual == "hot":
                            hot_leads += 1
                        elif s_val >= 40 or qual == "warm":
                            warm_leads += 1
                        else:
                            cold_leads += 1
                    except Exception:
                        pass
                elif qual == "hot":
                    hot_leads += 1
                elif qual == "warm":
                    warm_leads += 1
                elif qual == "cold":
                    cold_leads += 1

                if ann.get("callback_requested") or ann.get("callback_time"):
                    callbacks += 1
                if ann.get("appointment_booked") or ann.get("appointment_id"):
                    appointments += 1

            if isinstance(cost, dict):
                total_paise += cost.get("total_cost_paise", 0)

    emergency_stop_active = False
    try:
        import redis.asyncio as aioredis
        r_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        try:
            stop_val = await r_client.get(f"emergency_stop:{campaign.organization_id}")
            if stop_val:
                emergency_stop_active = True
        finally:
            await r_client.aclose()
    except Exception:
        pass

    total_contacts = total_queued if total_queued > 0 else (campaign.total_rows or 0)
    comp_pct = round((attempted / total_contacts * 100), 1) if total_contacts > 0 else 0.0

    return CampaignOwnerSummaryResponse(
        campaign_id=campaign.id,
        name=campaign.name,
        state=campaign.state,
        workflow_id=campaign.workflow_id,
        workflow_name=workflow_name or "Unknown",
        is_archived=is_archived,
        total_contacts=total_contacts,
        attempted_contacts=attempted,
        remaining_contacts=remaining,
        completion_percentage=comp_pct,
        connected_calls=connected,
        no_answer_calls=no_answer,
        busy_calls=busy,
        failed_calls=failed,
        hot_leads_count=hot_leads,
        warm_leads_count=warm_leads,
        cold_leads_count=cold_leads,
        callback_requested_count=callbacks,
        appointments_booked_count=appointments,
        retries_pending=retries_pending,
        retries_completed=retries_completed,
        retries_exhausted=retries_exhausted,
        estimated_spend_inr=round(total_paise / 100.0, 2),
        is_simulation=is_simulation_all,
        emergency_stop_active=emergency_stop_active,
    )


@router.get("/{campaign_id}/redial-preview")
async def get_redial_preview(
    campaign_id: int,
    retry_on_voicemail: bool = Query(True),
    retry_on_no_answer: bool = Query(True),
    retry_on_busy: bool = Query(True),
    user: UserModel = Depends(get_user),
) -> RedialPreviewResponse:
    """Preview count and sample of eligible candidates before redialing."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    candidates = await db_client.get_redial_candidates(
        campaign_id=campaign.id,
        include_voicemail=retry_on_voicemail,
        include_no_answer=retry_on_no_answer,
        include_busy=retry_on_busy,
    )

    sample = []
    for c in candidates[:5]:
        ctx = c.get("context_variables") or {}
        phone = ctx.get("phone_number") or ctx.get("phone") or ctx.get("caller_id") or "Unknown"
        name = ctx.get("name") or ctx.get("customer_name") or ctx.get("full_name") or "Contact"
        sample.append(RedialCandidatePreview(
            source_uuid=str(c.get("source_uuid")),
            reason="Unreachable (No Answer / Busy / Voicemail)",
            phone_number=str(phone),
            contact_name=str(name),
        ))

    return RedialPreviewResponse(
        campaign_id=campaign_id,
        total_eligible=len(candidates),
        voicemail_count=len(candidates) if retry_on_voicemail else 0,
        no_answer_count=len(candidates) if retry_on_no_answer else 0,
        busy_count=len(candidates) if retry_on_busy else 0,
        sample_candidates=sample,
    )


@router.get("/{campaign_id}/runs")
async def get_campaign_runs(
    campaign_id: int,
    page: int = 1,
    limit: int = 50,
    filters: Optional[str] = Query(None, description="JSON-encoded filter criteria"),
    sort_by: Optional[str] = Query(
        None, description="Field to sort by (e.g., 'duration', 'created_at')"
    ),
    sort_order: Optional[str] = Query(
        "desc", description="Sort order ('asc' or 'desc')"
    ),
    user: UserModel = Depends(get_user),
) -> CampaignRunsResponse:
    """Get campaign workflow runs with pagination, filters and sorting"""
    offset = (page - 1) * limit

    filter_criteria = []
    if filters:
        try:
            filter_criteria = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid filter format")

        allowed_attributes = {
            "dateRange",
            "dispositionCode",
            "duration",
            "status",
            "tokenUsage",
        }
        for filter_item in filter_criteria:
            attribute = filter_item.get("attribute")
            if attribute and attribute not in allowed_attributes:
                raise HTTPException(
                    status_code=403, detail=f"Invalid attribute '{attribute}'"
                )

    try:
        runs, total_count = await db_client.get_campaign_runs_paginated(
            campaign_id,
            user.selected_organization_id,
            limit=limit,
            offset=offset,
            filters=filter_criteria if filter_criteria else None,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    total_pages = (total_count + limit - 1) // limit

    return CampaignRunsResponse(
        runs=[run.model_dump() for run in runs],
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


class RedialCampaignRequest(BaseModel):
    name: Optional[str] = Field(
        None, min_length=1, max_length=255, description="Name for the redial campaign"
    )
    retry_on_voicemail: bool = True
    retry_on_no_answer: bool = True
    retry_on_busy: bool = True
    retry_config: Optional[RetryConfigRequest] = None

    @model_validator(mode="after")
    def validate_at_least_one_reason(self):
        if not (
            self.retry_on_voicemail or self.retry_on_no_answer or self.retry_on_busy
        ):
            raise ValueError(
                "At least one of retry_on_voicemail, retry_on_no_answer, "
                "retry_on_busy must be true"
            )
        return self


@router.post("/{campaign_id}/redial")
async def redial_campaign(
    campaign_id: int,
    request: RedialCampaignRequest,
    user: UserModel = Depends(get_user),
) -> CampaignResponse:
    """Create a new campaign that re-dials unique subscribers from a completed
    campaign whose latest call resulted in voicemail, no-answer, or busy.
    """
    parent = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if parent.state != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Only completed campaigns can be redialed (current state: {parent.state})",
        )

    parent_meta = parent.orchestrator_metadata or {}
    if parent_meta.get("redialed_campaign_id"):
        raise HTTPException(
            status_code=400,
            detail="This campaign has already been redialed",
        )

    candidates = await db_client.get_redial_candidates(
        campaign_id=parent.id,
        include_voicemail=request.retry_on_voicemail,
        include_no_answer=request.retry_on_no_answer,
        include_busy=request.retry_on_busy,
    )
    if not candidates:
        raise HTTPException(
            status_code=400,
            detail="No subscribers match the selected redial criteria",
        )

    queued_runs_data = [
        {
            "campaign_id": 0,
            "source_uuid": c["source_uuid"],
            "context_variables": c["context_variables"],
            "state": "queued",
        }
        for c in candidates
    ]

    retry_config = (
        request.retry_config.model_dump()
        if request.retry_config
        else parent.retry_config
    )
    new_name = request.name or f"{parent.name} (Redial)"

    try:
        child = await db_client.create_redial_campaign(
            parent_campaign=parent,
            new_name=new_name,
            retry_config=retry_config,
            queued_runs_data=queued_runs_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    workflow_name = await db_client.get_workflow_name(child.workflow_id, user.id)
    executed, total = await _get_campaign_stats(child.id)
    cfg_name = await _get_telephony_configuration_name(
        child.telephony_configuration_id, user.selected_organization_id
    )
    return _build_campaign_response(
        child,
        workflow_name or "Unknown",
        executed,
        total,
        telephony_configuration_name=cfg_name,
    )


@router.get("/{campaign_id}/progress")
async def get_campaign_progress(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignProgressResponse:
    """Get current campaign progress and statistics"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        progress = await campaign_runner_service.get_campaign_status(campaign_id)
        return CampaignProgressResponse(**progress)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class CampaignPreviewCSVRequest(BaseModel):
    source_id: str
    field_mapping: Optional[Dict[str, str]] = None


@router.post("/preview-csv")
async def preview_campaign_csv(
    request: CampaignPreviewCSVRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Previews CSV headers, inferred field mapping, sample data, and row count."""
    if not request.source_id.startswith(f"campaigns/{user.selected_organization_id}/"):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Source file does not belong to your organization",
        )

    from api.services.campaign.sources.csv import CSVSyncService
    from api.services.campaign.source_sync import CampaignSourceSyncService, ValidationError

    sync_service = CSVSyncService()
    try:
        csv_data = await sync_service._fetch_csv_data(request.source_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to load CSV: {e}")

    if not csv_data or len(csv_data) < 2:
        raise HTTPException(status_code=400, detail="CSV must contain a header row and at least one data row")

    headers = [h.strip() for h in csv_data[0]]
    data_rows = csv_data[1:]

    try:
        resolved = CampaignSourceSyncService.resolve_column_mapping(headers, request.field_mapping)
        is_valid = True
        error_msg = None
    except ValidationError as err:
        resolved = {"phone_col": None, "name_col": None, "inferred_mapping": {}}
        is_valid = False
        error_msg = err.message

    sample_preview = []
    for r in data_rows[:5]:
        padded = r + [""] * (len(headers) - len(r))
        sample_preview.append(dict(zip(headers, padded)))

    return {
        "headers": headers,
        "is_valid": is_valid,
        "error": error_msg,
        "resolved_mapping": resolved.get("inferred_mapping", {}),
        "phone_column": resolved.get("phone_col"),
        "total_rows_estimate": len(data_rows),
        "sample_rows": sample_preview,
    }


class CampaignSourceDownloadResponse(BaseModel):
    download_url: str
    expires_in: int


@router.get("/{campaign_id}/source-download-url")
async def get_campaign_source_download_url(
    campaign_id: int,
    user: UserModel = Depends(get_user),
) -> CampaignSourceDownloadResponse:
    """Get presigned download URL for campaign CSV source file"""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.source_type != "csv":
        raise HTTPException(
            status_code=400,
            detail=f"Download URL only available for CSV sources. This campaign uses {campaign.source_type}",
        )

    if not campaign.source_id.startswith(f"campaigns/{user.selected_organization_id}/"):
        raise HTTPException(
            status_code=403,
            detail="Access denied: Source file does not belong to your organization",
        )

    try:
        download_url = await storage_fs.aget_signed_url(
            campaign.source_id,
            expiration=3600,
        )

        if not download_url:
            raise HTTPException(
                status_code=500, detail="Failed to generate download URL"
            )

        return CampaignSourceDownloadResponse(
            download_url=download_url, expires_in=3600
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to generate download URL: {str(e)}"
        )


@router.get("/{campaign_id}/report")
async def download_campaign_report(
    campaign_id: int,
    user: UserModel = Depends(get_user),
    start_date: Optional[datetime] = Query(
        None, description="Filter runs created on or after this datetime (ISO 8601)"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Filter runs created on or before this datetime (ISO 8601)"
    ),
) -> StreamingResponse:
    """Download a CSV report of completed campaign runs."""
    campaign = await db_client.get_campaign(campaign_id, user.selected_organization_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    output, filename = await generate_campaign_report_csv(
        campaign_id, start_date=start_date, end_date=end_date
    )

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
