"""Vobiz telephony routes (webhooks, status callbacks, answer URLs).

Mounted under ``/api/v1/telephony`` by ``api.routes.telephony`` via the
provider registry — see ProviderSpec.router.
"""

import json
from datetime import UTC, datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger
from starlette.responses import HTMLResponse

from api.db import db_client
from api.services.telephony.factory import (
    get_telephony_provider_for_run,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.utils.common import get_backend_endpoints
from api.utils.run_context import set_current_run_id
from api.utils.telephony_helper import (
    parse_webhook_request,
)

router = APIRouter()


def _extract_vobiz_auth_headers(
    request: Request,
    explicit_sig: Optional[str] = None,
    explicit_ts: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract Vobiz signature and nonce/timestamp headers from request.
    Returns (sig_v3, nonce_v3, sig_legacy, ts_legacy).
    """
    headers = {k.lower(): v for k, v in request.headers.items()}
    sig_v3 = headers.get("x-vobiz-signature-v3")
    nonce_v3 = headers.get("x-vobiz-signature-v3-nonce")
    sig_legacy = headers.get("x-vobiz-signature") or explicit_sig
    ts_legacy = headers.get("x-vobiz-timestamp") or explicit_ts
    return sig_v3, nonce_v3, sig_legacy, ts_legacy


def _build_candidate_callback_urls(
    request: Request,
    backend_endpoint: str,
    path_suffix: str,
) -> tuple[str, list[str]]:
    configured_url = f"{backend_endpoint}{path_suffix}"
    headers = {k.lower(): v for k, v in request.headers.items()}
    proto = headers.get("x-forwarded-proto") or request.url.scheme
    host = headers.get("x-forwarded-host") or headers.get("host") or request.url.netloc
    path = request.url.path
    proxy_url = f"{proto}://{host}{path}"
    direct_url = str(request.url).split("?")[0]
    candidate_urls = list({configured_url, proxy_url, direct_url})
    return configured_url, candidate_urls


@router.post("/vobiz-xml", include_in_schema=False)
async def handle_vobiz_xml_webhook(
    workflow_id: int, user_id: int, workflow_run_id: int, organization_id: int
):
    """
    Handle initial webhook from Vobiz when call is answered.
    Returns Vobiz XML response with Stream element.

    Vobiz uses Plivo-compatible XML format similar to Twilio's TwiML.
    """
    set_current_run_id(workflow_run_id)
    logger.info(
        f"[run {workflow_run_id}] Vobiz XML webhook called - "
        f"workflow_id={workflow_id}, user_id={user_id}, org_id={organization_id}"
    )

    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    provider = await get_telephony_provider_for_run(workflow_run, organization_id)

    logger.debug(f"[run {workflow_run_id}] Using provider: {provider.PROVIDER_NAME}")

    response_content = await provider.get_webhook_response(
        workflow_id, user_id, workflow_run_id
    )

    logger.debug(
        f"[run {workflow_run_id}] Vobiz XML response generated:\n{response_content}"
    )

    return HTMLResponse(content=response_content, media_type="application/xml")


@router.post("/vobiz/hangup-callback/{workflow_run_id}")
async def handle_vobiz_hangup_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback (sent when call ends).

    Vobiz sends callbacks to hangup_url when the call terminates.
    This includes call duration, status, and billing information.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data (Vobiz sends form data or JSON)
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz hangup callback - Body: {json.dumps(callback_data)}"
    )
    logger.info(
        f"[run {workflow_run_id}] Received Vobiz hangup callback {json.dumps(callback_data)}"
    )

    # Extract Vobiz authentication headers (V3 or legacy fallback)
    sig_v3, nonce_v3, sig_legacy, ts_legacy = _extract_vobiz_auth_headers(
        request, x_vobiz_signature, x_vobiz_timestamp
    )
    has_v3 = bool(sig_v3 and nonce_v3)
    has_legacy = bool(sig_legacy and ts_legacy)

    # Require mandatory webhook authentication headers (fail closed)
    if not (has_v3 or has_legacy):
        logger.warning(
            f"[run {workflow_run_id}] Vobiz hangup callback rejected: missing signature or nonce/timestamp header"
        )
        raise HTTPException(
            status_code=401, detail="Missing required webhook authentication headers"
        )

    # We need the workflow run to get organization for provider credentials
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for signature verification"
        )
        raise HTTPException(status_code=404, detail="Workflow run not found")

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(
            f"[run {workflow_run_id}] Workflow not found for signature verification"
        )
        raise HTTPException(status_code=404, detail="Workflow not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    if not getattr(provider, "auth_token", None):
        logger.error(
            f"[run {workflow_run_id}] Vobiz auth_token not configured on provider (fail closed)"
        )
        raise HTTPException(status_code=401, detail="Webhook signing secret not configured")

    # Get raw body for legacy signature verification
    raw_body = await request.body()
    webhook_body = raw_body.decode("utf-8")

    # Build candidate URLs for reverse-proxy / host safety
    backend_endpoint, _ = await get_backend_endpoints()
    configured_url, candidate_urls = _build_candidate_callback_urls(
        request,
        backend_endpoint,
        f"/api/v1/telephony/vobiz/hangup-callback/{workflow_run_id}",
    )

    # Verify signature
    if has_v3:
        is_valid = await provider.verify_webhook_signature(
            configured_url,
            callback_data,
            signature=sig_v3,
            nonce=nonce_v3,
            headers=dict(request.headers),
            candidate_urls=candidate_urls,
        )
    else:
        is_valid = await provider.verify_webhook_signature(
            configured_url,
            callback_data,
            signature=sig_legacy,
            timestamp=ts_legacy,
            body=webhook_body,
            headers=dict(request.headers),
            candidate_urls=candidate_urls,
        )

    if not is_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid Vobiz hangup callback signature or replayed/expired token"
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    logger.info(f"[run {workflow_run_id}] Vobiz hangup callback signature verified")

    logger.debug(
        f"[run {workflow_run_id}] Processing Vobiz hangup with provider: {provider.PROVIDER_NAME}"
    )

    # Parse the callback data into generic format
    parsed_data = provider.parse_status_callback(callback_data)

    logger.debug(
        f"[run {workflow_run_id}] Parsed Vobiz callback data: {json.dumps(parsed_data)}"
    )

    # Create StatusCallbackRequest from parsed data
    status_update = StatusCallbackRequest(
        call_id=parsed_data["call_id"],
        status=parsed_data["status"],
        from_number=parsed_data.get("from_number"),
        to_number=parsed_data.get("to_number"),
        direction=parsed_data.get("direction"),
        duration=parsed_data.get("duration"),
        extra=parsed_data.get("extra", {}),
    )

    # Process the status update
    await _process_status_update(workflow_run_id, status_update)

    logger.info(f"[run {workflow_run_id}] Vobiz hangup callback processed successfully")

    return {"status": "success"}


@router.post("/vobiz/ring-callback/{workflow_run_id}")
async def handle_vobiz_ring_callback(
    workflow_run_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz ring callback (sent when call starts ringing).

    Vobiz can send callbacks to ring_url when the call starts ringing.
    This is optional and used for tracking ringing status.
    """
    set_current_run_id(workflow_run_id)

    # Logging all headers and body to understand what Vobiz actually sends
    all_headers = dict(request.headers)
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Headers: {json.dumps(all_headers)}"
    )

    # Parse the callback data
    form_data = await request.form()
    callback_data = dict(form_data)

    # TODO: Remove this debug logging after Vobiz team clarifies webhook authentication
    logger.info(
        f"[run {workflow_run_id}] Vobiz ring callback - Body: {json.dumps(callback_data)}"
    )

    logger.info(
        f"[run {workflow_run_id}] Received Vobiz ring callback {json.dumps(callback_data)}"
    )

    # Extract Vobiz authentication headers (V3 or legacy fallback)
    sig_v3, nonce_v3, sig_legacy, ts_legacy = _extract_vobiz_auth_headers(
        request, x_vobiz_signature, x_vobiz_timestamp
    )
    has_v3 = bool(sig_v3 and nonce_v3)
    has_legacy = bool(sig_legacy and ts_legacy)

    # Require mandatory webhook authentication headers (fail closed)
    if not (has_v3 or has_legacy):
        logger.warning(
            f"[run {workflow_run_id}] Vobiz ring callback rejected: missing signature or nonce/timestamp header"
        )
        raise HTTPException(
            status_code=401, detail="Missing required webhook authentication headers"
        )

    # We need the workflow run to get organization for provider credentials
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            f"[run {workflow_run_id}] Workflow run not found for signature verification"
        )
        raise HTTPException(status_code=404, detail="Workflow run not found")

    workflow = await db_client.get_workflow_by_id(workflow_run.workflow_id)
    if not workflow:
        logger.warning(
            f"[run {workflow_run_id}] Workflow not found for signature verification"
        )
        raise HTTPException(status_code=404, detail="Workflow not found")

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    if not getattr(provider, "auth_token", None):
        logger.error(
            f"[run {workflow_run_id}] Vobiz auth_token not configured on provider (fail closed)"
        )
        raise HTTPException(status_code=401, detail="Webhook signing secret not configured")

    # Get raw body for signature verification
    raw_body = await request.body()
    webhook_body = raw_body.decode("utf-8")

    # Build candidate URLs for reverse-proxy / host safety
    backend_endpoint, _ = await get_backend_endpoints()
    configured_url, candidate_urls = _build_candidate_callback_urls(
        request,
        backend_endpoint,
        f"/api/v1/telephony/vobiz/ring-callback/{workflow_run_id}",
    )

    # Verify signature
    if has_v3:
        is_valid = await provider.verify_webhook_signature(
            configured_url,
            callback_data,
            signature=sig_v3,
            nonce=nonce_v3,
            headers=dict(request.headers),
            candidate_urls=candidate_urls,
        )
    else:
        is_valid = await provider.verify_webhook_signature(
            configured_url,
            callback_data,
            signature=sig_legacy,
            timestamp=ts_legacy,
            body=webhook_body,
            headers=dict(request.headers),
            candidate_urls=candidate_urls,
        )

    if not is_valid:
        logger.warning(
            f"[run {workflow_run_id}] Invalid Vobiz ring callback signature or replayed/expired token"
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    logger.info(f"[run {workflow_run_id}] Vobiz ring callback signature verified")

    # Log the ringing event
    telephony_callback_logs = workflow_run.logs.get("telephony_status_callbacks", [])
    ring_log = {
        "status": "ringing",
        "timestamp": datetime.now(UTC).isoformat(),
        "call_id": callback_data.get("call_uuid", callback_data.get("CallUUID", "")),
        "event_type": "ring",
        "raw_data": callback_data,
    }
    telephony_callback_logs.append(ring_log)

    # Update workflow run logs
    await db_client.update_workflow_run(
        run_id=workflow_run_id,
        logs={"telephony_status_callbacks": telephony_callback_logs},
    )

    logger.info(f"[run {workflow_run_id}] Vobiz ring callback logged")

    return {"status": "success"}


@router.post("/vobiz/hangup-callback/workflow/{workflow_id}")
async def handle_vobiz_hangup_callback_by_workflow(
    workflow_id: int,
    request: Request,
    x_vobiz_signature: Optional[str] = Header(None),
    x_vobiz_timestamp: Optional[str] = Header(None),
):
    """Handle Vobiz hangup callback with workflow_id - finds workflow run by call_id."""

    all_headers = dict(request.headers)
    logger.info(
        f"[workflow {workflow_id}] Vobiz hangup callback - Headers: {json.dumps(all_headers)}"
    )

    try:
        callback_data, _ = await parse_webhook_request(request)
    except ValueError:
        callback_data = {}

    call_uuid = callback_data.get("CallUUID") or callback_data.get("call_uuid")
    logger.info(
        f"[workflow {workflow_id}] Received Vobiz hangup callback for call {call_uuid}: {json.dumps(callback_data)}"
    )

    if not call_uuid:
        logger.warning(
            f"[workflow {workflow_id}] No call_uuid found in Vobiz hangup callback"
        )
        return {"status": "error", "message": "No call_uuid found"}

    workflow = await db_client.get_workflow_by_id(workflow_id)
    if not workflow:
        logger.warning(f"[workflow {workflow_id}] Workflow not found")
        return {"status": "error", "message": "workflow_not_found"}

    try:
        workflow_run = await db_client.get_workflow_run_by_call_id(call_uuid)
    except Exception as e:
        logger.error(
            f"[workflow {workflow_id}] Error finding workflow run for call {call_uuid}: {e}"
        )
        return {"status": "error", "message": str(e)}

    if not workflow_run or workflow_run.workflow_id != workflow_id:
        logger.warning(
            f"[workflow {workflow_id}] No workflow run found for call {call_uuid}"
        )
        return {"status": "ignored", "reason": "workflow_run_not_found"}

    workflow_run_id = workflow_run.id
    set_current_run_id(workflow_run_id)
    logger.info(
        f"[workflow {workflow_id}] Found workflow run {workflow_run_id} for call {call_uuid}"
    )

    provider = await get_telephony_provider_for_run(
        workflow_run, workflow.organization_id
    )

    sig_v3, nonce_v3, sig_legacy, ts_legacy = _extract_vobiz_auth_headers(
        request, x_vobiz_signature, x_vobiz_timestamp
    )
    has_v3 = bool(sig_v3 and nonce_v3)
    has_legacy = bool(sig_legacy and ts_legacy)

    if has_v3 or has_legacy:
        raw_body = await request.body()
        webhook_body = raw_body.decode("utf-8")
        backend_endpoint, _ = await get_backend_endpoints()
        configured_url, candidate_urls = _build_candidate_callback_urls(
            request,
            backend_endpoint,
            f"/api/v1/telephony/vobiz/hangup-callback/workflow/{workflow_id}",
        )

        if has_v3:
            is_valid = await provider.verify_webhook_signature(
                configured_url,
                callback_data,
                signature=sig_v3,
                nonce=nonce_v3,
                headers=dict(request.headers),
                candidate_urls=candidate_urls,
            )
        else:
            is_valid = await provider.verify_webhook_signature(
                configured_url,
                callback_data,
                signature=sig_legacy,
                timestamp=ts_legacy,
                body=webhook_body,
                headers=dict(request.headers),
                candidate_urls=candidate_urls,
            )

        if not is_valid:
            logger.warning(
                f"[workflow {workflow_id}] Invalid Vobiz hangup callback signature or replayed token"
            )
            return {"status": "error", "message": "invalid_signature"}

        logger.info(
            f"[workflow {workflow_id}] Vobiz hangup callback signature verified"
        )

    try:
        parsed_data = provider.parse_status_callback(callback_data)

        status = StatusCallbackRequest(
            call_id=parsed_data["call_id"],
            status=parsed_data["status"],
            from_number=parsed_data.get("from_number"),
            to_number=parsed_data.get("to_number"),
            direction=parsed_data.get("direction"),
            duration=parsed_data.get("duration"),
            extra=parsed_data.get("extra", {}),
        )

        await _process_status_update(workflow_run_id, status)

        logger.info(
            f"[run {workflow_run_id}] Vobiz hangup callback processed successfully"
        )
        return {"status": "success"}

    except Exception as e:
        logger.error(
            f"[run {workflow_run_id}] Error processing Vobiz hangup callback: {e}"
        )
        return {"status": "error", "message": str(e)}
