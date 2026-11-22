from __future__ import annotations



"""Owner-Facing Universal Business API Routes for SPX Voice MVP.



Covers:

- Universal Agent Training (Presets, Drafts, Publishing, Version History, Runtime Updates)

- Conversation History & Transcripts

- AI Lead Intelligence & Evidence-Based Scoring

- Overview Analytics & KPIs

- Inbound/Outbound Local Simulation

"""



import copy

import math

from datetime import UTC, datetime, timedelta

from typing import Any, Dict, List, Optional
import uuid



from fastapi import APIRouter, Depends, Header, HTTPException, Query

from fastapi.responses import StreamingResponse

from loguru import logger

from pydantic import BaseModel, Field

from sqlalchemy.future import select

from sqlalchemy import func
from sqlalchemy.orm import defer



from api.db import db_client

from api.db.models import (

    CampaignModel,

    UserModel,

    WorkflowDefinitionModel,

    WorkflowModel,

    WorkflowRunModel,

    DashboardEventModel,

    ChatwootOutboxJobModel,

    ObjectionResponseModel,

    CustomerProfileModel,

)

from api.enums import CallType, WorkflowRunState

from api.services.auth.depends import get_user

from api.services.business.agent_training import (

    UNIVERSAL_INDUSTRY_PRESETS,

    check_budget_mode_request,

    compile_agent_training_to_workflow,

    find_missing_provider_config,

    get_preset_by_id,

    resolve_max_call_duration_seconds,

    resolve_product_mode_overrides,

)

from api.services.business.lead_intelligence import (
    analyze_lead_from_transcript,
    extract_transcript_from_logs,
    get_industry_recommended_action,
    resolve_industry_from_context_and_intent,
)

from api.services.audit.audit_service import audit_service

from api.services.business.guardrails import check_rate_limit, require_feature
from api.services.business.conversation_export import (
    EXPORT_FORMATS,
    build_csv,
    build_json,
    build_zip,
    export_filename,
    transcript_as_text,
)
from api.services.business.lead_intelligence import ANALYZER_VERSION
from api.services.business.conversation_query import (
    MAX_BULK_SELECTION,
    MAX_EXPORT_ROWS,
    MAX_JSON_TRANSCRIPT_ROWS,
    MAX_PAGE_SIZE,
    MAX_ZIP_ROWS,
    MAX_ZIP_RECORDINGS,
    MAX_RECORDING_BYTES,
    MAX_TOTAL_RECORDING_BYTES,
    ConversationFilters,
    ConversationQueryError,
    SelectionChangedError,
    build_conversation_item,
    list_conversation_items,
    parse_date_bound,
    resolve_selection,
)
from api.services.business.conversation_view import (
    OWNER_OVERRIDABLE_FIELDS,
    TERMINAL_ANALYSIS_STATUSES,
    apply_ai_analysis,
    apply_archive,
    apply_owner_override,
    apply_restore,
    apply_trash,
    classify_call_origin,
    recording_status,
    resolve_duration_seconds,
    timed_transcript_turns,
    transcript_hash,
    transcript_text,
)
from api.services.pricing.cost_calculator import (

    calculate_call_cost,

    check_budget_admission,

)

from api.services.telephony.ivr_service import (

    BusinessIVRConfig,

    generate_inbound_ivr_xml,

)

from api.utils.common import get_backend_endpoints

from api.services.events.realtime_bus import realtime_dashboard_bus

from api.services.chatwoot.outbox import chatwoot_outbox_service

from api.services.telephony.compliance import normalize_phone_number



router = APIRouter(prefix="/business", tags=["business"])

# Authoritative platform agreed tariff rates (single source of truth for commercial billing & UI displays)
AUTHORITATIVE_PLATFORM_RATES = {
    "budget": {"name": "Budget IVR", "rate_per_min_inr": 0.85, "pulse_seconds": 60},
    "standard": {"name": "Standard (Gemini Live)", "rate_per_min_inr": 1.80, "pulse_seconds": 60},
    "premium": {"name": "Premium (DeepSeek + Cartesia)", "rate_per_min_inr": 3.20, "pulse_seconds": 60},
}





# ---------------------------------------------------------------------------

# Schemas

# ---------------------------------------------------------------------------



class AgentTrainingDraftRequest(BaseModel):

    training_data: Dict[str, Any]

    workflow_id: Optional[int] = None





class UpdateLeadRequest(BaseModel):

    lead_classification: Optional[str] = None

    lead_score: Optional[int] = None

    customer_requirements: Optional[Dict[str, Any]] = None

    appointment_status: Optional[str] = None

    owner_notes: Optional[str] = None

    recommended_next_action: Optional[str] = None
    title: Optional[str] = None
    reset_fields: Optional[List[str]] = None





class ExecuteActionRequest(BaseModel):

    action_id: str

    action_type: str

    target_id: str

    payload: Optional[Dict[str, Any]] = None





class PublishEventRequest(BaseModel):

    event_type: str

    resource_type: str

    resource_id: str

    data: Dict[str, Any] = {}





class DraftObjectionRequest(BaseModel):

    objection_category: str

    objection_text: str

    draft_response: str





class ApproveObjectionRequest(BaseModel):

    approved_response: Optional[str] = None





class RejectObjectionRequest(BaseModel):

    reason: Optional[str] = None





class UpdateCustomerProfileRequest(BaseModel):

    name: Optional[str] = None

    email: Optional[str] = None

    notes: Optional[str] = None

    custom_attributes: Optional[Dict[str, Any]] = None

    external_identifiers: Optional[Dict[str, Any]] = None





class EnqueueChatwootJobRequest(BaseModel):

    recipient_phone: str

    message_type: str = "template"

    payload: Dict[str, Any]

    workflow_run_id: Optional[int] = None

    idempotency_key: Optional[str] = None





class SimulateCallRequest(BaseModel):

    customer_name: str = "Ramesh Kumar"

    customer_phone: str = "+91-9876543210"

    call_type: str = "inbound"  # "inbound" or "outbound"

    sample_dialogue: Optional[List[Dict[str, str]]] = None

    scenario: Optional[str] = "real_estate_site_visit"  # preset scenario





# ---------------------------------------------------------------------------

# Helper: Get or Create Primary Organization Workflow

# ---------------------------------------------------------------------------



async def record_customer_interaction(

    organization_id: int,

    phone_number: str,

    name: Optional[str] = None,

    email: Optional[str] = None,

    lead_status: Optional[str] = None,

    workflow_run_id: Optional[int] = None,

    notes: Optional[str] = None,

    channel: str = "voice",

    custom_attributes: Optional[Dict[str, Any]] = None,

) -> CustomerProfileModel:

    norm_phone = normalize_phone_number(phone_number)

    now = datetime.now(UTC)

    async with db_client.async_session() as session:

        stmt = select(CustomerProfileModel).where(

            CustomerProfileModel.organization_id == organization_id,

            CustomerProfileModel.phone_number == norm_phone,

        )

        res = await session.execute(stmt)

        profile = res.scalar_one_or_none()



        if not profile:

            attrs = copy.deepcopy(custom_attributes or {})

            attrs["call_history"] = [workflow_run_id] if workflow_run_id else []

            if notes:

                attrs["notes"] = [notes]

            profile = CustomerProfileModel(

                organization_id=organization_id,

                phone_number=norm_phone,

                name=name,

                email=email,

                channel=channel,

                interaction_count=1,

                last_lead_status=lead_status or "Warm",

                custom_attributes=attrs,

                external_identifiers={},

                created_at=now,

                last_seen_at=now,

            )

            session.add(profile)

            await session.commit()

            await session.refresh(profile)

        else:

            profile.interaction_count = (profile.interaction_count or 0) + 1

            profile.last_seen_at = now

            if name and not profile.name:

                profile.name = name

            if email and not profile.email:

                profile.email = email

            if lead_status:

                profile.last_lead_status = lead_status



            attrs = copy.deepcopy(profile.custom_attributes or {})

            ch = attrs.get("call_history", [])

            if workflow_run_id and workflow_run_id not in ch:

                ch.append(workflow_run_id)

            attrs["call_history"] = ch

            if notes:

                existing_notes = attrs.get("notes", [])

                existing_notes.append(notes)

                attrs["notes"] = existing_notes

            if custom_attributes:

                attrs.update(custom_attributes)

            profile.custom_attributes = attrs

            await session.commit()

            await session.refresh(profile)

        return profile





async def _get_primary_workflow(user: UserModel, workflow_id: Optional[int] = None) -> WorkflowModel:

    org_id = user.selected_organization_id

    # 1. If an explicit workflow_id is requested, enforce tenant isolation
    if workflow_id is not None:
        requested_wf = await db_client.get_workflow_by_id(workflow_id)
        if not requested_wf:
            raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found")
        if requested_wf.organization_id != org_id:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Workflow {workflow_id} does not belong to organization {org_id}",
            )
        return requested_wf

    # 2. Query workflows for this specific organization
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)

    if workflows:
        # Prefer named primary voice assistant in this organization
        for candidate in workflows:
            if candidate.name in ("Default Voice Assistant", "Universal AI Voice Assistant"):
                candidate_wf = await db_client.get_workflow_by_id(candidate.id)
                if candidate_wf and candidate_wf.organization_id == org_id:
                    return candidate_wf

        # Prefer authoritative primary workflow #1 if and only if it belongs to this organization
        wf1 = await db_client.get_workflow_by_id(1)
        if wf1 and wf1.organization_id == org_id:
            return wf1

        # Otherwise return the first workflow of this organization
        wf = await db_client.get_workflow_by_id(workflows[0].id)
        if wf and wf.organization_id == org_id:
            return wf



    # Create default if missing

    from api.services.workflow.defaults import build_default_workflow_definition

    definition = build_default_workflow_definition()

    new_wf = await db_client.create_workflow(

        name="Universal AI Voice Assistant",

        workflow_definition=definition,

        user_id=user.id,

        organization_id=org_id,

    )

    return new_wf





# ---------------------------------------------------------------------------

# Presets & Training Endpoints

# ---------------------------------------------------------------------------



@router.get("/presets")

async def list_industry_presets() -> List[Dict[str, Any]]:

    """List all 10 universal Indian business presets."""

    return UNIVERSAL_INDUSTRY_PRESETS





@router.get("/presets/{preset_id}")

async def get_industry_preset(preset_id: str) -> Dict[str, Any]:

    """Retrieve full configuration template for a specific preset."""

    preset = get_preset_by_id(preset_id)

    if not preset:

        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")

    return preset





@router.get("/agent")

async def get_agent_training(
    workflow_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:

    """Returns the current agent training configuration, published status, and version history."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    

    # Extract training config if present, or fallback to real estate preset

    training_data = (workflow.workflow_configurations or {}).get("agent_training")

    # "saved": the owner's own Train My Agent settings for THIS workflow.
    # "starter_template": nothing saved yet, so a generic starter is shown and
    # publishing would replace this workflow's current call flow.
    training_source = "saved" if training_data else "starter_template"

    if not training_data:

        training_data = get_preset_by_id("real_estate")



    # Fetch version history from workflow_definitions

    versions = []

    try:

        async with db_client.async_session() as session:

            stmt = (

                select(WorkflowDefinitionModel)

                .where(WorkflowDefinitionModel.workflow_id == workflow.id)

                .order_by(WorkflowDefinitionModel.version_number.desc())

            )

            result = await session.execute(stmt)

            defs = result.scalars().all()

            for d in defs:

                versions.append({

                    "id": d.id,

                    "version_number": d.version_number,

                    "status": d.status,

                    "is_current": d.is_current,

                    "published_at": d.published_at.isoformat() if d.published_at else None,

                    "created_at": d.created_at.isoformat() if d.created_at else None,

                })

    except Exception as e:

        logger.warning(f"Could not load version history: {e}")



    return {

        "workflow_id": workflow.id,

        "workflow_name": workflow.name,

        "training_source": training_source,

        "released_definition_id": workflow.released_definition_id,

        "training_data": training_data,

        "status": workflow.status.value if hasattr(workflow.status, "value") else str(workflow.status),

        "versions": versions,

        "rates": AUTHORITATIVE_PLATFORM_RATES,

    }





@router.put("/agent/draft")

async def save_agent_draft(

    request: AgentTrainingDraftRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Saves agent training draft without overwriting the live published prompt."""

    workflow = await _get_primary_workflow(user, workflow_id=request.workflow_id)



    # Compile the training data into definition draft

    new_definition = compile_agent_training_to_workflow(

        request.training_data,

        workflow.workflow_definition,

    )



    # Save to workflow configurations

    configs = copy.deepcopy(workflow.workflow_configurations or {})

    configs["agent_training"] = request.training_data



    # Map product mode (Budget, Standard, Premium) to LiveKit model overrides

    try:
        product_mode, model_overrides = resolve_product_mode_overrides(
            request.training_data.get("product_mode")
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    configs["product_mode"] = product_mode

    if model_overrides is None:
        # economy_ivr is zero-AI: never write (or keep) model overrides.
        configs.pop("model_overrides", None)
    else:
        configs["model_overrides"] = model_overrides

    # G-45: copy the owner's per-call duration limit to the top-level key the
    # LiveKit worker enforces. Fail closed: an invalid or missing limit is a 422
    # and nothing is saved, so no AI workflow is published without a time bound.
    try:
        configs["max_call_duration_seconds"] = resolve_max_call_duration_seconds(
            request.training_data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))



    # Save draft version in database

    draft = await db_client.save_workflow_draft(

        workflow_id=workflow.id,

        workflow_definition=new_definition,

        workflow_configurations=configs,

        template_context_variables=workflow.template_context_variables or {},

    )

    action_type = (
        "audit.config.industry_changed"
        if request.training_data.get("industry")
        else "audit.config.agent_draft_updated"
    )
    await audit_service.record_event(
        organization_id=user.selected_organization_id,
        action_type=action_type,
        target_resource="workflow_draft",
        target_id=str(workflow.id),
        actor_id=str(user.id),
        actor_role="admin" if user.is_superuser else "user",
        outcome="SUCCESS",
        changed_fields={
            "draft_version": draft.version_number,
            "industry": request.training_data.get("industry"),
            "product_mode": product_mode,
            "business_name": request.training_data.get("name"),
        },
    )

    return {

        "status": "draft_saved",

        "workflow_id": workflow.id,

        "draft_version": draft.version_number,

        "message": "Draft saved successfully. Test it in the browser or publish when ready.",

    }





@router.post("/agent/publish")

async def publish_agent_training(

    request: AgentTrainingDraftRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Compiles training, saves, and publishes it so it immediately takes effect in LiveKit / telephony."""

    workflow = await _get_primary_workflow(user, workflow_id=request.workflow_id)



    # Compile into ReactFlow graph

    new_definition = compile_agent_training_to_workflow(

        request.training_data,

        workflow.workflow_definition,

    )



    configs = copy.deepcopy(workflow.workflow_configurations or {})

    configs["agent_training"] = request.training_data



    # Map product mode (Budget, Standard, Premium) to LiveKit model overrides

    try:
        product_mode, model_overrides = resolve_product_mode_overrides(
            request.training_data.get("product_mode")
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    configs["product_mode"] = product_mode

    if model_overrides is None:
        # economy_ivr is zero-AI: never write (or keep) model overrides.
        configs.pop("model_overrides", None)
    else:
        configs["model_overrides"] = model_overrides

    # G-45: copy the owner's per-call duration limit to the top-level key the
    # LiveKit worker enforces. Fail closed: an invalid or missing limit is a 422
    # and nothing is saved, so no AI workflow is published without a time bound.
    try:
        configs["max_call_duration_seconds"] = resolve_max_call_duration_seconds(
            request.training_data
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Refuse to publish a live AI mode whose provider is not configured, so the
    # owner gets a precise error now instead of a failed call later. This checks
    # configuration presence only; it does not verify the key with the provider.
    missing_provider_config = find_missing_provider_config(
        product_mode,
        model_overrides,
        await db_client.get_user_configurations(workflow.user_id),
    )
    if missing_provider_config:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot publish '{product_mode}' mode: "
                + " ".join(missing_provider_config)
            ),
        )

    # 1. Save draft

    await db_client.save_workflow_draft(

        workflow_id=workflow.id,

        workflow_definition=new_definition,

        workflow_configurations=configs,

        template_context_variables=workflow.template_context_variables or {},

    )



    # 2. Publish draft

    published_wf = await db_client.publish_workflow_draft(workflow_id=workflow.id)



    # 3. Also update workflow main row

    async with db_client.async_session() as session:

        stmt = (

            select(WorkflowModel)

            .where(WorkflowModel.id == workflow.id)

        )

        res = await session.execute(stmt)

        live_wf = res.scalars().first()

        if live_wf:

            live_wf.workflow_definition = new_definition

            live_wf.workflow_configurations = configs

            await session.commit()



    try:
        await audit_service.record_event(
            organization_id=user.selected_organization_id,
            action_type="audit.config.agent_published",
            target_resource="workflow",
            target_id=str(workflow.id),
            actor_id=str(user.id),
            actor_role="admin" if user.is_superuser else "user",
            outcome="SUCCESS",
            changed_fields={
                "workflow_name": workflow.name,
                "released_definition_id": published_wf.id,
                "version_number": published_wf.version_number,
                "product_mode": product_mode,
            },
        )
    except Exception as exc:  # the publish is already committed
        logger.error(f"Failed to record agent publish audit event: {exc}")

    return {

        "status": "published",

        "workflow_id": workflow.id,

        "released_definition_id": published_wf.id,

        "version_number": published_wf.version_number,

        "message": "Agent training compiled and published to live runtime successfully.",

    }





@router.post("/agent/restore/{definition_id}")

async def restore_agent_version(

    definition_id: int,

    workflow_id: Optional[int] = Query(None),

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Restores a previous published version of the agent."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)



    # If an existing draft is active, discard it first to allow clean revert

    try:

        await db_client.discard_workflow_draft(workflow.id)

    except Exception:

        pass



    draft = await db_client.revert_to_version(

        workflow_id=workflow.id,

        definition_id=definition_id,

    )

    published = await db_client.publish_workflow_draft(workflow_id=workflow.id)



    try:
        await audit_service.record_event(
            organization_id=user.selected_organization_id,
            action_type="audit.config.agent_restored",
            target_resource="workflow",
            target_id=str(workflow.id),
            actor_id=str(user.id),
            actor_role="admin" if user.is_superuser else "user",
            outcome="SUCCESS",
            changed_fields={
                "workflow_name": workflow.name,
                "restored_from_definition_id": definition_id,
                "released_definition_id": published.id,
                "version_number": published.version_number,
            },
        )
    except Exception as exc:  # the restore is already committed
        logger.error(f"Failed to record agent restore audit event: {exc}")

    return {

        "status": "restored",

        "workflow_id": workflow.id,

        "released_definition_id": published.id,

        "version_number": published.version_number,

        "version_number": draft.version_number,

        "message": f"Successfully restored agent configuration to version {draft.version_number}.",

    }







# ---------------------------------------------------------------------------

# Business IVR Management

# ---------------------------------------------------------------------------



@router.get("/ivr")

async def get_business_ivr(
    workflow_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:

    """Get active IVR configuration for the selected (or primary) workflow."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    configs = workflow.workflow_configurations or {}

    raw_ivr = configs.get("ivr")

    if raw_ivr:

        return BusinessIVRConfig.model_validate(raw_ivr).model_dump()

    return BusinessIVRConfig().model_dump()





@router.post("/ivr")

async def save_business_ivr(

    config: BusinessIVRConfig,

    workflow_id: Optional[int] = Query(None),

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Save owner-configured IVR settings to the selected (or primary) workflow."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    configs = copy.deepcopy(workflow.workflow_configurations or {})

    configs["ivr"] = config.model_dump()



    async with db_client.async_session() as session:

        stmt = select(WorkflowModel).where(WorkflowModel.id == workflow.id)

        res = await session.execute(stmt)

        live_wf = res.scalars().first()

        if live_wf:

            live_wf.workflow_configurations = configs

            await session.commit()



    return {

        "status": "saved",

        "ivr": config.model_dump(),

        "message": "IVR configuration saved and linked to inbound call router.",

    }





from api.schemas.economy_ivr import EconomyIVRConfig





@router.get("/economy-ivr")

async def get_economy_ivr_config(

    workflow_id: Optional[int] = Query(None),

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Retrieve owner Economy IVR menu configuration."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    configs = workflow.workflow_configurations or {}

    raw_cfg = configs.get("economy_ivr")

    if raw_cfg:

        return EconomyIVRConfig.model_validate(raw_cfg).model_dump()

    return {

        "enabled": False,

        "default_language": "en",

        "greeting_recording_id": "",

        "language_greetings": {},

        "keypad_actions": {},

        "timeout_seconds": 8,

        "max_invalid_retries": 2,

        "auto_hangup_after_action": True,

        "allow_barge_in": True,

    }





@router.post("/economy-ivr")

async def save_economy_ivr_config(

    config: EconomyIVRConfig,

    workflow_id: Optional[int] = Query(None),

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Save owner-configured Economy IVR menu (1-9 actions, recordings, timeouts)."""

    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    configs = copy.deepcopy(workflow.workflow_configurations or {})

    configs["economy_ivr"] = config.model_dump()



    async with db_client.async_session() as session:

        stmt = select(WorkflowModel).where(WorkflowModel.id == workflow.id)

        res = await session.execute(stmt)

        live_wf = res.scalars().first()

        if live_wf:

            live_wf.workflow_configurations = configs

            await session.commit()



    return {

        "status": "saved",

        "config": config.model_dump(),

        "message": "Economy IVR configuration updated successfully.",

    }





@router.post("/ivr/preview")

async def preview_business_ivr(

    config: BusinessIVRConfig,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Preview carrier-compatible VoiceXML without modifying runtime."""

    xml = generate_inbound_ivr_xml(

        config=config,

        action_url="http://localhost:8001/api/v1/telephony/voicelink/dtmf/preview",

        sip_inbound_uri="sip:livekit-sip@sip.spxvoice.internal",

    )

    return {"status": "ok", "xml": xml}





# ---------------------------------------------------------------------------

# Conversation History & Lead Intelligence

# ---------------------------------------------------------------------------



@router.get("/overview")

async def get_business_overview(user: UserModel = Depends(get_user)) -> Dict[str, Any]:

    """Returns business KPIs: calls, lead conversion funnel, appointments, minutes, and costs."""

    org_id = user.selected_organization_id

    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)

    workflow_ids = [w.id for w in workflows]



    if not workflow_ids:

        return {

            "total_calls": 0,

            "inbound_calls": 0,

            "outbound_calls": 0,

            "answered_calls": 0,

            "failed_calls": 0,

            "qualified_leads": 0,

            "hot_leads": 0,

            "warm_leads": 0,

            "cold_leads": 0,

            "appointment_requests": 0,

            "confirmed_appointments": 0,

            "consultations": 0,

            "callbacks": 0,

            "human_handovers": 0,

            "total_calling_minutes": 0.0,

            "estimated_cost_inr": 0.0,

        }



    async with db_client.async_session() as session:

        stmt = (

            select(WorkflowRunModel)

            .options(defer(WorkflowRunModel.logs))

            .where(WorkflowRunModel.workflow_id.in_(workflow_ids))

            .order_by(WorkflowRunModel.created_at.desc())

        )

        res = await session.execute(stmt)

        runs = res.scalars().all()



    total_calls = len(runs)

    inbound_calls = 0

    outbound_calls = 0

    answered_calls = 0

    failed_calls = 0

    hot_leads = 0

    warm_leads = 0

    cold_leads = 0

    qualified_leads = 0

    appointment_requests = 0

    confirmed_appointments = 0

    consultations = 0

    callbacks = 0

    human_handovers = 0

    total_telecom_duration_sec = 0.0

    total_simulated_duration_sec = 0.0

    simulated_calls_count = 0



    for r in runs:

        # Browser and scripted calls have no PSTN leg; unknown origins stay in the telecom bucket.
        is_sim = classify_call_origin(r) in ("simulated", "browser")

        # Call type

        ct = r.call_type.value if hasattr(r.call_type, "value") else str(r.call_type)

        if ct == "inbound":

            inbound_calls += 1

        else:

            outbound_calls += 1



        # State & disposition

        disp = getattr(r, "call_disposition", None)

        st = r.state.value if hasattr(r.state, "value") else str(r.state)

        if disp == "ANSWERED" or (disp is None and st in ("completed", "running")):

            answered_calls += 1

        elif disp in ("BUSY", "NO_ANSWER", "FAILED", "CANCELLED") or st in ("failed", "cancelled"):

            failed_calls += 1



        # Duration

        dur = resolve_duration_seconds(r)

        if is_sim:

            total_simulated_duration_sec += dur

            simulated_calls_count += 1

        else:

            total_telecom_duration_sec += dur



        # Annotations lead intelligence

        intel = (r.annotations or {}).get("lead_intelligence") or {}

        cls = intel.get("lead_classification")

        if cls == "Hot":

            hot_leads += 1

            qualified_leads += 1

        elif cls == "Warm":

            warm_leads += 1

            qualified_leads += 1

        elif cls == "Cold":

            cold_leads += 1



        # Appointments

        appt = intel.get("appointment") or {}

        if appt.get("requested"):

            appointment_requests += 1

            consultations += 1

        if appt.get("status") == "Confirmed":

            confirmed_appointments += 1



        # Callbacks & Escalations

        if (intel.get("callback") or {}).get("requested"):

            callbacks += 1

        if (intel.get("human_escalation") or {}).get("requested"):

            human_handovers += 1



    telecom_minutes = round(total_telecom_duration_sec / 60.0, 1)

    simulated_minutes = round(total_simulated_duration_sec / 60.0, 1)

    total_minutes = round((total_telecom_duration_sec + total_simulated_duration_sec) / 60.0, 1)



    # Actual telecom spend (only for real telecom minutes)

    estimated_cost_inr = round(telecom_minutes * 1.80, 2)

    simulated_cost_inr = round(simulated_minutes * 1.80, 2)



    return {

        "total_calls": total_calls,

        "inbound_calls": inbound_calls,

        "outbound_calls": outbound_calls,

        "answered_calls": answered_calls,

        "failed_calls": failed_calls,

        "qualified_leads": qualified_leads,

        "hot_leads": hot_leads,

        "warm_leads": warm_leads,

        "cold_leads": cold_leads,

        "appointment_requests": appointment_requests,

        "confirmed_appointments": confirmed_appointments,

        "consultations": consultations,

        "callbacks": callbacks,

        "human_handovers": human_handovers,

        "total_calling_minutes": total_minutes,

        "telecom_calling_minutes": telecom_minutes,

        "simulated_calling_minutes": simulated_minutes,

        "estimated_cost_inr": estimated_cost_inr,

        "simulated_cost_inr": simulated_cost_inr,

        "simulated_calls_count": simulated_calls_count,

    }





def _require_org_id(user: UserModel) -> int:
    org_id = getattr(user, "selected_organization_id", None)
    if not isinstance(org_id, int) or isinstance(org_id, bool):
        raise HTTPException(status_code=403, detail="No organization selected")
    return org_id


def _actor(user: UserModel) -> Dict[str, Any]:
    return {
        "user_id": getattr(user, "id", None),
        "role": "superadmin" if getattr(user, "is_superuser", False) else "owner",
    }


def _recording_storage_configured() -> bool:
    try:
        from api.services.livekit import post_call as _post_call

        return _post_call._recording_config_from_env() is not None
    except Exception:
        return False


class ConversationFilterRequest(BaseModel):
    search: Optional[str] = None
    classification: Optional[str] = None
    call_type: Optional[str] = None
    source: str = "all"
    status: str = "all"
    archived: str = "active"
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    analysis_status: Optional[str] = None


class BulkSelectionRequest(BaseModel):
    ids: Optional[List[int]] = None
    filters: Optional[ConversationFilterRequest] = None
    expected_count: Optional[int] = None
    reason: Optional[str] = Field(default=None, max_length=200)


def _build_filters(
    *,
    search: Optional[str] = None,
    classification: Optional[str] = None,
    call_type: Optional[str] = None,
    source: str = "all",
    status: str = "all",
    archived: str = "active",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    analysis_status: Optional[str] = None,
    run_ids: Optional[List[int]] = None,
) -> ConversationFilters:
    try:
        return ConversationFilters(
            search=search,
            classification=classification,
            call_type=call_type,
            source=source or "all",
            status=status or "all",
            archived=archived or "active",
            date_from=parse_date_bound(date_from),
            date_to=parse_date_bound(date_to, end_of_day=True),
            analysis_status=analysis_status,
            run_ids=run_ids,
        ).validate()
    except ConversationQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


async def _conversation_page(
    user: UserModel,
    filters: ConversationFilters,
    *,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 25,
    offset: int = 0,
    max_limit: int = MAX_PAGE_SIZE,
    include_facets: bool = True,
    mask_contacts: bool = True,
) -> Dict[str, Any]:
    org_id = _require_org_id(user)
    try:
        async with db_client.async_session() as session:
            return await list_conversation_items(
                session,
                org_id,
                filters,
                sort=sort,
                order=order,
                limit=limit,
                offset=offset,
                max_limit=max_limit,
                storage_configured=_recording_storage_configured(),
                include_facets=include_facets,
                mask_contacts=mask_contacts,
            )
    except ConversationQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/conversations")
async def list_conversations(
    limit: int = 25,
    offset: int = 0,
    search: Optional[str] = None,
    classification: Optional[str] = None,
    call_type: Optional[str] = None,
    source: str = "all",
    status: str = "all",
    archived: str = "active",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    analysis_status: Optional[str] = None,
    sort: str = "created_at",
    order: str = "desc",
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Server-side paginated conversation list.

    Reads persisted analysis only (never recomputes it per request), never loads
    raw logs, is scoped to the caller's organization and returns accurate totals
    plus classification facets for the whole filtered set.
    """
    filters = _build_filters(
        search=search,
        classification=classification,
        call_type=call_type,
        source=source,
        status=status,
        archived=archived,
        date_from=date_from,
        date_to=date_to,
        analysis_status=analysis_status,
    )
    return await _conversation_page(user, filters, sort=sort, order=order, limit=limit, offset=offset)


async def _legacy_conversation_items(user: UserModel) -> List[Dict[str, Any]]:
    """Bounded newest-first projection used by the legacy /leads and /appointments lists."""
    page = await _conversation_page(
        user,
        _build_filters(),
        limit=500,
        max_limit=500,
        include_facets=False,
    )
    return page.get("items", [])


RESERVED_EVENT_TYPES = frozenset(
    {
        "conversation_started",
        "conversation_finalized",
        "conversation_analysis_completed",
        "conversation_analysis_failed",
        "recording_status_changed",
        "conversations_archived",
        "conversations_trashed",
        "conversations_restored",
        "conversations_analyzed",
        "lead_intelligence_updated",
        "lead_updated",
        "call_completed",
        "lead_created",
    }
)
_BULK_MODES = {"archive": "conversations_archived", "trash": "conversations_trashed", "restore": "conversations_restored"}


async def _bulk_lifecycle_action(user: UserModel, body: BulkSelectionRequest, *, mode: str) -> Dict[str, Any]:
    """Reversible bulk Archive / Move to Trash / Restore. Nothing is ever erased.

    * Selection is re-resolved on the server (ids verified against the organization, or the
      filter scope re-counted against ``expected_count``).
    * An audit record is written FIRST and fail-closed: if it cannot be stored, nothing changes.
    * Each run is changed under a row lock; the operation is idempotent, so after a partial
      failure it can simply be repeated.
    """
    org_id = _require_org_id(user)
    require_feature("bulk_actions", "Bulk actions are unavailable.")
    await check_rate_limit("bulk", getattr(user, "id", org_id))
    filters = None
    if body.filters is not None:
        f = body.filters
        scope = "active"
        if mode == "restore":
            scope = f.archived if f.archived in ("archived", "trashed") else "all"
        filters = _build_filters(
            search=f.search,
            classification=f.classification,
            call_type=f.call_type,
            source=f.source,
            status=f.status,
            archived=scope,
            date_from=f.date_from,
            date_to=f.date_to,
            analysis_status=f.analysis_status,
        )
    try:
        async with db_client.async_session() as session:
            selection = await resolve_selection(
                session,
                org_id,
                ids=body.ids,
                filters=filters,
                expected_count=body.expected_count,
            )
    except SelectionChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ConversationQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    actor = _actor(user)
    ids = selection["ids"]
    try:
        await audit_service.record_event(
            organization_id=org_id,
            action_type=f"audit.crm.conversations_{mode}_started",
            target_resource="conversation_bulk",
            target_id=f"{len(ids)}",
            actor_id=str(getattr(user, "id", "")),
            actor_role=actor["role"],
            changed_fields={"selected": len(ids), "mode": selection["mode"], "run_ids": ids[:50]},
            reason=body.reason,
            fail_closed=True,
        )
    except Exception as exc:
        logger.error(f"[Audit] bulk {mode} refused: audit record could not be written ({type(exc).__name__})")
        raise HTTPException(
            status_code=503,
            detail="The action was not performed because it could not be audited. Please retry.",
        )

    def _mutate(current: Dict[str, Any]):
        if mode == "restore":
            return apply_restore(current, actor=actor)
        if mode == "trash":
            return apply_trash(current, actor=actor, reason=body.reason)
        return apply_archive(current, actor=actor, reason=body.reason)

    changed = 0
    processed = 0
    try:
        for run_id in ids:
            _, did_change = await db_client.mutate_workflow_run_annotations(
                run_id, _mutate, organization_id=org_id
            )
            changed += 1 if did_change else 0
            processed += 1
    except Exception as exc:
        logger.error(f"[Conversations] bulk {mode} stopped after {processed}/{len(ids)}: {type(exc).__name__}")
        try:
            await audit_service.record_event(
                organization_id=org_id,
                action_type=f"audit.crm.conversations_{mode}_completed",
                target_resource="conversation_bulk",
                target_id=f"{len(ids)}",
                outcome="FAILED",
                actor_id=str(getattr(user, "id", "")),
                actor_role=actor["role"],
                changed_fields={"selected": len(ids), "processed": processed, "changed": changed},
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=(
                f"Stopped after {processed} of {len(ids)} conversations ({changed} changed). "
                "The operation is safe to repeat."
            ),
        )

    try:
        await audit_service.record_event(
            organization_id=org_id,
            action_type=f"audit.crm.conversations_{mode}_completed",
            target_resource="conversation_bulk",
            target_id=f"{len(ids)}",
            actor_id=str(getattr(user, "id", "")),
            actor_role=actor["role"],
            changed_fields={"selected": len(ids), "changed": changed, "mode": selection["mode"]},
        )
    except Exception as exc:
        logger.warning(f"[Audit] bulk {mode} completion audit failed: {type(exc).__name__}")
    try:
        await realtime_dashboard_bus.publish_event(
            organization_id=org_id,
            event_type=_BULK_MODES[mode],
            resource_type="conversation",
            resource_id="bulk",
            data={"count": changed},
        )
    except Exception as exc:
        logger.warning(f"[RealtimeBus] bulk {mode} event failed: {type(exc).__name__}")
    return {
        "status": {"archive": "archived", "trash": "trashed", "restore": "restored"}[mode],
        "selected": len(ids),
        "changed": changed,
        "skipped": selection["skipped"],
    }


@router.post("/conversations/bulk/archive")
async def bulk_archive_conversations(
    body: BulkSelectionRequest, user: UserModel = Depends(get_user)
) -> Dict[str, Any]:
    """Reversible bulk archive. Financial evidence stays on the run; nothing is deleted."""
    return await _bulk_lifecycle_action(user, body, mode="archive")


@router.post("/conversations/bulk/trash")
async def bulk_trash_conversations(
    body: BulkSelectionRequest, user: UserModel = Depends(get_user)
) -> Dict[str, Any]:
    """Move to Trash (reversible). This is NOT permanent deletion: usage, cost and transcripts are kept."""
    return await _bulk_lifecycle_action(user, body, mode="trash")


@router.post("/conversations/bulk/restore")
async def bulk_restore_conversations(
    body: BulkSelectionRequest, user: UserModel = Depends(get_user)
) -> Dict[str, Any]:
    return await _bulk_lifecycle_action(user, body, mode="restore")


MAX_BULK_ANALYZE = 200


class BulkAnalyzeRequest(BulkSelectionRequest):
    dry_run: bool = False


async def _business_context(workflow_id: int, cache: Dict[int, Any]) -> Optional[Dict[str, Any]]:
    if workflow_id not in cache:
        wf = await db_client.get_workflow_by_id(workflow_id)
        biz = (wf.workflow_configurations or {}).get("agent_training") if wf else None
        cache[workflow_id] = biz if isinstance(biz, dict) else None
    return cache[workflow_id]


async def _plan_reanalysis(org_id: int, run_ids: List[int]) -> Dict[str, Any]:
    """What re-analysis would do for the selected runs. Reads only; writes nothing."""
    plan: Dict[str, Any] = {"eligible": [], "skipped": {"no_transcript": 0, "up_to_date": 0}, "examples": []}
    cache: Dict[int, Any] = {}
    async with db_client.async_session() as session:
        for start in range(0, len(run_ids), 50):
            rows = (
                await session.execute(
                    select(
                        WorkflowRunModel.id,
                        WorkflowRunModel.logs,
                        WorkflowRunModel.annotations,
                        WorkflowRunModel.workflow_id,
                    ).where(
                        WorkflowRunModel.id.in_(run_ids[start : start + 50]),
                        WorkflowRunModel.workflow_id.in_(
                            select(WorkflowModel.id).where(WorkflowModel.organization_id == org_id)
                        ),
                    )
                )
            ).all()
            for rid, logs, annotations, wf_id in rows:
                transcript = extract_transcript_from_logs(logs)
                if not transcript:
                    plan["skipped"]["no_transcript"] += 1
                    continue
                analysis = (annotations or {}).get("analysis") or {}
                if (
                    analysis.get("status") in TERMINAL_ANALYSIS_STATUSES
                    and analysis.get("transcript_hash") == transcript_hash(transcript)
                    and analysis.get("analyzer_version") == ANALYZER_VERSION
                ):
                    plan["skipped"]["up_to_date"] += 1
                    continue
                plan["eligible"].append(rid)
                if len(plan["examples"]) < 10:
                    current = (annotations or {}).get("lead_intelligence") or {}
                    after = analyze_lead_from_transcript(
                        transcript, business_context=await _business_context(wf_id, cache)
                    )
                    plan["examples"].append(
                        {
                            "run_id": rid,
                            "before": {
                                "classification": current.get("lead_classification") or "Unclassified",
                                "intent": current.get("customer_intent"),
                            },
                            "after": {"classification": after["lead_classification"], "title": after["title"]},
                        }
                    )
    return plan


async def _reanalyze_run(org_id: int, run_id: int, cache: Dict[int, Any]) -> bool:
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(WorkflowRunModel.logs, WorkflowRunModel.workflow_id).where(
                    WorkflowRunModel.id == run_id,
                    WorkflowRunModel.workflow_id.in_(
                        select(WorkflowModel.id).where(WorkflowModel.organization_id == org_id)
                    ),
                )
            )
        ).first()
    if row is None:
        return False
    logs, wf_id = row
    transcript = extract_transcript_from_logs(logs)
    if not transcript:
        return False
    intel = analyze_lead_from_transcript(
        transcript, business_context=await _business_context(wf_id, cache), gathered_context={}
    )
    th = transcript_hash(transcript)
    _, changed = await db_client.mutate_workflow_run_annotations(
        run_id,
        lambda cur: apply_ai_analysis(
            cur,
            intel,
            transcript_hash_value=th,
            search_text="\n".join([str(intel.get("title", "")), str(intel.get("summary", "")), transcript]),
        ),
        organization_id=org_id,
    )
    return bool(changed)


@router.post("/conversations/bulk/analyze")
async def bulk_reanalyze_conversations(
    body: BulkAnalyzeRequest, user: UserModel = Depends(get_user)
) -> Dict[str, Any]:
    """Re-analyze selected conversations with the latest rules (owner-triggered, audited).

    The earlier AI output is kept (``analysis.previous_ai``) and owner corrections made through
    the new correction flow are preserved. Runs without a transcript are never touched. Use
    ``dry_run=true`` first to preview exactly what would change.
    """
    require_feature("bulk_actions", "Bulk re-analysis is unavailable.")
    org_id = _require_org_id(user)
    await check_rate_limit("analyze", getattr(user, "id", org_id))
    filters = None
    if body.filters is not None:
        f = body.filters
        filters = _build_filters(
            search=f.search,
            classification=f.classification,
            call_type=f.call_type,
            source=f.source,
            status=f.status,
            archived=f.archived if f.archived in ("active", "archived") else "active",
            date_from=f.date_from,
            date_to=f.date_to,
            analysis_status=f.analysis_status,
        )
    try:
        async with db_client.async_session() as session:
            selection = await resolve_selection(
                session, org_id, ids=body.ids, filters=filters,
                expected_count=body.expected_count, cap=MAX_BULK_ANALYZE,
            )
    except SelectionChangedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ConversationQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    plan = await _plan_reanalysis(org_id, selection["ids"])
    summary = {
        "selected": len(selection["ids"]),
        "eligible": len(plan["eligible"]),
        "skipped": plan["skipped"],
        "skipped_other_tenant": selection["skipped"],
        "examples": plan["examples"],
        "max_per_request": MAX_BULK_ANALYZE,
    }
    if body.dry_run:
        return {"status": "dry_run", **summary}

    actor = _actor(user)
    try:
        await audit_service.record_event(
            organization_id=org_id,
            action_type="audit.crm.conversations_analyze_started",
            target_resource="conversation_bulk",
            target_id=f"{len(plan['eligible'])}",
            actor_id=str(getattr(user, "id", "")),
            actor_role=actor["role"],
            changed_fields={"eligible": len(plan["eligible"]), "run_ids": plan["eligible"][:50]},
            fail_closed=True,
        )
    except Exception as exc:
        logger.error(f"[Audit] bulk analyze refused: audit record could not be written ({type(exc).__name__})")
        raise HTTPException(
            status_code=503, detail="The action was not performed because it could not be audited. Please retry."
        )
    cache: Dict[int, Any] = {}
    changed = processed = 0
    try:
        for run_id in plan["eligible"]:
            changed += 1 if await _reanalyze_run(org_id, run_id, cache) else 0
            processed += 1
    except Exception as exc:
        logger.error(f"[Conversations] bulk analyze stopped after {processed}/{len(plan['eligible'])}: {type(exc).__name__}")
        raise HTTPException(
            status_code=500,
            detail=f"Stopped after {processed} of {len(plan['eligible'])} conversations ({changed} changed). The operation is safe to repeat.",
        )
    try:
        await audit_service.record_event(
            organization_id=org_id,
            action_type="audit.crm.conversations_analyze_completed",
            target_resource="conversation_bulk",
            target_id=f"{len(plan['eligible'])}",
            actor_id=str(getattr(user, "id", "")),
            actor_role=actor["role"],
            changed_fields={"eligible": len(plan["eligible"]), "changed": changed},
        )
        await realtime_dashboard_bus.publish_event(
            organization_id=org_id, event_type="conversations_analyzed", resource_type="conversation",
            resource_id="bulk", data={"count": changed},
        )
    except Exception as exc:
        logger.warning(f"[Conversations] bulk analyze completion bookkeeping failed: {type(exc).__name__}")
    return {"status": "analyzed", "changed": changed, **summary}


async def _load_transcripts(run_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {}
    async with db_client.async_session() as session:
        for start in range(0, len(run_ids), 100):
            chunk = run_ids[start : start + 100]
            rows = (
                await session.execute(
                    select(WorkflowRunModel.id, WorkflowRunModel.logs).where(WorkflowRunModel.id.in_(chunk))
                )
            ).all()
            for rid, logs in rows:
                out[rid] = timed_transcript_turns(logs)
    return out


async def _collect_recordings(org_id: int, items: List[Dict[str, Any]]):
    """Verified recording audio for an explicit ZIP export. Bounded count/size; org-scoped."""
    from api.services.livekit import post_call as _post_call

    audio: Dict[int, bytes] = {}
    notes: Dict[int, str] = {}
    ready = []
    for item in items:
        status = (item.get("recording") or {}).get("status")
        if status == "ready":
            ready.append(item["id"])
        else:
            notes[item["id"]] = f"no recording to include (status: {status})"
    for skipped in ready[MAX_ZIP_RECORDINGS:]:
        notes[skipped] = f"not included: at most {MAX_ZIP_RECORDINGS} recordings per ZIP"
    wanted = ready[:MAX_ZIP_RECORDINGS]
    if not wanted:
        return audio, notes
    async with db_client.async_session() as session:
        runs = (
            await session.execute(
                select(WorkflowRunModel)
                .options(defer(WorkflowRunModel.logs))
                .where(
                    WorkflowRunModel.id.in_(wanted),
                    WorkflowRunModel.workflow_id.in_(
                        select(WorkflowModel.id).where(WorkflowModel.organization_id == org_id)
                    ),
                )
            )
        ).scalars().all()
    total = 0
    storage = _recording_storage_configured()
    for run in runs:
        rec = recording_status(run, storage_configured=storage)
        key = _post_call.recording_object_key_from_url_or_key(rec.get("key") or run.recording_url)
        if not key:
            notes[run.id] = "legacy-storage recordings are downloaded individually"
            continue
        data = await _post_call.fetch_recording_bytes(key, max_bytes=MAX_RECORDING_BYTES)
        if data is None:
            notes[run.id] = "recording could not be verified in storage or exceeds the size limit"
            continue
        if total + len(data) > MAX_TOTAL_RECORDING_BYTES:
            notes[run.id] = "not included: ZIP recording size limit reached"
            continue
        total += len(data)
        audio[run.id] = data
    return audio, notes


@router.get("/conversations/export")
async def export_conversations(
    format: str = "csv",
    ids: Optional[str] = None,
    include_transcripts: bool = False,
    include_recordings: bool = False,
    search: Optional[str] = None,
    classification: Optional[str] = None,
    call_type: Optional[str] = None,
    source: str = "all",
    status: str = "all",
    archived: str = "active",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    analysis_status: Optional[str] = None,
    user: UserModel = Depends(get_user),
) -> StreamingResponse:
    """Export selected ids or the current filtered set as CSV, JSON or a ZIP archive.

    Never silently truncates: a result larger than the format's cap is rejected so
    the owner can narrow the filters. Recordings are not embedded.
    """
    org_id = _require_org_id(user)
    require_feature("exports", "Exports are unavailable.")
    await check_rate_limit("export", getattr(user, "id", org_id))
    fmt = (format or "csv").lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"format must be one of {EXPORT_FORMATS}. PDF and XLSX are not available (no supported library).",
        )
    run_ids: Optional[List[int]] = None
    if ids:
        try:
            run_ids = list(dict.fromkeys(int(x) for x in ids.split(",") if x.strip()))
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be comma-separated integers")
        if not run_ids or len(run_ids) > MAX_BULK_SELECTION:
            raise HTTPException(status_code=400, detail=f"Select between 1 and {MAX_BULK_SELECTION} conversations")
    filters = _build_filters(
        search=search,
        classification=classification,
        call_type=call_type,
        source=source,
        status=status,
        archived="all" if run_ids else archived,
        date_from=date_from,
        date_to=date_to,
        analysis_status=analysis_status,
        run_ids=run_ids,
    )
    with_transcripts = fmt == "zip" or (fmt == "json" and include_transcripts)
    cap = MAX_ZIP_ROWS if fmt == "zip" else (MAX_JSON_TRANSCRIPT_ROWS if with_transcripts else MAX_EXPORT_ROWS)

    items: List[Dict[str, Any]] = []
    offset = 0
    total = None
    while True:
        page = await _conversation_page(
            user, filters, limit=MAX_PAGE_SIZE, offset=offset, include_facets=False, mask_contacts=False
        )
        total = page["total"]
        if total > cap:
            raise HTTPException(
                status_code=400,
                detail=f"{total} conversations match; {fmt.upper()} export is limited to {cap}. Narrow the filters or select fewer.",
            )
        items.extend(page["items"])
        offset += MAX_PAGE_SIZE
        if not page["has_more"] or not page["items"]:
            break

    transcripts = await _load_transcripts([i["id"] for i in items]) if with_transcripts else None
    filter_summary = filters.as_dict()
    filter_summary["search"] = bool(filter_summary.get("search"))  # never echo free-text search terms
    if fmt == "csv":
        payload, media = build_csv(items), "text/csv; charset=utf-8"
    elif fmt == "json":
        payload, media = build_json(items, transcripts=transcripts, filters=filter_summary), "application/json; charset=utf-8"
    else:
        if include_recordings:
            require_feature("recording_bundle", "Bundling recordings into exports is unavailable.")
        audio, notes = (
            await _collect_recordings(org_id, items) if include_recordings else ({}, {})
        )
        payload, media = (
            build_zip(
                items,
                transcripts or {},
                filters=filter_summary,
                recordings=audio,
                recording_notes=notes,
            ),
            "application/zip",
        )

    try:
        await audit_service.record_event(
            organization_id=org_id,
            action_type="audit.export.conversations",
            target_resource="conversation_export",
            target_id=f"{len(items)}",
            actor_id=str(getattr(user, "id", "")),
            actor_role=_actor(user)["role"],
            changed_fields={
                "format": fmt,
                "count": len(items),
                "selected_ids": bool(run_ids),
                "recordings": bool(include_recordings and fmt == "zip"),
            },
        )
    except Exception as exc:
        logger.warning(f"[Audit] export audit write failed: {type(exc).__name__}")

    filename = export_filename("spx-conversations", fmt)
    return StreamingResponse(
        iter([payload]),
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Export-Count": str(len(items)),
        },
    )


async def _load_run_row(run_id: int, user: UserModel, *, with_logs: bool = False):
    """Return ``(run, workflow_name, organization_id)`` or raise 404 (a missing run and another tenant's run are indistinguishable)."""
    async with db_client.async_session() as session:
        stmt = (
            select(WorkflowRunModel, WorkflowModel.name.label("workflow_name"), WorkflowModel.organization_id)
            .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
            .where(WorkflowRunModel.id == run_id)
        )
        if not with_logs:
            stmt = stmt.options(defer(WorkflowRunModel.logs))
        row = (await session.execute(stmt)).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    run, workflow_name, run_org = row
    if run_org != getattr(user, "selected_organization_id", None) and not getattr(user, "is_superuser", False):
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run, workflow_name, run_org


@router.get("/conversations/{run_id}/export")
async def export_single_conversation(
    run_id: int, format: str = "json", user: UserModel = Depends(get_user)
) -> StreamingResponse:
    require_feature("exports", "Exports are unavailable.")
    await check_rate_limit("export", getattr(user, "id", 0))
    fmt = (format or "json").lower()
    if fmt not in ("json", "csv", "txt"):
        raise HTTPException(status_code=400, detail="format must be json, csv or txt")
    run, wf_name, run_org = await _load_run_row(run_id, user, with_logs=True)
    item = build_conversation_item(
        run, wf_name, storage_configured=_recording_storage_configured(), mask_contact_value=False
    )
    turns = timed_transcript_turns(run.logs)
    if fmt == "csv":
        payload, media = build_csv([item]), "text/csv; charset=utf-8"
    elif fmt == "txt":
        payload, media = transcript_as_text(item, turns).encode("utf-8"), "text/plain; charset=utf-8"
    else:
        payload, media = build_json([item], transcripts={run.id: turns}), "application/json; charset=utf-8"
    filename = export_filename(f"spx-conversation-{run.id}", fmt)
    return StreamingResponse(
        iter([payload]),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.get("/conversations/{run_id}")
async def get_conversation_detail(
    run_id: int,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Full conversation detail. Read-only: never recomputes or writes analysis."""
    run, wf_name, run_org = await _load_run_row(run_id, user, with_logs=True)
    storage_configured = _recording_storage_configured()
    item = build_conversation_item(
        run, wf_name, storage_configured=storage_configured, mask_contact_value=False
    )
    transcript = transcript_text(run)
    turns = timed_transcript_turns(run.logs)
    analysis = (run.annotations or {}).get("analysis") or {}
    return {
        **item,
        "transcript": transcript,
        "transcript_turns": turns,
        "analysis_retryable": analysis.get("retryable"),
        "transcript_status": (
            "available"
            if transcript
            else ("not_applicable" if item["run_status"]["status"] in ("not_started", "starting", "failed", "stale") else "empty")
        ),
        "timestamps_available": any(t["timestamp"] for t in turns),
        "lead_intelligence": (run.annotations or {}).get("lead_intelligence") or {},
        "analysis": {
            "status": analysis.get("status") or item["analysis_status"],
            "analyzer_version": analysis.get("analyzer_version"),
            "analyzed_at": analysis.get("analyzed_at"),
            "error": analysis.get("error"),
            "ai": analysis.get("ai") or {},
            "owner_overrides": analysis.get("owner_overrides") or {},
            "owner_history": (analysis.get("owner_history") or [])[-50:],
        },
        # The storage key is deliberately not returned; playback goes through the verified,
        # short-lived /recording endpoint.
        "recording": {
            k: v
            for k, v in recording_status(run, storage_configured=storage_configured).items()
            if k != "key"
        },
        "cost_links": {"owner_costs": "/costs", "superadmin_costs": f"/superadmin/costs?runId={run.id}"},
    }


@router.get("/conversations/{run_id}/recording")
async def get_conversation_recording(
    run_id: int, download: bool = False, user: UserModel = Depends(get_user)
) -> Dict[str, Any]:
    """Authorized, short-lived playback/download link.

    A link is returned only after the storage object is verified to exist. Every
    other situation returns an honest status instead of a playable link.
    """
    from api.services.livekit import post_call as _post_call

    run, _wf, _org = await _load_run_row(run_id, user)
    rec = recording_status(run, storage_configured=_recording_storage_configured())
    base = {"run_id": run.id, "status": rec["status"], "detail": rec["detail"], "playable": False}
    if not rec["playable"]:
        return base
    key = _post_call.recording_object_key_from_url_or_key(rec.get("key") or run.recording_url)
    if not key:
        # Legacy storage: the existing authenticated signed-URL route owns playback.
        return {**base, "playable": True, "legacy_key": run.recording_url, "url": None}
    exists = await _post_call.recording_object_exists(key)
    if exists is None:
        return {**base, "status": "unavailable", "detail": "Recording storage could not be reached to verify the file."}
    if exists is False:
        return {**base, "status": "failed", "detail": "The recording file was not found in storage."}
    url = await _post_call.generate_recording_signed_url(
        key,
        expiration=900,
        force_inline=not download,
        download_filename=f"conversation-{run.id}.mp3" if download else None,
    )
    if not url:
        return {**base, "status": "unavailable", "detail": "A secure link could not be generated."}
    return {**base, "playable": True, "url": url, "expires_in": 900, "download": bool(download)}


_ALLOWED_CLASSIFICATIONS = {
    "Hot", "Warm", "Cold", "Won", "Unqualified", "Needs Review", "Callback Requested",
    "Human Assistance Required", "Not Interested", "Do Not Contact", "Voicemail / No Human Answer",
}


@router.patch("/conversations/{run_id}/lead")
async def update_lead_intelligence(
    run_id: int,
    request: UpdateLeadRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Owner corrections. AI evidence is preserved; each change is audited with before/after."""
    run, wf_name, run_org = await _load_run_row(run_id, user)

    patch: Dict[str, Any] = {}
    if request.lead_classification is not None:
        if request.lead_classification not in _ALLOWED_CLASSIFICATIONS:
            raise HTTPException(status_code=400, detail="Unsupported lead classification")
        patch["lead_classification"] = request.lead_classification
    if request.lead_score is not None:
        if not 0 <= request.lead_score <= 100:
            raise HTTPException(status_code=400, detail="lead_score must be between 0 and 100")
        patch["lead_score"] = request.lead_score
    if request.customer_requirements is not None:
        if len(request.customer_requirements) > 40 or len(str(request.customer_requirements)) > 4000:
            raise HTTPException(status_code=400, detail="customer_requirements is too large")
        patch["customer_requirements"] = request.customer_requirements
    if request.recommended_next_action is not None:
        if len(request.recommended_next_action) > 500:
            raise HTTPException(status_code=400, detail="recommended_next_action is too long")
        patch["recommended_next_action"] = request.recommended_next_action
    if request.title is not None:
        if not request.title.strip() or len(request.title) > 120:
            raise HTTPException(status_code=400, detail="title must be 1-120 characters")
        patch["title"] = request.title.strip()
    if request.owner_notes is not None and len(request.owner_notes) > 2000:
        raise HTTPException(status_code=400, detail="owner_notes is too long")
    if request.appointment_status is not None:
        valid_statuses = {"Requested", "Confirmed", "Rescheduled", "Completed", "Cancelled"}
        if request.appointment_status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid appointment status '{request.appointment_status}'. Must be one of: {sorted(valid_statuses)}",
            )
        patch["appointment_status"] = request.appointment_status
    reset_fields = [f for f in (request.reset_fields or []) if f in OWNER_OVERRIDABLE_FIELDS]

    actor = _actor(user)

    def _mutate(current: Dict[str, Any]):
        if "appointment_status" in patch:
            existing = ((current.get("lead_intelligence") or {}).get("appointment") or {}).get("status")
            if existing == "Completed" and patch["appointment_status"] != "Completed":
                raise ValueError(
                    "Invalid appointment state transition: Cannot change status of an already Completed appointment."
                )
        updated = apply_owner_override(current, patch, actor=actor, reset_fields=reset_fields)
        if request.owner_notes is not None and request.owner_notes != updated.get("owner_notes", ""):
            history = list((updated.get("analysis") or {}).get("owner_history") or [])
            history.append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "by": actor,
                    "field": "owner_notes",
                    "before": f"{len(updated.get('owner_notes') or '')} chars",
                    "after": f"{len(request.owner_notes)} chars",
                }
            )
            updated.setdefault("analysis", {})["owner_history"] = history[-200:]
            updated["owner_notes"] = request.owner_notes
        return updated, True

    try:
        annotations, _ = await db_client.mutate_workflow_run_annotations(
            run.id, _mutate, organization_id=run_org
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if annotations is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    intel = annotations.get("lead_intelligence") or {}

    try:
        await audit_service.record_event(
            organization_id=run_org,
            action_type="audit.crm.lead_corrected",
            target_resource="workflow_run",
            target_id=str(run.id),
            actor_id=str(getattr(user, "id", "")),
            actor_role=actor["role"],
            changed_fields={
                "fields": sorted(list(patch.keys()) + (["owner_notes"] if request.owner_notes is not None else [])),
                "lead_classification": patch.get("lead_classification"),
            },
            workflow_run_id=run.id,
        )
    except Exception as exc:
        logger.warning(f"[Audit] lead correction audit write failed: {type(exc).__name__}")

    try:
        await realtime_dashboard_bus.publish_event(
            organization_id=run_org,
            event_type="lead_updated",
            resource_type="lead",
            resource_id=str(run.id),
            data={
                "run_id": run.id,
                "lead_intelligence": intel,
                "owner_notes": annotations.get("owner_notes", ""),
            },
        )
    except Exception as e:
        logger.warning(f"[RealtimeBus] Failed to publish lead_updated event: {e}")

    phone = (
        (run.initial_context or {}).get("phone_number")
        or (run.gathered_context or {}).get("customer_phone")
        or (run.gathered_context or {}).get("phone_number")
    )
    if phone:
        try:
            await record_customer_interaction(
                organization_id=run_org,
                phone_number=phone,
                lead_status=intel.get("lead_classification"),
                workflow_run_id=run.id,
                notes=request.owner_notes,
            )
        except Exception as e:
            logger.warning(f"[CustomerProfile] Failed to update profile on lead change: {e}")

    analysis = annotations.get("analysis") or {}
    return {
        "status": "updated",
        "run_id": run.id,
        "lead_intelligence": intel,
        "owner_notes": annotations.get("owner_notes", ""),
        "owner_overrides": analysis.get("owner_overrides") or {},
        "owner_history": (analysis.get("owner_history") or [])[-50:],
    }


@router.post("/conversations/{run_id}/analyze")
async def reanalyze_conversation(
    run_id: int,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Owner-triggered (re)analysis. Owner overrides are preserved; prior AI output is kept."""
    run, wf_name, run_org = await _load_run_row(run_id, user, with_logs=True)
    transcript = transcript_text(run)
    if not transcript:
        raise HTTPException(status_code=400, detail="No transcript available to analyze")

    wf = await db_client.get_workflow_by_id(run.workflow_id)
    biz_ctx = (wf.workflow_configurations or {}).get("agent_training") if wf else None
    intel = analyze_lead_from_transcript(
        transcript,
        business_context=biz_ctx if isinstance(biz_ctx, dict) else None,
        gathered_context=run.gathered_context or {},
    )
    th = transcript_hash(transcript)
    annotations, changed = await db_client.mutate_workflow_run_annotations(
        run.id,
        lambda current: apply_ai_analysis(
            current,
            intel,
            transcript_hash_value=th,
            search_text="\n".join([str(intel.get("title", "")), str(intel.get("summary", "")), transcript]),
        ),
        organization_id=run_org,
    )
    if annotations is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if changed:
        try:
            await realtime_dashboard_bus.publish_event(
                organization_id=run_org,
                event_type="conversation_analysis_completed",
                resource_type="conversation",
                resource_id=str(run.id),
                data={
                    "workflow_run_id": run.id,
                    "analysis_status": (annotations.get("analysis") or {}).get("status"),
                    "lead_classification": (annotations.get("lead_intelligence") or {}).get("lead_classification"),
                },
            )
        except Exception as exc:
            logger.warning(f"[RealtimeBus] analyze event failed: {type(exc).__name__}")
    return {
        "status": "analyzed" if changed else "unchanged",
        "run_id": run.id,
        "lead_intelligence": annotations.get("lead_intelligence") or {},
    }


@router.get("/leads")
async def list_leads(user: UserModel = Depends(get_user)) -> List[Dict[str, Any]]:
    """Returns extracted leads list with scores, status, requirements, and next actions."""
    items = await _legacy_conversation_items(user)
    leads = []
    for it in items:
        h_esc = it.get("human_escalation") or {}
        cb = it.get("callback") or {}
        leads.append({
            "run_id": it["id"],
            "run_name": it.get("name"),
            "name": it.get("name"),
            "contact": it["contact"],
            "customer_name": it["customer_name"],
            "date": it["date"],
            "lead_score": it["lead_score"],
            "lead_classification": it["lead_classification"],
            "customer_requirements": it["customer_requirements"],
            "budget": it["budget"],
            "timeline": it["timeline"],
            "objections": it["objections"],
            "transcript_evidence": it["transcript_evidence"],
            "recommended_next_action": it["recommended_next_action"],
            "owner_notes": it["owner_notes"],
            "is_test": it.get("is_test", False),
            "mode": it.get("mode", ""),
            "call_source": it.get("call_source", "unverified"),
            "human_escalation": h_esc,
            "callback": cb,
            "human_handover": bool(h_esc.get("requested") or it.get("lead_classification") == "Human Assistance Required"),
            "callback_requested": bool(cb.get("requested") or "callback" in str(it.get("recommended_next_action", "")).lower()),
        })
    return leads


@router.get("/appointments")
async def list_appointments(user: UserModel = Depends(get_user)) -> List[Dict[str, Any]]:
    """Returns appointment and consultation requests."""
    items = await _legacy_conversation_items(user)
    appts = []
    for it in items:
        appt = it.get("appointment") or {}
        if appt.get("requested"):
            appts.append({
                "run_id": it["id"],
                "run_name": it.get("name"),
                "name": it.get("name"),
                "contact": it["contact"],
                "customer_name": it["customer_name"],
                "date": it["date"],
                "appointment_type": appt.get("type", "Consultation"),
                "preferred_slot": appt.get("preferred_slot", "To be confirmed"),
                "status": appt.get("status", "Requested"),
                "lead_classification": it["lead_classification"],
                "lead_score": it["lead_score"],
                "evidence": it["transcript_evidence"],
                "agent_name": it["agent_name"],
                "is_test": it.get("is_test", False),
                "mode": it.get("mode", ""),
                "call_source": it.get("call_source", "unverified"),
            })

    return appts





# ---------------------------------------------------------------------------

# Simulated Call Runner (Inbound / Outbound testing without real SIP)

# ---------------------------------------------------------------------------



@router.post("/test-call/simulate")

async def simulate_call(

    request: SimulateCallRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Simulates an inbound or outbound call turn, running conversation state,

    lead intelligence, and storing everything in PostgreSQL.

    """

    workflow = await _get_primary_workflow(user, workflow_id=getattr(request, "workflow_id", None))



    # Standard scenario dialogues for realistic commercial demonstrations

    dialogues: List[Dict[str, str]] = []

    if request.sample_dialogue:

        dialogues = request.sample_dialogue

    elif request.scenario == "real_estate_site_visit":

        dialogues = [

            {"speaker": "Agent", "text": "Namaste! Welcome to Sri Krishna Realty. My name is Priya. Are you looking to purchase a flat, explore luxury villas, or schedule a project site visit today?"},

            {"speaker": "Caller", "text": "Namaskaram Priya andi. We are looking for a 3BHK flat in Gachibowli or Kokapet area."},

            {"speaker": "Agent", "text": "Wonderful! We have our flagship Skyline Heights in Gachibowli offering spacious 3BHK highrise flats starting from 1.45 Crores. What is your comfortable budget range and purchase timeline?"},

            {"speaker": "Caller", "text": "Our budget is around 1.5 to 1.7 Crores. We want to move in within 3 months, before Diwali."},

            {"speaker": "Agent", "text": "Skyline Heights is ready for possession and fits your 1.7 Cr budget perfectly with HMDA and RERA approvals. Would you like to schedule an exclusive on-site property tour this Saturday around 11:00 AM?"},

            {"speaker": "Caller", "text": "Yes, this Saturday at 11 AM works fine for my family. Please send the location and book the visit."},

            {"speaker": "Agent", "text": "Confirmed! We have booked your site visit for Saturday at 11:00 AM. Our sales manager will send the directions and assist you. Thank you for calling Sri Krishna Realty. Namaskaram!"}

        ]

    elif request.scenario == "clinic_appointment":

        dialogues = [

            {"speaker": "Agent", "text": "Namaste! Welcome to Sanjeevani Clinic. I am Aditi. How may I assist you with doctor consultation or medical tests today?"},

            {"speaker": "Caller", "text": "Hello, my father has had mild fever and body aches since yesterday. Can we consult a General Physician today evening?"},

            {"speaker": "Agent", "text": "Certainly, Dr. Sharma, our senior general physician, is available today evening between 5:30 PM and 8:00 PM. May I know your father's name and confirm a 6:00 PM slot?"},

            {"speaker": "Caller", "text": "His name is Venkat Rao. 6:00 PM today works great."},

            {"speaker": "Agent", "text": "Your consultation with Dr. Sharma is booked for today at 6:00 PM. We are located near Madhapur Metro Station. Wishing him a quick recovery. Namaste!"}

        ]

    elif request.scenario == "gym_trial":

        dialogues = [

            {"speaker": "Agent", "text": "Hi! Welcome to IronFit Fitness Club. I am Kavya. Are you looking to inquire about memberships or book a free trial workout?"},

            {"speaker": "Caller", "text": "Hi Kavya, I want to lose weight and build stamina. Do you have morning workout slots and personal trainers?"},

            {"speaker": "Agent", "text": "Yes! Our morning slots run from 6:00 AM to 10:00 AM with certified trainers. We have an annual membership offer of â‚¹17,999. Would you like to try a free 1-Day Trial workout tomorrow morning at 7:00 AM?"},

            {"speaker": "Caller", "text": "Sure, please book my trial session for tomorrow morning at 7 AM."},

            {"speaker": "Agent", "text": "You're all set for tomorrow at 7:00 AM! Please bring workout shoes. See you at IronFit!"}

        ]

    else:

        dialogues = [

            {"speaker": "Agent", "text": "Namaste! Welcome to our customer desk. How may I help you today?"},

            {"speaker": "Caller", "text": f"Hi, I am {request.customer_name}. I have an enquiry regarding your services and would like a callback."},

            {"speaker": "Agent", "text": "Thank you! I have noted your details and our representative will call you back shortly. Have a great day!"}

        ]



    # Convert dialogue to transcript

    transcript = "\n".join([f"{d.get('speaker', 'Caller')}: {d.get('text', '')}" for d in dialogues])



    # Run Lead Intelligence

    intel = analyze_lead_from_transcript(transcript)



    # Create WorkflowRunModel record

    ct = "inbound" if request.call_type.lower() == "inbound" else "outbound"

    run_name = f"SIM-{request.call_type.upper()}-{int(datetime.now(UTC).timestamp())}"

    

    logs_data = {

        "messages": [

            {"role": d.get("speaker", "Caller").lower(), "content": d.get("text", "")}

            for d in dialogues

        ]

    }



    initial_ctx = {

        "phone_number": request.customer_phone,

        "caller_name": request.customer_name,

        "simulated": True,

    }



    product_mode = (workflow.workflow_configurations or {}).get("product_mode", "standard")

    voice_engine_label = (

        "Gemini Live Native Speech (Standard)"

        if product_mode != "premium"

        else "DeepSeek Flash + Cartesia TTS (Premium)"

    )



    gathered_ctx = {

        "customer_name": request.customer_name,

        "customer_phone": request.customer_phone,

        "telephony_snapshot": {

            "provider": "VoiceLink (Simulated)",

            "gateway": "sip.voicelink.co.in",

            "dlt_entity_id": "DLT-1100445588",

        },

        "voice_engine": voice_engine_label,

        "lead_intelligence": intel,

    }



    annotations_data = {

        "lead_intelligence": intel,

        "summary": intel.get("customer_intent", "Simulated conversation completed."),

        "is_simulated": True,

    }



    cost_calc = calculate_call_cost(

        mode=product_mode,

        duration_seconds=138.0,

        is_outbound=(ct == "outbound"),

    )

    if not check_budget_admission(

        current_spend_inr=0.0,

        spending_cap_inr=1000.0,

        estimated_call_cost_inr=cost_calc["total_cost_inr"],

    ):

        raise HTTPException(

            status_code=402, detail="Call rejected: Business spending cap exceeded."

        )



    usage_data = {

        "duration_seconds": 138.0,

        "billed_pulses": cost_calc["billed_pulses"],

        "currency": "INR",

        "cost_inr": cost_calc["total_cost_inr"],

        "effective_rate_per_min": cost_calc["effective_rate_per_min"],

        "ceiling_rate_per_min": cost_calc["ceiling_rate_per_min"],

        "is_within_ceiling": cost_calc["is_within_ceiling"],

        "itemized": cost_calc["itemized"],

    }



    async with db_client.async_session() as session:

        new_run = WorkflowRunModel(

            name=run_name,

            workflow_id=workflow.id,

            mode="simulated",

            call_type=ct,

            state="completed",

            is_completed=True,

            initial_context=initial_ctx,

            gathered_context=gathered_ctx,

            logs=logs_data,

            annotations=annotations_data,

            usage_info=usage_data,

            cost_info={"currency": "INR", "total_cost": cost_calc["total_cost_inr"]},

        )

        session.add(new_run)

        await session.commit()

        await session.refresh(new_run)



    # Phase 5: Update Customer Profile & Identity Continuity (P2-08)

    try:

        await record_customer_interaction(

            organization_id=user.selected_organization_id,

            phone_number=request.customer_phone,

            name=request.customer_name,

            lead_status=intel.get("lead_classification", "Warm"),

            workflow_run_id=new_run.id,

            notes=f"Simulated {request.call_type} call ({request.scenario})",

            channel="simulation",

        )

    except Exception as e:

        logger.warning(f"[CustomerProfile] Failed to record interaction in simulation: {e}")



    # Phase 5: Publish Realtime Events to Dashboard Event Bus

    try:

        await realtime_dashboard_bus.publish_event(

            organization_id=user.selected_organization_id,

            event_type="call_completed",

            resource_type="call",

            resource_id=str(new_run.id),

            data={

                "run_id": new_run.id,

                "name": new_run.name,

                "call_type": ct,

                "customer_name": request.customer_name,

                "customer_phone": request.customer_phone,

                "lead_classification": intel.get("lead_classification"),

                "lead_score": intel.get("lead_score"),

                "duration_seconds": 138.0,

            },

        )

        await realtime_dashboard_bus.publish_event(

            organization_id=user.selected_organization_id,

            event_type="lead_created",

            resource_type="lead",

            resource_id=str(new_run.id),

            data=intel,

        )

    except Exception as e:

        logger.warning(f"[RealtimeBus] Failed to publish simulation event: {e}")



    # Phase 5: Trigger Async Chatwoot Outbox Notification for Hot/Warm Leads

    if intel.get("lead_classification") in ("Hot", "Warm"):

        try:

            await chatwoot_outbox_service.enqueue_message(

                organization_id=user.selected_organization_id,

                recipient_phone=request.customer_phone,

                message_type="lead_followup",

                payload={

                    "customer_name": request.customer_name,

                    "lead_classification": intel.get("lead_classification"),

                    "appointment": intel.get("appointment"),

                    "intent": intel.get("customer_intent"),

                },

                workflow_run_id=new_run.id,

                idempotency_key=f"lead_sim_{new_run.id}",

            )

        except Exception as e:

            logger.warning(f"[ChatwootOutbox] Failed to enqueue follow-up: {e}")



    return {

        "status": "success",

        "run_id": new_run.id,

        "run_name": new_run.name,

        "customer_name": request.customer_name,

        "call_type": request.call_type,

        "lead_score": intel["lead_score"],

        "lead_classification": intel["lead_classification"],

        "transcript_evidence": intel["transcript_evidence"],

        "appointment_status": intel["appointment"]["status"],

        "message": "Simulated call processed and logged into database with complete lead intelligence.",

    }





# ---------------------------------------------------------------------------

# Business Costs, Budgeting & Emergency Stop APIs

# ---------------------------------------------------------------------------



class BudgetUpdateRequest(BaseModel):

    daily_cap_inr: Optional[float] = 500.0

    monthly_cap_inr: Optional[float] = 10000.0

    per_call_cap_inr: Optional[float] = 25.0

    mode_preference: Optional[str] = None

    # Sent by the Costs page; accepted for compatibility but never applied.
    tier_preference: Optional[str] = None





class EmergencyStopRequest(BaseModel):

    reason: str = "Owner manual stop"





@router.get("/costs")

async def get_business_costs(
    workflow_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:

    """Returns organization-scoped cost analytics, voice mode selection, and budget status."""

    org_id = user.selected_organization_id

    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)

    workflow_ids = [w.id for w in workflows]



    rates = AUTHORITATIVE_PLATFORM_RATES



    if not workflow_ids:

        return {

            "selected_mode": "standard",

            "rates": rates,

            "total_calls": 0,

            "telecom_calls": 0,

            "not_connected_calls": 0,

            "simulated_calls": 0,

            "total_minutes": 0.0,

            "telecom_minutes": 0.0,

            "simulated_minutes": 0.0,

            "actual_telecom_spend_inr": 0.0,

            "simulated_spend_inr": 0.0,

            "itemized_costs": {"carrier_telephony": 0.0, "ai_engine": 0.0, "infrastructure": 0.0},

            "budget": {

                "daily_cap_inr": 500.0,

                "monthly_cap_inr": 10000.0,

                "per_call_cap_inr": 25.0,

                "current_month_spend_inr": 0.0,

                "remaining_budget_inr": 10000.0,

                "status": "OK",

            },

            "cost_per_lead_inr": 0.0,

            "reconciliation_status": "RECONCILED",

        }



    async with db_client.async_session() as session:

        stmt = (

            select(WorkflowRunModel)

            .options(defer(WorkflowRunModel.logs))

            .where(WorkflowRunModel.workflow_id.in_(workflow_ids))

            .order_by(WorkflowRunModel.created_at.desc())

        )

        res = await session.execute(stmt)

        runs = res.scalars().all()



    telecom_calls = 0

    not_connected_calls = 0

    simulated_calls = 0

    telecom_duration_sec = 0.0

    simulated_duration_sec = 0.0

    qualified_leads = 0

    verified_spend = 0.0

    estimated_spend = 0.0

    verified_count = 0

    pending_count = 0

    failed_count = 0

    estimated_count = 0



    for r in runs:

        is_sim = classify_call_origin(r) in ("simulated", "browser")

        dur = resolve_duration_seconds(r)

        if is_sim:

            simulated_calls += 1

            simulated_duration_sec += dur

        elif dur <= 0:

            # Never connected (failed to start / not started / stale): no carrier minutes were used,

            # so it is neither a "connected" call nor part of the billing-status tally.

            not_connected_calls += 1

            continue

        else:

            telecom_calls += 1

            telecom_duration_sec += dur

            cost_data = r.cost_info or {}

            c_status = cost_data.get("billing_status")

            telecom_cost_inr = float((cost_data.get("actual_cost") or {}).get("telephony_total_inr") or 0.0)

            if c_status == "VERIFIED":

                verified_count += 1

                verified_spend += telecom_cost_inr

            elif c_status == "RECONCILIATION_FAILED":

                failed_count += 1

                estimated_spend += telecom_cost_inr

            elif c_status == "PENDING_RECONCILIATION":

                pending_count += 1

                estimated_spend += telecom_cost_inr

            else:

                estimated_count += 1

                estimated_spend += telecom_cost_inr



        intel = (r.annotations or {}).get("lead_intelligence") or {}

        if not is_sim and intel.get("lead_classification") in ("Hot", "Warm"):

            qualified_leads += 1



    # Pulse billing (60s ceil pulses)

    telecom_pulses = int(math.ceil(telecom_duration_sec / 60.0)) if telecom_duration_sec > 0 else 0

    telecom_minutes = round(telecom_duration_sec / 60.0, 1)

    simulated_minutes = round(simulated_duration_sec / 60.0, 1)



    actual_telecom_spend = round(telecom_pulses * 1.80, 2)

    simulated_spend = round((simulated_duration_sec / 60.0) * 1.80, 2)



    carrier_share = round(telecom_pulses * 0.70, 2)

    ai_share = round(telecom_pulses * 0.90, 2)

    infra_share = round(telecom_pulses * 0.20, 2)



    workflow = await _get_primary_workflow(user, workflow_id=workflow_id)

    saved_budget = (workflow.workflow_configurations or {}).get("budget") or {}

    daily_cap = float(saved_budget.get("daily_cap_inr") or 500.0)

    monthly_cap = float(saved_budget.get("monthly_cap_inr") or 10000.0)

    per_call_cap = float(saved_budget.get("per_call_cap_inr") or 25.0)

    selected_mode = saved_budget.get("mode_preference") or (workflow.workflow_configurations or {}).get("product_mode") or "standard"



    remaining_budget = max(0.0, round(monthly_cap - actual_telecom_spend, 2))

    budget_status = "OK" if remaining_budget > (monthly_cap * 0.1) else ("ALERT" if remaining_budget > 0 else "EXHAUSTED")



    cost_per_lead = round(actual_telecom_spend / max(1, qualified_leads), 2) if qualified_leads > 0 else 0.0



    from api.services.filesystem.storage_quota import get_organization_storage_usage

    storage_usage = await get_organization_storage_usage(org_id)



    # Honest labelling: spend below is the platform tariff applied to resolved call
    # duration (an estimate). Only runs whose carrier CDR was verified count as verified.
    accounting_status = "TARIFF_ESTIMATE"
    if telecom_calls > 0 and verified_count == telecom_calls:
        accounting_status = "VERIFIED"
    elif telecom_calls > 0 and verified_count > 0:
        accounting_status = "PARTIALLY_VERIFIED"

    emergency_stop_active = False
    emergency_stop_reason = saved_budget.get("emergency_stop_reason")
    try:
        import os
        import redis.asyncio as aioredis
        REDIS_URL = os.environ.get("REDIS_URL", "redis://:redissecret@redis:6379/0")
        r_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        em_val = await r_client.get(f"emergency_stop:{org_id}")
        await r_client.aclose()
        if em_val:
            emergency_stop_active = True
    except Exception:
        emergency_stop_active = bool(saved_budget.get("emergency_stop_active", False))



    return {

        "accounting_status": accounting_status,
        "spend_is_estimate": accounting_status != "VERIFIED",
        "spend_basis": (
            "Platform tariff x resolved call duration (60s pulses) for PSTN calls. "
            "Browser and simulated calls are excluded from carrier spend. "
            "Provider invoices are not reconciled here."
        ),

        "selected_mode": selected_mode,

        "rates": rates,

        "total_calls": len(runs),

        "telecom_calls": telecom_calls,

        "not_connected_calls": not_connected_calls,

        "simulated_calls": simulated_calls,

        "total_minutes": round(telecom_minutes + simulated_minutes, 1),

        "telecom_minutes": telecom_minutes,

        "simulated_minutes": simulated_minutes,

        "actual_telecom_spend_inr": actual_telecom_spend,

        "simulated_spend_inr": simulated_spend,

        "itemized_costs": {

            "carrier_telephony": carrier_share,

            "ai_engine": ai_share,

            "infrastructure": infra_share,

        },

        "budget": {

            "daily_cap_inr": daily_cap,

            "monthly_cap_inr": monthly_cap,

            "per_call_cap_inr": per_call_cap,

            "current_month_spend_inr": actual_telecom_spend,

            "remaining_budget_inr": remaining_budget,

            "status": budget_status,

            "emergency_stop_active": emergency_stop_active,

            "emergency_stop_reason": emergency_stop_reason,

        },

        "storage": storage_usage,

        "cost_per_lead_inr": cost_per_lead,

        "verified_telecom_spend_inr": round(verified_spend, 2),

        "estimated_telecom_spend_inr": round(estimated_spend or (actual_telecom_spend - verified_spend), 2),

        "reconciliation_status": (
            "ESTIMATED"
            if telecom_calls == 0
            else (
                "RECONCILIATION_FAILED"
                if failed_count > 0
                else (
                    "PENDING_RECONCILIATION"
                    if pending_count > 0
                    else ("VERIFIED" if verified_count == telecom_calls and verified_count > 0 else "ESTIMATED")
                )
            )
        ),

        "billing_status_breakdown": {
            "verified": verified_count,
            "pending": pending_count,
            "failed": failed_count,
            "estimated": estimated_count,
        },
        "emergency_stop_active": emergency_stop_active,
        "emergency_stop_reason": emergency_stop_reason,

    }





@router.post("/costs/budget")

async def update_business_budget(

    body: BudgetUpdateRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Updates business spending caps only.

    The voice mode, model_overrides and IVR configuration are never changed
    here; mode changes go through the agent draft/publish flow.
    """

    workflow = await _get_primary_workflow(user, workflow_id=getattr(body, "workflow_id", None))

    configs = copy.deepcopy(workflow.workflow_configurations or {})

    # An explicit, different mode request is an unauthorized mode change.
    try:
        check_budget_mode_request(body.mode_preference, configs.get("product_mode"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    caps: Dict[str, float] = {}

    for cap_name in ("daily_cap_inr", "monthly_cap_inr", "per_call_cap_inr"):

        cap_value = getattr(body, cap_name)

        if cap_value is None:
            continue

        if not (0 < cap_value < float("inf")):
            raise HTTPException(
                status_code=422,
                detail=f"{cap_name} must be a positive, finite number.",
            )

        caps[cap_name] = cap_value

    # tier_preference (sent by the Costs page) is intentionally ignored: it can
    # never change the voice mode.
    ignored_fields = ["tier_preference"] if body.tier_preference else []

    async with db_client.async_session() as session:

        stmt = select(WorkflowModel).where(WorkflowModel.id == workflow.id)

        res = await session.execute(stmt)

        live_wf = res.scalars().first()

        if live_wf:

            # Re-read the live row and change ONLY the "budget" key so a
            # concurrent publish (product_mode / model_overrides) is not lost.
            live_configs = copy.deepcopy(live_wf.workflow_configurations or {})

            merged_budget = {**(live_configs.get("budget") or {}), **caps}

            merged_budget.pop("mode_preference", None)

            live_configs["budget"] = merged_budget

            live_wf.workflow_configurations = live_configs

            await session.commit()

            configs = live_configs

    await audit_service.record_event(
        organization_id=user.selected_organization_id,
        action_type="audit.operations.budget_changed",
        target_resource="business_budget",
        target_id=str(workflow.id),
        actor_id=str(user.id),
        actor_role="admin" if user.is_superuser else "user",
        outcome="SUCCESS",
        changed_fields=caps,
    )

    logger.info(

        f"[Budget] Persisted spending caps for org {user.selected_organization_id}: {caps}"

    )

    return {

        "status": "success",

        "message": "Budget settings updated successfully",

        "budget": {
            **(configs.get("budget") or {}),
            "mode_preference": configs.get("product_mode") or "standard",
        },

        "ignored_fields": ignored_fields,

    }



@router.get("/audit/trail")

async def get_business_audit_trail(

    limit: int = 50,

    offset: int = 0,

    action_prefix: Optional[str] = None,

    outcome: Optional[str] = None,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Fetch durable audit trail for the organization with strict tenant isolation."""

    events = await audit_service.get_audit_trail(

        organization_id=user.selected_organization_id,

        user=user,

        limit=limit,

        offset=offset,

        action_prefix=action_prefix,

        outcome=outcome,

    )

    return {

        "organization_id": user.selected_organization_id,

        "count": len(events),

        "events": events,

    }





@router.post("/emergency-stop")
@router.post("/costs/emergency-stop")
async def trigger_emergency_stop(
    body: EmergencyStopRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Emergency stops outbound campaign dispatch and queued calls without dropping active calls."""
    org_id = user.selected_organization_id
    correlation_id = str(uuid.uuid4())
    logger.warning(
        f"[Emergency Stop] Activated for organization {org_id} by user {user.id}. Reason: {body.reason} (correlation: {correlation_id})"
    )

    # 1. Pause active campaigns
    async with db_client.async_session() as session:
        stmt = (
            select(CampaignModel)
            .where(
                CampaignModel.organization_id == org_id,
                CampaignModel.state.in_(["running", "syncing"]),
            )
        )
        res = await session.execute(stmt)
        campaigns = res.scalars().all()
        for c in campaigns:
            c.state = "paused"
        await session.commit()

    # 2. Persist durable emergency lock in primary workflow budget config
    try:
        workflow = await _get_primary_workflow(user, workflow_id=getattr(body, "workflow_id", None))
        if workflow:
            configs = copy.deepcopy(workflow.workflow_configurations or {})
            budget = copy.deepcopy(configs.get("budget") or {})
            budget["emergency_stop_active"] = True
            budget["emergency_stop_reason"] = body.reason
            budget["emergency_stop_activated_at"] = datetime.now(UTC).isoformat()
            configs["budget"] = budget
            async with db_client.async_session() as session:
                stmt = select(WorkflowModel).where(WorkflowModel.id == workflow.id)
                res = await session.execute(stmt)
                live_wf = res.scalars().first()
                if live_wf:
                    live_wf.workflow_configurations = configs
                    await session.commit()
    except Exception as e:
        logger.warning(f"[Emergency Stop] Could not persist budget lock to workflow: {e}")

    # 3. Set Redis emergency stop flag
    try:
        import os
        import redis.asyncio as aioredis
        REDIS_URL = os.environ.get("REDIS_URL", "redis://:redissecret@redis:6379/0")
        r_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r_client.set(f"emergency_stop:{org_id}", "true")
        await r_client.aclose()
    except Exception as e:
        logger.warning(f"[Emergency Stop] Could not set Redis lock: {e}")

    # 4. Record structured audit event
    await audit_service.record_event(
        organization_id=org_id,
        action_type="audit.operations.emergency_stop_activated",
        target_resource="outbound_campaigns",
        target_id=str(org_id),
        actor_id=str(user.id),
        actor_role="admin" if user.is_superuser else "user",
        outcome="SUCCESS",
        changed_fields={
            "reason": body.reason,
            "campaigns_paused": len(campaigns),
            "correlation_id": correlation_id,
        },
    )

    return {
        "status": "stopped",
        "emergency_stop_active": True,
        "message": "Outbound campaigns and queued dispatch stopped successfully. Active customer calls remain protected.",
        "campaigns_paused": len(campaigns),
        "timestamp": datetime.now(UTC).isoformat(),
        "activated_by": user.email,
        "reason": body.reason,
        "correlation_id": correlation_id,
    }


@router.post("/emergency-stop/resume")
@router.post("/costs/emergency-stop/resume")
async def resume_emergency_stop(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Resumes outbound campaign dispatch and clears the emergency stop lock."""
    org_id = user.selected_organization_id
    correlation_id = str(uuid.uuid4())
    logger.info(
        f"[Emergency Stop] Resume requested for organization {org_id} by user {user.id} (correlation: {correlation_id})"
    )

    # 1. Clear durable emergency lock in primary workflow budget config
    try:
        workflow = await _get_primary_workflow(user)
        if workflow:
            configs = copy.deepcopy(workflow.workflow_configurations or {})
            budget = copy.deepcopy(configs.get("budget") or {})
            budget["emergency_stop_active"] = False
            budget.pop("emergency_stop_reason", None)
            budget["emergency_stop_resumed_at"] = datetime.now(UTC).isoformat()
            configs["budget"] = budget
            async with db_client.async_session() as session:
                stmt = select(WorkflowModel).where(WorkflowModel.id == workflow.id)
                res = await session.execute(stmt)
                live_wf = res.scalars().first()
                if live_wf:
                    live_wf.workflow_configurations = configs
                    await session.commit()
    except Exception as e:
        logger.warning(f"[Emergency Stop] Could not clear budget lock from workflow: {e}")

    # 2. Clear Redis emergency stop flag
    try:
        import os
        import redis.asyncio as aioredis
        REDIS_URL = os.environ.get("REDIS_URL", "redis://:redissecret@redis:6379/0")
        r_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r_client.delete(f"emergency_stop:{org_id}")
        await r_client.aclose()
    except Exception as e:
        logger.warning(f"[Emergency Stop] Could not clear Redis lock: {e}")

    # 3. Record structured audit event
    await audit_service.record_event(
        organization_id=org_id,
        action_type="audit.operations.emergency_stop_resumed",
        target_resource="outbound_campaigns",
        target_id=str(org_id),
        actor_id=str(user.id),
        actor_role="admin" if user.is_superuser else "user",
        outcome="SUCCESS",
        changed_fields={
            "action": "resume",
            "correlation_id": correlation_id,
        },
    )

    return {
        "status": "resumed",
        "emergency_stop_active": False,
        "message": "Outbound calling and campaign dispatch lock released.",
        "timestamp": datetime.now(UTC).isoformat(),
        "resumed_by": user.email,
        "correlation_id": correlation_id,
    }





# ---------------------------------------------------------------------------

# Business Owner Action Inbox API

# ---------------------------------------------------------------------------



@router.get("/inbox")

async def list_action_inbox_items(

    item_type: Optional[str] = None,

    status: Optional[str] = None,

    limit: int = 50,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Returns business owner action inbox with pending missed calls, appointments, and callbacks."""

    from api.services.business.action_inbox import action_inbox



    items = await action_inbox.list_action_items(

        organization_id=user.selected_organization_id,

        item_type_filter=item_type,

        status_filter=status,

        limit=limit,

    )

    return {

        "items": items,

        "total_pending": len([i for i in items if i.get("status") == "PENDING"]),

        "count": len(items),

    }





class ActionItemUpdateRequest(BaseModel):

    status: str = "RESOLVED"

    resolution_notes: Optional[str] = None





@router.patch("/inbox/{item_id}")
@router.patch("/inbox/{item_id}/status")
async def update_action_inbox_item(
    item_id: str,
    body: ActionItemUpdateRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Updates status and notes for an action inbox item."""
    from api.services.business.action_inbox import action_inbox

    persisted = await action_inbox.update_item_status(
        item_id=item_id,
        status=body.status,
        organization_id=user.selected_organization_id,
        resolution_notes=body.resolution_notes,
    )

    logger.info(
        f"[Action Inbox] Item {item_id} updated to {body.status} (persisted: {persisted}) by user {user.id}"
    )

    return {
        "id": item_id,
        "status": body.status,
        "persisted": persisted,
        "resolution_notes": body.resolution_notes,
        "updated_at": datetime.now(UTC).isoformat(),
    }



# ---------------------------------------------------------------------------

# Public Website Lead Capture REST API & Widget Backend

# ---------------------------------------------------------------------------



class PublicLeadCaptureRequest(BaseModel):

    name: str

    phone: str

    organization_id: Optional[int] = None

    business_id: Optional[int] = None

    email: Optional[str] = None

    source: Optional[str] = "website_widget"

    workflow_id: Optional[int] = None

    session_id: Optional[str] = None

    call_id: Optional[str] = None

    origin: Optional[str] = None

    landing_page: Optional[str] = None

    referrer: Optional[str] = None

    utm_source: Optional[str] = None

    utm_medium: Optional[str] = None

    utm_campaign: Optional[str] = None

    utm_term: Optional[str] = None

    utm_content: Optional[str] = None

    consent: Optional[bool] = True

    consent_timestamp: Optional[str] = None

    enquiry_requirements: Optional[str] = None

    conversation_outcome: Optional[str] = None

    requirements: Optional[Dict[str, Any]] = None

    preferred_callback_time: Optional[str] = None





@router.post("/leads/capture")

async def capture_public_website_lead(

    body: PublicLeadCaptureRequest,

) -> Dict[str, Any]:

    """Public lead capture endpoint for website widgets and external forms."""

    from api.services.telephony.compliance import compliance_manager, normalize_phone_number



    # 0. Consent & Opt-in Verification

    if body.consent is False:

        return {

            "status": "consent_required",

            "message": "User affirmative consent is required before capturing contact information.",

        }



    org_id = body.organization_id or body.business_id

    if not org_id:

        raise HTTPException(status_code=400, detail="Either organization_id or business_id is required")



    norm_phone = normalize_phone_number(body.phone)



    # 1. DNC Check

    is_suppressed, reason = await compliance_manager.is_suppressed_db(

        organization_id=org_id,

        phone_number=norm_phone,

    )

    if is_suppressed:

        logger.warning(

            f"[Lead Capture] Rejected phone {norm_phone} for org {org_id}: DNC suppressed ({reason})"

        )

        return {

            "status": "suppressed",

            "message": "Phone number is listed in Do-Not-Call suppression registry.",

            "phone": norm_phone,

        }



    # 2. Idempotent Deduplication: Check if active lead was already captured in the last 24h

    now_utc = datetime.now(UTC)

    async with db_client.async_session() as session:

        cutoff = now_utc - timedelta(hours=24)

        stmt_dup = (

            select(WorkflowRunModel)

            .join(WorkflowModel, WorkflowRunModel.workflow_id == WorkflowModel.id)

            .where(

                WorkflowModel.organization_id == org_id,

                WorkflowRunModel.mode == "web_lead",

                WorkflowRunModel.created_at >= cutoff,

            )

            .order_by(WorkflowRunModel.created_at.desc())

        )

        res_dup = await session.execute(stmt_dup)

        recent_runs = res_dup.scalars().all()

        for r in recent_runs:

            existing_phone = (

                (r.initial_context or {}).get("phone_number")

                or (r.gathered_context or {}).get("phone_number")

            )

            if existing_phone == norm_phone:

                existing_name = (r.initial_context or {}).get("customer_name") or ""

                existing_req = ((r.initial_context or {}).get("lead_intelligence") or {}).get("enquiry_requirements") or ""

                new_req = body.enquiry_requirements or (body.requirements or {}).get("enquiry") or (body.requirements or {}).get("description") or ""



                name_differs = bool(existing_name and body.name and existing_name.lower().strip() != body.name.lower().strip())

                req_differs = bool(existing_req and new_req and existing_req.lower().strip() != new_req.lower().strip())



                # Only mark duplicate if it is the same caller and same enquiry

                if not (name_differs or req_differs):

                    logger.info(

                        f"[Lead Capture] Deduplicated existing lead {r.id} for {norm_phone} in org {org_id}"

                    )

                    return {

                        "status": "duplicate",

                        "message": "Duplicate callback request detected within 24h window; existing lead preserved.",

                        "lead_id": r.id,

                        "phone": norm_phone,

                    }



    # 3. Find workflow for organization (prioritize requested workflow_id if provided)

    async with db_client.async_session() as session:

        if body.workflow_id:

            stmt = (

                select(WorkflowModel)

                .where(WorkflowModel.id == body.workflow_id, WorkflowModel.organization_id == org_id)

                .limit(1)

            )

        else:

            stmt = (

                select(WorkflowModel)

                .where(WorkflowModel.organization_id == org_id)

                .order_by(WorkflowModel.id.asc())

                .limit(1)

            )

        res = await session.execute(stmt)

        wf = res.scalar_one_or_none()



    if not wf:

        raise HTTPException(

            status_code=404,

            detail=f"Organization {org_id} has no configured workflows",

        )



    # 4. Intelligent Lead Scoring & Classification

    run_name = f"WEB-LEAD-{body.name[:12].upper()}-{norm_phone[-4:]}"

    now_iso = datetime.now(UTC).isoformat()

    enquiry_req_text = body.enquiry_requirements or (body.requirements or {}).get("enquiry") or (body.requirements or {}).get("description") or ""

    lower_req = enquiry_req_text.lower()



    # Evaluate classification & score

    lead_score = 65

    lead_classification = "Warm"

    if any(k in lower_req for k in ("immediate", "book", "site visit", "appointment", "doctor", "urgent", "today", "tomorrow")):

        lead_classification = "Hot"

        lead_score = 85

    elif any(k in lower_req for k in ("not sure", "just checking", "browsing", "later", "curious")):

        lead_classification = "Cold"

        lead_score = 35



    traffic_attribution = {

        "origin": body.origin,

        "landing_page": body.landing_page,

        "referrer": body.referrer,

        "utm_source": body.utm_source,

        "utm_medium": body.utm_medium,

        "utm_campaign": body.utm_campaign,

        "utm_term": body.utm_term,

        "utm_content": body.utm_content,

    }



    consent_status = {

        "consent": True,

        "captured_at": body.consent_timestamp or now_iso,

    }



    session_identifiers = {

        "business_id": org_id,

        "workflow_id": wf.id,

        "session_id": body.session_id,

        "call_id": body.call_id,

    }



    lead_intel = {

        "customer_name": body.name,

        "lead_classification": lead_classification,

        "lead_score": lead_score,

        "customer_requirements": body.requirements or {"enquiry": enquiry_req_text} if enquiry_req_text else {},

        "enquiry_requirements": enquiry_req_text,

        "conversation_outcome": body.conversation_outcome or "Lead captured via website widget",

        "budget": (body.requirements or {}).get("budget", "Not specified"),

        "timeline": (body.requirements or {}).get("timeline", "Exploring (Website Lead)"),

        "transcript_evidence": f"Captured via {body.source} on {now_iso}",

        "recommended_next_action": f"Call back {body.name} regarding website inquiry: {enquiry_req_text or 'general consultation'}.",

        "traffic_attribution": traffic_attribution,

        "consent_status": consent_status,

        "session_identifiers": session_identifiers,

        "appointment": {

            "requested": bool(body.preferred_callback_time),

            "preferred_slot": body.preferred_callback_time or "Callback requested",

            "status": "Requested" if body.preferred_callback_time else "None",

        },

        "callback": {

            "requested": True,

            "status": "Pending",

        },

    }



    new_run = await db_client.create_workflow_run(

        name=run_name,

        workflow_id=wf.id,

        mode="web_lead",

        user_id=wf.user_id,

        call_type=CallType.OUTBOUND,

        call_disposition="MISSED",  # Pending dial

        initial_context={

            "customer_name": body.name,

            "phone_number": norm_phone,

            "called_number": norm_phone,

            "email": body.email,

            "source": body.source or "website_widget",

            "traffic_attribution": traffic_attribution,

            "consent_status": consent_status,

            "session_identifiers": session_identifiers,

            "lead_intelligence": lead_intel,

        },

        gathered_context={

            "customer_name": body.name,

            "phone_number": norm_phone,

            "lead_intelligence": lead_intel,

        },

        organization_id=org_id,

    )



    # Persist annotations

    await db_client.update_workflow_run(

        run_id=new_run.id,

        annotations={

            "lead_intelligence": lead_intel,

            "owner_notes": f"Website Lead from {body.source or 'website_widget'}",

            "traffic_attribution": traffic_attribution,

            "consent_status": consent_status,

            "session_identifiers": session_identifiers,

        },

    )



    logger.info(

        f"[Lead Capture] Successfully captured lead {new_run.id} for {body.name} ({norm_phone}) in org {org_id}"

    )



    return {

        "status": "success",

        "message": "Lead received successfully. An AI specialist or executive will connect shortly.",

        "lead_id": new_run.id,

        "phone": norm_phone,

    }





# ---------------------------------------------------------------------------

# Phase 5 Workstream A: Server-Sent Events (SSE) & Real-Time Event Bus

# ---------------------------------------------------------------------------



@router.post("/events/ticket")
async def create_sse_connection_ticket(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Issues a short-lived (30s), single-use connection ticket for Server-Sent Events."""
    _require_org_id(user)  # an unscoped user must not hold a stream ticket
    ticket = await realtime_dashboard_bus.create_sse_ticket(user)
    return {"ticket": ticket, "expires_in_seconds": 30}


async def get_user_sse(
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Query(None),
    ticket: Optional[str] = Query(None),
) -> UserModel:
    # 1. Prefer Authorization: Bearer <jwt> header if present
    if authorization:
        return await get_user(authorization=authorization)

    # 2. If token query parameter is provided, it MUST be a short-lived single-use SSE ticket
    ticket_val = ticket if isinstance(ticket, str) and ticket else None
    token_val = token if isinstance(token, str) and token else None
    ticket_candidate = ticket_val or token_val
    if ticket_candidate:
        if ticket_candidate.startswith("eyJ"):
            raise HTTPException(
                status_code=401,
                detail="Long-lived JWT tokens are not permitted in URL query parameters. Use POST /events/ticket to obtain a short-lived connection ticket, or pass Authorization header."
            )
        user = await realtime_dashboard_bus.redeem_sse_ticket(ticket_candidate)
        if not user:
            raise HTTPException(
                status_code=401,
                detail="Invalid, expired, or already redeemed SSE connection ticket."
            )
        return user

    raise HTTPException(
        status_code=401,
        detail="Authentication required: provide Authorization header or valid SSE connection ticket"
    )


@router.get("/events/stream")
async def stream_dashboard_events(
    last_event_id: Optional[int] = Query(None),
    user: UserModel = Depends(get_user_sse),
) -> StreamingResponse:

    """Streams real-time organization-scoped events via Server-Sent Events (SSE).

    

    Supports cursor-based reconnection recovery if last_event_id is provided.

    """

    return StreamingResponse(

        realtime_dashboard_bus.event_generator(

            _require_org_id(user), last_event_id=last_event_id

        ),

        media_type="text/event-stream",

        headers={

            # no-transform: the UI's Next.js proxy gzips everything else and buffers the stream, so no live
            # event ever reached a browser (verified: 10 bytes in 12 s with Accept-Encoding: gzip).
            "Cache-Control": "no-cache, no-transform",

            "X-Accel-Buffering": "no",

            "Connection": "keep-alive",

        },

    )





@router.post("/events/publish")

async def publish_dashboard_event(

    body: PublishEventRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Publishes a custom event to the organization's real-time event bus."""

    org_id = _require_org_id(user)
    if body.event_type in RESERVED_EVENT_TYPES or body.event_type.startswith("audit."):
        raise HTTPException(status_code=400, detail="This event type is reserved for the platform")
    event = await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type=body.event_type,

        resource_type=body.resource_type,

        resource_id=body.resource_id,

        data=body.data,

    )

    return {"status": "published", "event": event}





# ---------------------------------------------------------------------------

# Phase 5 Workstream B: Bidirectional Owner Actions & Two-Phase Acknowledgement

# ---------------------------------------------------------------------------



@router.post("/actions/execute")

async def execute_owner_action(

    body: ExecuteActionRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Executes owner commands with two-phase verification and real-time acknowledgment."""

    org_id = user.selected_organization_id

    now = datetime.now(UTC)

    result_data: Dict[str, Any] = {}



    action_type = body.action_type.lower()

    target_id = body.target_id

    payload = body.payload or {}



    if action_type == "update_lead_classification":

        try:

            run_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target run_id")

        run = await db_client.get_workflow_run_by_id(run_id)

        if not run:

            raise HTTPException(status_code=404, detail="Run not found")

        wf = await db_client.get_workflow_by_id(run.workflow_id)

        if not wf or (wf.organization_id != org_id and not user.is_superuser):

            raise HTTPException(status_code=403, detail="Forbidden: Run belongs to another organization")

        

        ann = copy.deepcopy(run.annotations or {})

        intel = copy.deepcopy(ann.get("lead_intelligence") or {})

        if "lead_classification" in payload:

            intel["lead_classification"] = payload["lead_classification"]

        if "lead_score" in payload:

            intel["lead_score"] = int(payload["lead_score"])

        ann["lead_intelligence"] = intel

        await db_client.update_workflow_run(run.id, annotations=ann)

        result_data = {"run_id": run.id, "lead_classification": intel.get("lead_classification")}



    elif action_type == "reschedule_callback":

        try:

            run_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target run_id")

        run = await db_client.get_workflow_run_by_id(run_id)

        if not run:

            raise HTTPException(status_code=404, detail="Run not found")

        wf = await db_client.get_workflow_by_id(run.workflow_id)

        if not wf or (wf.organization_id != org_id and not user.is_superuser):

            raise HTTPException(status_code=403, detail="Forbidden: Run belongs to another organization")



        ann = copy.deepcopy(run.annotations or {})

        intel = copy.deepcopy(ann.get("lead_intelligence") or {})

        cb = copy.deepcopy(intel.get("callback") or {})

        cb["status"] = "Rescheduled"

        cb["rescheduled_slot"] = payload.get("new_slot", "Tomorrow 11:00 AM")

        intel["callback"] = cb

        ann["lead_intelligence"] = intel

        await db_client.update_workflow_run(run.id, annotations=ann)

        result_data = {"run_id": run.id, "callback": cb}



    elif action_type == "request_human_handover":

        try:

            run_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target run_id")

        run = await db_client.get_workflow_run_by_id(run_id)

        if not run:

            raise HTTPException(status_code=404, detail="Run not found")

        wf = await db_client.get_workflow_by_id(run.workflow_id)

        if not wf or (wf.organization_id != org_id and not user.is_superuser):

            raise HTTPException(status_code=403, detail="Forbidden: Run belongs to another organization")



        ann = copy.deepcopy(run.annotations or {})

        ann["human_handover_requested"] = True

        ann["human_handover_at"] = now.isoformat()

        await db_client.update_workflow_run(run.id, annotations=ann)

        result_data = {"run_id": run.id, "human_handover_requested": True}



    elif action_type in ("pause_campaign", "resume_campaign", "cancel_campaign"):

        try:

            camp_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target campaign_id")

        camp = await db_client.get_campaign(camp_id, org_id)

        if not camp:

            raise HTTPException(status_code=404, detail="Campaign not found")

        from api.services.campaign.runner import campaign_runner_service

        if action_type == "pause_campaign":

            await campaign_runner_service.pause_campaign(camp_id)

            updated_camp = await db_client.get_campaign_by_id(camp_id)

            result_data = {"campaign_id": camp_id, "state": getattr(updated_camp, "state", "paused"), "status": "PAUSED"}

        elif action_type == "resume_campaign":

            await campaign_runner_service.resume_campaign(camp_id)

            updated_camp = await db_client.get_campaign_by_id(camp_id)

            result_data = {"campaign_id": camp_id, "state": getattr(updated_camp, "state", "running"), "status": "RUNNING"}

        elif action_type == "cancel_campaign":

            await campaign_runner_service.cancel_campaign(camp_id, user_id=user.id)

            updated_camp = await db_client.get_campaign_by_id(camp_id)

            result_data = {"campaign_id": camp_id, "state": getattr(updated_camp, "state", "completed"), "status": "CANCELLED"}



    elif action_type == "approve_objection":

        try:

            obj_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target objection_id")

        async with db_client.async_session() as session:

            stmt = select(ObjectionResponseModel).where(

                ObjectionResponseModel.id == obj_id,

                ObjectionResponseModel.organization_id == org_id,

            )

            res = await session.execute(stmt)

            obj = res.scalar_one_or_none()

            if not obj:

                raise HTTPException(status_code=404, detail="Objection not found")

            obj.status = "APPROVED"

            obj.version_number += 1

            if "approved_response" in payload:

                obj.approved_response = payload["approved_response"]

            elif not obj.approved_response:

                obj.approved_response = obj.draft_response

            obj.approved_by_user_id = user.id

            obj.updated_at = now

            await session.commit()

            result_data = {

                "objection_id": obj.id,

                "status": obj.status,

                "version_number": obj.version_number,

                "approved_response": obj.approved_response,

            }



    elif action_type == "reject_objection":

        try:

            obj_id = int(target_id)

        except ValueError:

            raise HTTPException(status_code=400, detail="Invalid target objection_id")

        async with db_client.async_session() as session:

            stmt = select(ObjectionResponseModel).where(

                ObjectionResponseModel.id == obj_id,

                ObjectionResponseModel.organization_id == org_id,

            )

            res = await session.execute(stmt)

            obj = res.scalar_one_or_none()

            if not obj:

                raise HTTPException(status_code=404, detail="Objection not found")

            obj.status = "REJECTED"

            obj.updated_at = now

            await session.commit()

            result_data = {"objection_id": obj.id, "status": obj.status}



    elif action_type == "trigger_whatsapp":

        phone = payload.get("recipient_phone") or target_id

        job = await chatwoot_outbox_service.enqueue_message(

            organization_id=org_id,

            recipient_phone=phone,

            message_type=payload.get("message_type", "template"),

            payload=payload.get("payload", {}),

            workflow_run_id=payload.get("workflow_run_id"),

        )

        result_data = job



    else:

        raise HTTPException(status_code=400, detail=f"Unsupported action_type: {body.action_type}")



    # Emit real-time event for the executed action

    await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type="owner_action_acknowledged",

        resource_type="action",

        resource_id=body.action_id,

        data={

            "action_id": body.action_id,

            "action_type": body.action_type,

            "target_id": body.target_id,

            "execution_status": "SUCCESS",

            "result": result_data,

        },

    )



    return {

        "status": "acknowledged",

        "action_id": body.action_id,

        "action_type": body.action_type,

        "target_id": body.target_id,

        "execution_status": "SUCCESS",

        "executed_at": now.isoformat(),

        "result": result_data,

    }





# ---------------------------------------------------------------------------

# Phase 5 Workstream C3: Objection Intelligence (P2-07)

# ---------------------------------------------------------------------------



@router.get("/objections")

async def list_objections(

    status: Optional[str] = None,

    page: int = 1,

    page_size: int = 50,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Lists objection responses for the organization with version history."""

    org_id = user.selected_organization_id

    offset = max(0, (page - 1) * page_size)



    async with db_client.async_session() as session:

        query = select(ObjectionResponseModel).where(

            ObjectionResponseModel.organization_id == org_id

        )

        if status:

            query = query.where(ObjectionResponseModel.status == status.upper())

        query = query.order_by(ObjectionResponseModel.updated_at.desc()).offset(offset).limit(page_size)

        res = await session.execute(query)

        rows = res.scalars().all()



        count_query = select(func.count(ObjectionResponseModel.id)).where(

            ObjectionResponseModel.organization_id == org_id

        )

        if status:

            count_query = count_query.where(ObjectionResponseModel.status == status.upper())

        count_res = await session.execute(count_query)

        total = count_res.scalar_one() or 0



    return {

        "items": [

            {

                "id": r.id,

                "organization_id": r.organization_id,

                "objection_category": r.objection_category,

                "objection_text": r.objection_text,

                "draft_response": r.draft_response,

                "approved_response": r.approved_response,

                "status": r.status,

                "version_number": r.version_number,

                "approved_by_user_id": r.approved_by_user_id,

                "created_at": r.created_at.isoformat() if r.created_at else None,

                "updated_at": r.updated_at.isoformat() if r.updated_at else None,

            }

            for r in rows

        ],

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@router.post("/objections/draft")

async def create_draft_objection(

    body: DraftObjectionRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Creates a draft objection response awaiting business owner review."""

    org_id = user.selected_organization_id

    now = datetime.now(UTC)



    async with db_client.async_session() as session:

        new_obj = ObjectionResponseModel(

            organization_id=org_id,

            objection_category=body.objection_category,

            objection_text=body.objection_text,

            draft_response=body.draft_response,

            status="DRAFT",

            version_number=1,

            created_at=now,

            updated_at=now,

        )

        session.add(new_obj)

        await session.commit()

        await session.refresh(new_obj)



    await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type="objection_draft_created",

        resource_type="objection",

        resource_id=str(new_obj.id),

        data={"id": new_obj.id, "category": new_obj.objection_category, "status": "DRAFT"},

    )



    return {

        "status": "success",

        "objection": {

            "id": new_obj.id,

            "category": new_obj.objection_category,

            "draft_response": new_obj.draft_response,

            "version_number": new_obj.version_number,

            "status": new_obj.status,

        },

    }





@router.post("/objections/{id}/approve")

async def approve_objection(

    id: int,

    body: ApproveObjectionRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Approves an objection response, incrementing version and syncing with agent training."""

    org_id = user.selected_organization_id

    now = datetime.now(UTC)



    async with db_client.async_session() as session:

        stmt = select(ObjectionResponseModel).where(

            ObjectionResponseModel.id == id,

            ObjectionResponseModel.organization_id == org_id,

        )

        res = await session.execute(stmt)

        obj = res.scalar_one_or_none()

        if not obj:

            raise HTTPException(status_code=404, detail="Objection not found")



        obj.status = "APPROVED"

        obj.version_number += 1

        if body.approved_response:

            obj.approved_response = body.approved_response

        elif not obj.approved_response:

            obj.approved_response = obj.draft_response

        obj.approved_by_user_id = user.id

        obj.updated_at = now

        await session.commit()

        await session.refresh(obj)



    await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type="objection_approved",

        resource_type="objection",

        resource_id=str(obj.id),

        data={

            "id": obj.id,

            "version_number": obj.version_number,

            "status": "APPROVED",

            "approved_response": obj.approved_response,

        },

    )



    return {

        "status": "approved",

        "id": obj.id,

        "version_number": obj.version_number,

        "approved_response": obj.approved_response,

    }





@router.post("/objections/{id}/reject")

async def reject_objection(

    id: int,

    body: RejectObjectionRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Rejects an objection response draft."""

    org_id = user.selected_organization_id

    now = datetime.now(UTC)



    async with db_client.async_session() as session:

        stmt = select(ObjectionResponseModel).where(

            ObjectionResponseModel.id == id,

            ObjectionResponseModel.organization_id == org_id,

        )

        res = await session.execute(stmt)

        obj = res.scalar_one_or_none()

        if not obj:

            raise HTTPException(status_code=404, detail="Objection not found")



        obj.status = "REJECTED"

        obj.updated_at = now

        await session.commit()

        await session.refresh(obj)



    await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type="objection_rejected",

        resource_type="objection",

        resource_id=str(obj.id),

        data={"id": obj.id, "status": "REJECTED"},

    )



    return {"status": "rejected", "id": obj.id}





@router.post("/objections/{id}/rollback")

async def rollback_objection(

    id: int,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Rolls back an objection response to draft state."""

    org_id = user.selected_organization_id

    now = datetime.now(UTC)



    async with db_client.async_session() as session:

        stmt = select(ObjectionResponseModel).where(

            ObjectionResponseModel.id == id,

            ObjectionResponseModel.organization_id == org_id,

        )

        res = await session.execute(stmt)

        obj = res.scalar_one_or_none()

        if not obj:

            raise HTTPException(status_code=404, detail="Objection not found")



        obj.status = "DRAFT"

        obj.updated_at = now

        await session.commit()

        await session.refresh(obj)



    await realtime_dashboard_bus.publish_event(

        organization_id=org_id,

        event_type="objection_rolled_back",

        resource_type="objection",

        resource_id=str(obj.id),

        data={"id": obj.id, "status": "DRAFT"},

    )



    return {"status": "rolled_back", "id": obj.id, "new_status": "DRAFT"}





# ---------------------------------------------------------------------------

# Phase 5 Workstream E: Customer Identity & CRM Continuity (P2-08)

# ---------------------------------------------------------------------------



@router.get("/customers")

async def list_customers(

    page: int = 1,

    page_size: int = 50,

    search: Optional[str] = None,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Paginated listing of customer profiles scoped to the authenticated organization."""

    org_id = user.selected_organization_id

    offset = max(0, (page - 1) * page_size)



    async with db_client.async_session() as session:

        query = select(CustomerProfileModel).where(

            CustomerProfileModel.organization_id == org_id

        )

        if search:

            s = f"%{search.strip()}%"

            query = query.where(

                CustomerProfileModel.phone_number.ilike(s) | CustomerProfileModel.name.ilike(s)

            )

        query = query.order_by(CustomerProfileModel.last_seen_at.desc()).offset(offset).limit(page_size)

        res = await session.execute(query)

        rows = res.scalars().all()



        count_query = select(func.count(CustomerProfileModel.id)).where(

            CustomerProfileModel.organization_id == org_id

        )

        if search:

            s = f"%{search.strip()}%"

            count_query = count_query.where(

                CustomerProfileModel.phone_number.ilike(s) | CustomerProfileModel.name.ilike(s)

            )

        count_res = await session.execute(count_query)

        total = count_res.scalar_one() or 0



    return {

        "items": [

            {

                "id": r.id,

                "phone_number": r.phone_number,

                "name": r.name,

                "email": r.email,

                "channel": r.channel,

                "interaction_count": r.interaction_count,

                "last_lead_status": r.last_lead_status,

                "custom_attributes": r.custom_attributes,

                "external_identifiers": r.external_identifiers,

                "created_at": r.created_at.isoformat() if r.created_at else None,

                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,

            }

            for r in rows

        ],

        "total": total,

        "page": page,

        "page_size": page_size,

    }





@router.get("/customers/{phone}")

async def get_customer_profile(

    phone: str,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Retrieves full repeat-caller history, lead status timeline, and identifiers for a phone."""

    org_id = user.selected_organization_id

    norm_phone = normalize_phone_number(phone)



    async with db_client.async_session() as session:

        stmt = select(CustomerProfileModel).where(

            CustomerProfileModel.organization_id == org_id,

            CustomerProfileModel.phone_number == norm_phone,

        )

        res = await session.execute(stmt)

        profile = res.scalar_one_or_none()

        if not profile:

            raise HTTPException(status_code=404, detail="Customer profile not found in this organization")



        # Fetch call history runs

        ch_ids = (profile.custom_attributes or {}).get("call_history", [])

        call_runs = []

        if ch_ids:

            runs_stmt = select(WorkflowRunModel).where(

                WorkflowRunModel.id.in_(ch_ids),

                WorkflowRunModel.organization_id == org_id,

            ).order_by(WorkflowRunModel.created_at.desc())

            runs_res = await session.execute(runs_stmt)

            for r in runs_res.scalars().all():

                call_runs.append({

                    "run_id": r.id,

                    "name": r.name,

                    "call_type": r.call_type.value if hasattr(r.call_type, "value") else str(r.call_type),

                    "disposition": getattr(r, "call_disposition", None),

                    "created_at": r.created_at.isoformat() if r.created_at else None,

                    "lead_intelligence": (r.annotations or {}).get("lead_intelligence"),

                })



    return {

        "profile": {

            "id": profile.id,

            "organization_id": profile.organization_id,

            "phone_number": profile.phone_number,

            "name": profile.name,

            "email": profile.email,

            "interaction_count": profile.interaction_count,

            "last_lead_status": profile.last_lead_status,

            "custom_attributes": profile.custom_attributes,

            "external_identifiers": profile.external_identifiers,

            "created_at": profile.created_at.isoformat() if profile.created_at else None,

            "last_seen_at": profile.last_seen_at.isoformat() if profile.last_seen_at else None,

        },

        "call_history": call_runs,

    }





@router.patch("/customers/{phone}")

async def update_customer_profile(

    phone: str,

    body: UpdateCustomerProfileRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Updates customer notes, custom attributes, or external channel identifiers."""

    org_id = user.selected_organization_id

    norm_phone = normalize_phone_number(phone)



    async with db_client.async_session() as session:

        stmt = select(CustomerProfileModel).where(

            CustomerProfileModel.organization_id == org_id,

            CustomerProfileModel.phone_number == norm_phone,

        )

        res = await session.execute(stmt)

        profile = res.scalar_one_or_none()

        if not profile:

            raise HTTPException(status_code=404, detail="Customer profile not found in this organization")



        if body.name is not None:

            profile.name = body.name

        if body.email is not None:

            profile.email = body.email

        if body.custom_attributes is not None:

            attrs = copy.deepcopy(profile.custom_attributes or {})

            attrs.update(body.custom_attributes)

            profile.custom_attributes = attrs

        if body.notes:

            attrs = copy.deepcopy(profile.custom_attributes or {})

            notes = attrs.get("notes", [])

            notes.append(body.notes)

            attrs["notes"] = notes

            profile.custom_attributes = attrs

        if body.external_identifiers is not None:

            ext = copy.deepcopy(profile.external_identifiers or {})

            ext.update(body.external_identifiers)

            profile.external_identifiers = ext



        await session.commit()

        await session.refresh(profile)



    return {

        "status": "updated",

        "phone_number": profile.phone_number,

        "name": profile.name,

        "custom_attributes": profile.custom_attributes,

        "external_identifiers": profile.external_identifiers,

    }





# ---------------------------------------------------------------------------

# Phase 5 Workstream G: Minimal Chatwoot / WhatsApp Asynchronous Outbox

# ---------------------------------------------------------------------------



@router.get("/chatwoot/outbox")

async def list_chatwoot_outbox(

    limit: int = 50,

    offset: int = 0,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Lists asynchronous WhatsApp/Chatwoot outbox jobs for the organization."""

    jobs = await chatwoot_outbox_service.list_outbox_jobs(

        organization_id=user.selected_organization_id,

        limit=limit,

        offset=offset,

    )

    return {"jobs": jobs, "total": len(jobs)}





@router.post("/chatwoot/outbox")

async def enqueue_chatwoot_outbox(

    body: EnqueueChatwootJobRequest,

    user: UserModel = Depends(get_user),

) -> Dict[str, Any]:

    """Durably enqueues a non-blocking WhatsApp template/notification outbox job."""

    res = await chatwoot_outbox_service.enqueue_message(

        organization_id=user.selected_organization_id,

        recipient_phone=body.recipient_phone,

        message_type=body.message_type,

        payload=body.payload,

        workflow_run_id=body.workflow_run_id,

        idempotency_key=body.idempotency_key,

    )

    return res


# ---------------------------------------------------------------------------
# Meta WhatsApp Cloud API Integration Settings (Step 2B)
# ---------------------------------------------------------------------------

class ConfigureWhatsAppIntegrationRequest(BaseModel):
    waba_id: Optional[str] = None
    phone_number_id: str
    access_token: Optional[str] = None
    app_secret: Optional[str] = None
    verify_token: Optional[str] = None
    graph_api_version: Optional[str] = "v21.0"
    activate_as_provider: Optional[bool] = False


@router.get("/integrations/whatsapp")
async def get_whatsapp_integration_settings(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieves masked Meta WhatsApp Cloud API settings for the authenticated organization."""
    org_id = user.selected_organization_id
    cfg = await db_client.get_configuration(org_id, "whatsapp_meta_cloud")
    provider_cfg = await db_client.get_configuration(org_id, "messaging_provider")
    active_provider = "chatwoot"
    if provider_cfg and provider_cfg.value and isinstance(provider_cfg.value, dict):
        active_provider = provider_cfg.value.get("provider", "chatwoot")

    if not cfg or not cfg.value:
        return {
            "is_configured": False,
            "waba_id": None,
            "phone_number_id": None,
            "has_access_token": False,
            "access_token_masked": None,
            "graph_api_version": "v21.0",
            "has_app_secret": False,
            "app_secret_masked": None,
            "status": "Not configured",
            "active_provider": active_provider,
        }

    val = cfg.value
    raw_token = val.get("access_token")
    raw_secret = val.get("app_secret")

    decrypted_token = None
    if raw_token:
        try:
            from api.utils.crypto import decrypt_string
            decrypted_token = decrypt_string(raw_token)
        except Exception:
            decrypted_token = raw_token

    def _mask_secret(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        s_clean = str(s).strip()
        if len(s_clean) <= 8:
            return "••••••••"
        return f"{s_clean[:6]}...{s_clean[-4:]}"

    return {
        "is_configured": True,
        "waba_id": val.get("waba_id"),
        "phone_number_id": val.get("phone_number_id"),
        "has_access_token": bool(raw_token),
        "access_token_masked": _mask_secret(decrypted_token),
        "graph_api_version": val.get("graph_api_version", "v21.0"),
        "has_app_secret": bool(raw_secret),
        "app_secret_masked": "••••••••" if raw_secret else None,
        "status": "Saved — Live verification pending",
        "active_provider": active_provider,
    }


@router.post("/integrations/whatsapp")
async def save_whatsapp_integration_settings(
    body: ConfigureWhatsAppIntegrationRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Validates locally, encrypts sensitive tokens, and stores Meta WhatsApp settings."""
    org_id = user.selected_organization_id

    # 1. Local validation (No real Meta API call)
    phone_id = body.phone_number_id.strip()
    if not phone_id.isdigit():
        raise HTTPException(
            status_code=400,
            detail="phone_number_id must be a numeric string provided by Meta Business Manager",
        )

    from api.services.whatsapp.meta_adapter import meta_whatsapp_adapter
    from api.services.whatsapp.exceptions import MetaConfigurationError
    try:
        graph_version = meta_whatsapp_adapter.get_supported_graph_version(body.graph_api_version)
    except MetaConfigurationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Existing configuration to preserve unchanged tokens if not re-supplied
    existing_cfg = await db_client.get_configuration(org_id, "whatsapp_meta_cloud")
    existing_val = existing_cfg.value if (existing_cfg and existing_cfg.value) else {}

    # 3. Encrypt access token before persistence using existing crypto utility
    new_token = body.access_token.strip() if body.access_token else None
    if new_token:
        try:
            encrypted_token = meta_whatsapp_adapter.encrypt_access_token(new_token)
        except MetaConfigurationError as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        encrypted_token = existing_val.get("access_token")

    if not encrypted_token:
        raise HTTPException(status_code=400, detail="access_token is required for configuration")

    new_secret = body.app_secret.strip() if body.app_secret else None
    app_secret_val = new_secret or existing_val.get("app_secret")

    new_verify = body.verify_token.strip() if body.verify_token else None
    verify_token_val = new_verify or existing_val.get("verify_token")

    # 4. Save to OrganizationConfigurationModel
    config_payload = {
        "waba_id": body.waba_id.strip() if body.waba_id else existing_val.get("waba_id"),
        "phone_number_id": phone_id,
        "access_token": encrypted_token,
        "app_secret": app_secret_val,
        "verify_token": verify_token_val,
        "graph_api_version": graph_version,
        "is_enabled": False,  # Keep Meta provider disabled for real delivery
        "updated_at": datetime.now(UTC).isoformat(),
    }

    await db_client.upsert_configuration(
        organization_id=org_id,
        key="whatsapp_meta_cloud",
        value=config_payload,
    )

    # 5. Explicit provider selection (Do not silently change active provider unless requested)
    if body.activate_as_provider is True:
        await db_client.upsert_configuration(
            organization_id=org_id,
            key="messaging_provider",
            value={"provider": "meta_cloud"},
        )

    # Fetch active provider
    provider_cfg = await db_client.get_configuration(org_id, "messaging_provider")
    active_provider = "chatwoot"
    if provider_cfg and provider_cfg.value and isinstance(provider_cfg.value, dict):
        active_provider = provider_cfg.value.get("provider", "chatwoot")

    return {
        "status": "success",
        "message": "Meta WhatsApp Cloud API configuration saved successfully.",
        "configuration_status": "Saved — Live verification pending",
        "waba_id": config_payload["waba_id"],
        "phone_number_id": phone_id,
        "graph_api_version": graph_version,
        "active_provider": active_provider,
        "real_delivery_enabled": False,
    }


# ==============================================================================
# Phase 5 Step 5: Tenant WhatsApp Template Configuration Registry
# ==============================================================================

APPROVED_TRANSACTIONAL_CATEGORIES = [
    "appointment_confirmation",
    "appointment_reminder",
    "booking_details",
    "document_delivery",
    "ticket_itinerary",
    "payment_link",
]

DEFAULT_CORE_TEMPLATES = [
    {
        "template_name": "appointment_confirmation",
        "category": "appointment_confirmation",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Transactional appointment confirmation with date/time and service name.",
        "is_default": True,
    },
    {
        "template_name": "appointment_reminder",
        "category": "appointment_reminder",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Upcoming appointment reminder.",
        "is_default": True,
    },
    {
        "template_name": "booking_details",
        "category": "booking_details",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Booking confirmation and reference summary.",
        "is_default": True,
    },
    {
        "template_name": "document_delivery",
        "category": "document_delivery",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Secure HTTPS signed PDF/brochure document link delivery.",
        "is_default": True,
    },
    {
        "template_name": "ticket_itinerary",
        "category": "ticket_itinerary",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Ticket or travel itinerary details.",
        "is_default": True,
    },
    {
        "template_name": "payment_link",
        "category": "payment_link",
        "language_code": "en",
        "status": "Active (Standard Default)",
        "description": "Transactional payment link for confirmed services.",
        "is_default": True,
    },
]


class RegisterWhatsAppTemplateRequest(BaseModel):
    template_name: str
    category: str
    language_code: str = "en"


@router.get("/integrations/whatsapp/templates")
async def list_whatsapp_templates(
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Retrieves standard and tenant-registered custom WhatsApp templates."""
    org_id = user.selected_organization_id
    cfg = await db_client.get_configuration(org_id, "whatsapp_templates")
    custom_templates: List[Dict[str, Any]] = []
    if cfg and cfg.value and isinstance(cfg.value, list):
        custom_templates = cfg.value

    all_templates = list(DEFAULT_CORE_TEMPLATES) + custom_templates
    return {
        "templates": all_templates,
        "total_count": len(all_templates),
        "approved_categories": APPROVED_TRANSACTIONAL_CATEGORIES,
    }


@router.post("/integrations/whatsapp/templates")
async def register_whatsapp_template(
    body: RegisterWhatsAppTemplateRequest,
    user: UserModel = Depends(get_user),
) -> Dict[str, Any]:
    """Registers a business-owned WhatsApp template mapped to an approved transactional category."""
    import re
    org_id = user.selected_organization_id
    cat = body.category.strip().lower()
    if cat not in APPROVED_TRANSACTIONAL_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category '{body.category}'. Permitted categories: {APPROVED_TRANSACTIONAL_CATEGORIES}",
        )

    t_name = body.template_name.strip().lower()
    if not re.match(r"^[a-z0-9_]{1,512}$", t_name):
        raise HTTPException(
            status_code=400,
            detail="Template name must contain only lowercase alphanumeric characters and underscores (1-512 chars).",
        )

    cfg = await db_client.get_configuration(org_id, "whatsapp_templates")
    existing_list: List[Dict[str, Any]] = []
    if cfg and cfg.value and isinstance(cfg.value, list):
        existing_list = [t for t in cfg.value if t.get("template_name") != t_name]

    new_template = {
        "template_name": t_name,
        "category": cat,
        "language_code": (body.language_code or "en").strip(),
        "status": "Registered — Live Meta verification pending",
        "created_at": datetime.now(UTC).isoformat(),
        "is_default": False,
    }
    existing_list.append(new_template)

    await db_client.upsert_configuration(
        organization_id=org_id,
        key="whatsapp_templates",
        value=existing_list,
    )

    return {
        "status": "success",
        "message": f"Template '{t_name}' registered successfully with status 'Registered — Live Meta verification pending'.",
        "template": new_template,
        "templates": list(DEFAULT_CORE_TEMPLATES) + existing_list,
    }
