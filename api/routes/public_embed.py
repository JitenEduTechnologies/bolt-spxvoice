"""Public API endpoints for workflow embedding.

These endpoints are accessible without authentication but require valid embed tokens.
They handle CORS, domain validation, and session management for embedded workflows.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
)
from loguru import logger
from pydantic import BaseModel

from sqlalchemy import select, func
from api.db import db_client
from api.enums import WorkflowRunMode
from api.routes.turn_credentials import (
    TURN_SECRET,
    TurnCredentialsResponse,
    generate_turn_credentials,
)

router = APIRouter(prefix="/public/embed")


class InitEmbedRequest(BaseModel):
    """Request model for initializing an embed session"""

    token: str
    context_variables: Optional[dict] = None


class InitEmbedResponse(BaseModel):
    """Response model for embed initialization"""

    session_token: str
    workflow_run_id: int
    config: dict


class EmbedConfigResponse(BaseModel):
    """Response model for embed configuration"""

    workflow_id: int
    settings: dict
    theme: str
    position: str
    button_text: str
    button_color: str
    size: str
    auto_start: bool


def validate_origin(origin: str, allowed_domains: list) -> bool:
    """Validate if the origin is in the allowed domains list.

    Args:
        origin: The origin header from the request
        allowed_domains: List of allowed domain patterns

    Returns:
        True if origin is allowed, False otherwise
    """
    if not allowed_domains:
        # If no domains specified, allow all origins
        return True

    # Extract domain from origin (remove protocol)
    if "://" in origin:
        domain = origin.split("://")[1].split("/")[0].split(":")[0]
    else:
        domain = origin

    # Normalize domain for www matching
    def normalize_www(d: str) -> tuple[str, str]:
        """Return both www and non-www versions of a domain"""
        if d.startswith("www."):
            return (d, d[4:])  # (www.x.com, x.com)
        else:
            return (d, f"www.{d}")  # (x.com, www.x.com)

    domain_variants = normalize_www(domain)

    for allowed in allowed_domains:
        if allowed == "*":
            return True
        elif allowed.startswith("*."):
            # Wildcard subdomain matching
            base_domain = allowed[2:]
            if domain == base_domain or domain.endswith("." + base_domain):
                return True
        else:
            # Check both www and non-www versions
            allowed_variants = normalize_www(allowed)
            # If any variant of domain matches any variant of allowed, it's valid
            if any(
                dv in allowed_variants or av in domain_variants
                for dv in domain_variants
                for av in allowed_variants
            ):
                return True

    return False


def generate_session_token() -> str:
    """Generate a cryptographically secure session token"""
    return f"emb_session_{secrets.token_urlsafe(32)}"


def get_request_origin(request: Request) -> str:
    """Extract origin from request headers, falling back to referer if not present."""
    origin = request.headers.get("origin", "")
    if not origin:
        origin = request.headers.get("referer", "")
    return origin


@router.post("/init", response_model=InitEmbedResponse)
async def initialize_embed_session(request: Request, init_request: InitEmbedRequest):
    """Initialize an embed session with token validation and domain checking.

    This endpoint:
    1. Validates the embed token
    2. Checks domain whitelist
    3. Creates a workflow run
    4. Generates a temporary session token
    5. Returns configuration for the widget
    """
    origin = get_request_origin(request)

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(init_request.token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Check expiration
    if embed_token.expires_at and embed_token.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Embed token has expired")

    # Check usage limit
    if embed_token.usage_limit and embed_token.usage_count >= embed_token.usage_limit:
        raise HTTPException(status_code=403, detail="Embed token usage limit exceeded")

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Concurrency admission control
    from sqlalchemy import func
    from api.db.models import EmbedSessionModel
    token_settings = embed_token.settings or {}
    max_concurrent = int(token_settings.get("max_concurrent_sessions", 25))

    async with db_client.async_session() as session:
        active_stmt = select(func.count(EmbedSessionModel.id)).where(
            EmbedSessionModel.embed_token_id == embed_token.id,
            EmbedSessionModel.expires_at > datetime.now(UTC),
        )
        active_res = await session.execute(active_stmt)
        active_count = active_res.scalar() or 0
        if active_count >= max_concurrent:
            logger.warning(f"[Embed Admission] Token {embed_token.id} concurrency limit ({active_count}/{max_concurrent}) reached")
            raise HTTPException(
                status_code=429,
                detail=f"Concurrent session limit ({max_concurrent}) reached. Please try again shortly.",
            )

        # Client IP flood protection (max 30 requests/min per IP)
        client_ip = request.client.host if request.client else None
        if client_ip and client_ip not in ("127.0.0.1", "localhost", "testclient"):
            one_min_ago = datetime.now(UTC) - timedelta(minutes=1)
            ip_stmt = select(func.count(EmbedSessionModel.id)).where(
                EmbedSessionModel.client_ip == client_ip,
                EmbedSessionModel.created_at >= one_min_ago,
            )
            ip_res = await session.execute(ip_stmt)
            ip_count = ip_res.scalar() or 0
            if ip_count >= 30:
                logger.warning(f"[Embed RateLimit] Client IP {client_ip} exceeded rate limit ({ip_count}/30 in 1m)")
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Please wait before creating new voice sessions.",
                )

    # Generate session token
    session_token = generate_session_token()
    now_iso = datetime.now(UTC).isoformat()

    ctx = init_request.context_variables or {}
    traffic_attribution = {
        "origin": origin,
        "landing_page": ctx.get("landing_page"),
        "referrer": ctx.get("referrer") or request.headers.get("referer"),
        "utm_source": ctx.get("utm_source"),
        "utm_medium": ctx.get("utm_medium"),
        "utm_campaign": ctx.get("utm_campaign"),
        "utm_term": ctx.get("utm_term"),
        "utm_content": ctx.get("utm_content"),
    }
    session_identifiers = {
        "business_id": embed_token.organization_id,
        "workflow_id": embed_token.workflow_id,
        "session_id": session_token,
        "token_id": embed_token.id,
    }
    consent_status = {
        "consent": bool(ctx.get("consent", True)),
        "captured_at": ctx.get("consent_timestamp") or now_iso,
    }

    initial_context = {
        **ctx,
        "provider": WorkflowRunMode.SMALLWEBRTC.value,
        "source": "website_widget",
        "traffic_attribution": traffic_attribution,
        "session_identifiers": session_identifiers,
        "consent_status": consent_status,
    }

    # Create workflow run
    try:
        workflow_run = await db_client.create_workflow_run(
            name=f"Embed Run - {now_iso}",
            workflow_id=embed_token.workflow_id,
            mode=WorkflowRunMode.SMALLWEBRTC.value,
            user_id=embed_token.created_by,  # Use token creator as run owner
            initial_context=initial_context,
            organization_id=embed_token.organization_id,
        )
    except Exception as e:
        logger.error(f"Failed to create workflow run: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workflow run")

    # Create embed session
    try:
        await db_client.create_embed_session(
            session_token=session_token,
            embed_token_id=embed_token.id,
            workflow_run_id=workflow_run.id,
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent", "")[:500],
            origin=origin[:255],
            expires_at=datetime.now(UTC) + timedelta(hours=1),  # 1 hour expiry
        )
    except Exception as e:
        logger.error(f"Failed to create embed session: {e}")
        raise HTTPException(status_code=500, detail="Failed to create session")

    # Annotate workflow run with initial metadata
    try:
        await db_client.update_workflow_run(
            run_id=workflow_run.id,
            annotations={
                "traffic_attribution": traffic_attribution,
                "session_identifiers": session_identifiers,
                "consent_status": consent_status,
                "embed_events": [
                    {
                        "event": "session_started",
                        "timestamp": now_iso,
                        "origin": origin,
                    }
                ],
            },
        )
    except Exception as e:
        logger.warning(f"Failed to record initial embed annotations: {e}")

    # Increment usage count
    await db_client.increment_embed_token_usage(embed_token.id)

    # Prepare configuration
    config = {
        "workflow_id": embed_token.workflow_id,
        "workflow_run_id": workflow_run.id,
        **(embed_token.settings or {}),
    }

    return InitEmbedResponse(
        session_token=session_token, workflow_run_id=workflow_run.id, config=config
    )


class EmbedSessionEventRequest(BaseModel):
    """Event reporting request model for widget sessions"""

    session_token: str
    event_name: str  # widget_opened, session_started, session_completed, error
    metadata: Optional[dict] = None
    contact_info: Optional[dict] = None
    consent: Optional[bool] = None


@router.post("/session-event")
async def record_embed_session_event(request: Request, body: EmbedSessionEventRequest):
    """Record lifecycle events and voluntary visitor contact info for an embed session."""
    origin = get_request_origin(request)

    embed_session = await db_client.get_embed_session_by_token(body.session_token)
    if not embed_session:
        raise HTTPException(status_code=404, detail="Invalid session token")

    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    if not validate_origin(origin, embed_token.allowed_domains or []):
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    now_iso = datetime.now(UTC).isoformat()
    run = await db_client.get_workflow_run(embed_session.workflow_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Associated workflow run not found")

    ann = dict(run.annotations or {})
    events = list(ann.get("embed_events") or [])
    events.append({
        "event": body.event_name,
        "timestamp": now_iso,
        "metadata": body.metadata or {},
    })
    ann["embed_events"] = events

    # If contact info voluntarily supplied with consent, capture CRM lead info
    if body.contact_info and (body.consent is not False):
        contact = body.contact_info
        ann["captured_lead"] = {
            "name": contact.get("name"),
            "phone": contact.get("phone"),
            "email": contact.get("email"),
            "enquiry": contact.get("enquiry"),
            "consent": True,
            "captured_at": now_iso,
            "source": "website_widget",
        }

    await db_client.update_workflow_run(
        run_id=run.id,
        annotations=ann,
    )

    return {"status": "ok", "event": body.event_name, "workflow_run_id": run.id}


@router.options("/session-event")
async def options_session_event(request: Request):
    """Handle CORS preflight for session-event endpoint"""
    origin = request.headers.get("origin", "*")
    return Response(
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Origin",
            "Access-Control-Max-Age": "86400",
        }
    )


@router.get("/config/{token}", response_model=EmbedConfigResponse)
async def get_embed_config(token: str, request: Request):
    """Get embed configuration without creating a session.

    This endpoint is used to fetch widget configuration for display purposes
    without actually starting a call session.
    """
    origin = get_request_origin(request)

    # Validate embed token
    embed_token = await db_client.get_embed_token_by_token(token)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Check if token is active
    if not embed_token.is_active:
        raise HTTPException(status_code=403, detail="Embed token is inactive")

    # Validate domain
    if not validate_origin(origin, embed_token.allowed_domains or []):
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Extract settings with defaults
    settings = embed_token.settings or {}

    return EmbedConfigResponse(
        workflow_id=embed_token.workflow_id,
        settings=settings,
        theme=settings.get("theme", "light"),
        position=settings.get("position", "bottom-right"),
        button_text=settings.get("buttonText", "Start Voice Call"),
        button_color=settings.get("buttonColor", "#3B82F6"),
        size=settings.get("size", "medium"),
        auto_start=settings.get("autoStart", False),
    )


@router.options("/init")
async def options_init(request: Request):
    """Handle CORS preflight for init endpoint"""
    # For init endpoint, we need to check the token in the request body
    # But OPTIONS requests don't have body, so we'll be permissive
    # The actual validation happens in the POST request
    origin = request.headers.get("origin", "*")

    return Response(
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Origin",
            "Access-Control-Max-Age": "86400",
        }
    )


@router.get("/turn-credentials/{session_token}", response_model=TurnCredentialsResponse)
async def get_public_turn_credentials(session_token: str, request: Request):
    """Get TURN credentials for an embed session.

    This endpoint allows embedded widgets to obtain TURN server credentials
    for WebRTC connections without requiring authentication.

    Args:
        session_token: The session token from embed initialization

    Returns:
        TurnCredentialsResponse with username, password, ttl, and TURN URIs
    """
    origin = get_request_origin(request)

    # Validate session token
    embed_session = await db_client.get_embed_session_by_token(session_token)
    if not embed_session:
        raise HTTPException(status_code=404, detail="Invalid session token")

    # Check if session is expired
    if embed_session.expires_at and embed_session.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=403, detail="Session expired")

    # Get the embed token to check allowed domains
    embed_token = await db_client.get_embed_token_by_id(embed_session.embed_token_id)
    if not embed_token:
        raise HTTPException(status_code=404, detail="Invalid embed token")

    # Validate domain (empty allowed_domains means allow all)
    if not validate_origin(origin, embed_token.allowed_domains or []):
        logger.warning(
            f"Domain validation failed for TURN credentials: {origin} not in {embed_token.allowed_domains}"
        )
        raise HTTPException(status_code=403, detail=f"Domain not allowed: {origin}")

    # Check if TURN is configured
    if not TURN_SECRET:
        raise HTTPException(
            status_code=503,
            detail="TURN server not configured",
        )

    try:
        # Use session token as identifier for TURN credentials
        credentials = generate_turn_credentials(f"embed:{session_token[:16]}")
        return TurnCredentialsResponse(**credentials)
    except Exception as e:
        logger.error(f"Failed to generate TURN credentials for embed session: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate TURN credentials",
        )


@router.options("/turn-credentials/{session_token}")
async def options_turn_credentials(request: Request, session_token: str):
    """Handle CORS preflight for TURN credentials endpoint"""
    origin = request.headers.get("origin", "*")

    # Try to validate the session token and get allowed domains
    allowed_origin = origin
    try:
        embed_session = await db_client.get_embed_session_by_token(session_token)
        if embed_session:
            embed_token = await db_client.get_embed_token_by_id(
                embed_session.embed_token_id
            )
            if embed_token:
                # Check if origin is in allowed domains (empty means allow all)
                if validate_origin(origin, embed_token.allowed_domains or []):
                    allowed_origin = origin
                else:
                    allowed_origin = ""
    except Exception:
        # On error, be permissive for OPTIONS
        pass

    return Response(
        headers={
            "Access-Control-Allow-Origin": allowed_origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "86400",
        }
    )


@router.options("/config/{token}")
async def options_config(request: Request, token: str):
    """Handle CORS preflight for config endpoint"""
    # Get origin header
    origin = request.headers.get("origin", "*")

    # Try to validate the token and get allowed domains
    allowed_origin = origin
    try:
        embed_token = await db_client.get_embed_token_by_token(token)
        if embed_token and embed_token.is_active:
            # Check if origin is in allowed domains
            if validate_origin(origin, embed_token.allowed_domains or []):
                allowed_origin = origin
            else:
                # If not allowed, don't include the origin
                allowed_origin = ""
    except Exception:
        # On error, be permissive for OPTIONS
        pass

    return Response(
        headers={
            "Access-Control-Allow-Origin": allowed_origin,
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "86400",
        }
    )
