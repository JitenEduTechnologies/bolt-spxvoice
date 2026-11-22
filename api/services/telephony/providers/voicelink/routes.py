"""VoiceLink telephony routes (webhooks, status callbacks, DTMF).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via provider registry.
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger
from starlette.responses import HTMLResponse, JSONResponse

from api.db import db_client
from api.services.telephony.factory import get_telephony_provider_for_run
from api.services.telephony.smart_did import smart_did_controller
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.run_context import set_current_run_id

router = APIRouter()


@router.post("/voicelink/status-callback/{workflow_run_id}")
async def handle_voicelink_status_callback(
    workflow_run_id: int,
    request: Request,
    x_voicelink_signature: Optional[str] = Header(None, alias="X-VoiceLink-Signature"),
    x_voicelink_token: Optional[str] = Header(None, alias="X-VoiceLink-Token"),
) -> JSONResponse:
    """Handle VoiceLink status callbacks (ringing, answered, completed, failed, busy)."""
    set_current_run_id(workflow_run_id)

    # Parse incoming data (supports JSON or form-encoded POST)
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            callback_data: Dict[str, Any] = await request.json()
        except Exception:
            callback_data = {}
    else:
        form_data = await request.form()
        callback_data = dict(form_data)

    logger.info(
        f"[run {workflow_run_id}] VoiceLink status callback: {json.dumps(callback_data)}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow_run.workflow.organization_id
    )

    # Webhook signature / token verification (fail closed)
    signature = x_voicelink_signature or x_voicelink_token or str(callback_data.get("token") or "")
    if not signature:
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink webhook rejected: missing signature or token"
        )
        raise HTTPException(status_code=401, detail="Missing required webhook authentication")

    if not getattr(provider, "webhook_secret", None):
        logger.error(
            f"[run {workflow_run_id}] VoiceLink webhook rejected: webhook_secret not configured (fail closed)"
        )
        raise HTTPException(status_code=401, detail="Webhook signing secret not configured")

    if not await provider.verify_webhook_signature(
        str(request.url), callback_data, signature
    ):
        logger.warning(
            f"[run {workflow_run_id}] VoiceLink webhook signature verification failed"
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Standardize event
    parsed = provider.parse_status_callback(callback_data)

    status_req = StatusCallbackRequest(
        call_id=parsed["call_id"],
        status=parsed["status"],
        duration=parsed.get("duration"),
        from_number=parsed.get("from_number"),
        to_number=parsed.get("to_number"),
    )
    await _process_status_update(workflow_run_id, status_req)

    return JSONResponse(content={"status": "ok", "run_id": workflow_run_id})


from api.services.telephony.ivr_service import (
    BusinessIVRConfig,
    generate_inbound_ivr_xml,
    handle_dtmf_input_xml,
)


@router.post("/voicelink/inbound")
async def handle_voicelink_inbound(request: Request) -> HTMLResponse:
    """Handle VoiceLink inbound call forwarding webhook with optional IVR."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data: Dict[str, Any] = await request.json()
        except Exception:
            data = {}
    else:
        form_data = await request.form()
        data = dict(form_data)

    caller = str(data.get("from") or data.get("Caller") or "")
    called = str(data.get("to") or data.get("Called") or "")
    call_sid = str(data.get("CallSid") or data.get("call_id") or "vl-inbound")
    logger.info(f"[VoiceLink Inbound] Incoming call from {caller} to {called} (CallSid: {call_sid})")

    # Fetch default or primary workflow IVR configuration and create preliminary run
    ivr_config = BusinessIVRConfig()
    preliminary_run_id = 0
    try:
        from api.db import db_client
        from api.db.models import WorkflowModel
        from api.enums import CallType
        from sqlalchemy.future import select

        async with db_client.async_session() as session:
            stmt = select(WorkflowModel).order_by(WorkflowModel.id.asc()).limit(1)
            res = await session.execute(stmt)
            wf = res.scalar_one_or_none()
            if wf:
                if wf.workflow_configurations:
                    raw_ivr = wf.workflow_configurations.get("ivr")
                    if raw_ivr:
                        ivr_config = BusinessIVRConfig.model_validate(raw_ivr)

                # Create preliminary run immediately so dropped calls before SIP answer are captured
                suffix = caller[-4:] if len(caller) >= 4 else "IN"
                run_name = f"INBOUND-{suffix}-{call_sid[:8]}"
                prelim_run = await db_client.create_workflow_run(
                    name=run_name,
                    workflow_id=wf.id,
                    mode="voicelink",
                    user_id=wf.user_id,
                    call_type=CallType.INBOUND,
                    call_disposition="MISSED",  # Preliminary disposition until answered
                    initial_context={
                        "caller_number": caller,
                        "called_number": called,
                        "call_id": call_sid,
                        "direction": "inbound",
                        "provider": "voicelink",
                    },
                    gathered_context={
                        "call_id": call_sid,
                        "caller_number": caller,
                        "called_number": called,
                    },
                    organization_id=wf.organization_id,
                )
                preliminary_run_id = prelim_run.id
                logger.info(
                    f"[VoiceLink Inbound] Created preliminary inbound run {preliminary_run_id} for call {call_sid}"
                )
    except Exception as e:
        logger.warning(f"[VoiceLink Inbound] Could not load workflow or create prelim run: {e}. Using defaults.")

    base_url = str(request.base_url).rstrip("/")
    action_url = f"{base_url}/api/v1/telephony/voicelink/dtmf/{preliminary_run_id}"
    sip_uri = "sip:livekit-sip@sip.spxvoice.internal"

    xml = generate_inbound_ivr_xml(
        config=ivr_config,
        action_url=action_url,
        sip_inbound_uri=sip_uri,
        session_id=call_sid,
    )
    return HTMLResponse(content=xml, media_type="application/xml")


@router.post("/voicelink/dtmf/{workflow_run_id}")
async def handle_voicelink_dtmf(
    workflow_run_id: int, request: Request
) -> Any:
    """Handle RFC 4733 DTMF input callbacks from VoiceLink."""
    set_current_run_id(workflow_run_id)
    raw_content_type = ""
    try:
        header_val = request.headers.get("content-type")
        raw_content_type = str(header_val) if isinstance(header_val, str) else ""
    except Exception:
        raw_content_type = ""

    form_data = {}
    if "application/json" in raw_content_type:
        try:
            form_data = await request.json()
        except Exception:
            form_data = {}
    else:
        try:
            raw_form = await request.form()
            form_data = dict(raw_form)
        except Exception:
            form_data = {}

    digits = str(form_data.get("digits") or form_data.get("Digits") or "1")
    logger.info(f"[run {workflow_run_id}] VoiceLink captured DTMF digits: {digits}")

    # Fetch IVR config
    ivr_config = BusinessIVRConfig()
    try:
        from api.db import db_client
        from api.db.models import WorkflowModel
        from sqlalchemy.future import select

        async with db_client.async_session() as session:
            stmt = select(WorkflowModel).order_by(WorkflowModel.id.asc()).limit(1)
            res = await session.execute(stmt)
            wf = res.scalar_one_or_none()
            if wf and wf.workflow_configurations:
                raw_ivr = wf.workflow_configurations.get("ivr")
                if raw_ivr:
                    ivr_config = BusinessIVRConfig.model_validate(raw_ivr)
    except Exception as e:
        logger.warning(f"[VoiceLink DTMF] Could not load IVR settings: {e}")

    sip_uri = "sip:livekit-sip@sip.spxvoice.internal"
    xml = handle_dtmf_input_xml(
        digits=digits,
        config=ivr_config,
        sip_inbound_uri=sip_uri,
    )
    return JSONResponse(
        content={"status": "ok", "digits": digits, "xml": xml},
        headers={"X-IVR-Action-XML": xml.replace("\n", " ")},
    )

