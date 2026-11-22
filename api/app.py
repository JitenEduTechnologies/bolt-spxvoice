from api.logging_config import setup_logging

# Set up logging and get the listener for cleanup
setup_logging()

from contextlib import asynccontextmanager, nullcontext
import re
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from api.constants import REDIS_URL

try:
    from api.mcp_server import mcp

    mcp_app = mcp.http_app(path="/", stateless_http=True)
except Exception as exc:  # optional MCP addon must never brick the core API
    logger.warning(f"MCP addon unavailable; starting without it: {exc}")
    mcp = None
    mcp_app = None
from api.routes.main import router as main_router
from api.services.livekit.worker_process import (
    apply_livekit_worker_settings,
    stop_livekit_worker,
)
from api.services.pipecat.tracing_config import (
    handle_langfuse_sync,
    load_all_org_langfuse_credentials,
)
from api.services.worker_sync.manager import (
    WorkerSyncManager,
    set_worker_sync_manager,
)
from api.services.worker_sync.protocol import WorkerSyncEventType
from api.services.workflow.templates import ensure_default_workflow_templates
from api.tasks.arq import get_arq_redis
from api.utils.run_context import set_current_request_id

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp_ctx = mcp_app.lifespan(app) if mcp_app is not None else nullcontext()
    async with mcp_ctx:
        # warmup arq pool
        await get_arq_redis()

        # Pre-register all org-specific Langfuse exporters so they're ready
        # before any pipeline runs, without per-call DB lookups.
        await load_all_org_langfuse_credentials()

        # Start cross-worker sync manager so config changes propagate to all workers
        sync_manager = WorkerSyncManager(REDIS_URL)
        sync_manager.register(
            WorkerSyncEventType.LANGFUSE_CREDENTIALS, handle_langfuse_sync
        )
        await sync_manager.start()
        set_worker_sync_manager(sync_manager)
        apply_livekit_worker_settings()

        # Seed the built-in starter templates so the "Create Agent" picker is
        # never empty on a fresh install. Idempotent and best-effort.
        try:
            await ensure_default_workflow_templates()
        except Exception as exc:
            logger.warning(f"Workflow template seeding skipped: {exc}")

        # Start Chatwoot outbox recovery worker loop (Gap 3)
        try:
            from api.services.chatwoot.outbox import chatwoot_outbox_service
            await chatwoot_outbox_service.start_outbox_worker()
        except Exception as exc:
            logger.warning(f"Chatwoot outbox worker startup skipped: {exc}")

        # Start autonomous operational health monitoring scheduler (Phase C3)
        try:
            from api.services.health.scheduler import health_scheduler
            await health_scheduler.start()
        except Exception as exc:
            logger.warning(f"Health monitoring scheduler startup skipped: {exc}")

        # Deterministic, bounded recovery for interrupted LiveKit finalizations.
        try:
            from api.services.livekit.finalization_recovery import (
                schedule_finalization_recovery,
            )

            schedule_finalization_recovery()
        except Exception as exc:
            logger.warning(f"LiveKit finalization recovery startup skipped: {exc}")

        yield  # Run app

        # Shutdown sequence - this runs when FastAPI is shutting down
        from api.services.livekit.finalization_recovery import stop_finalization_recovery
        await stop_finalization_recovery()
        logger.info("Starting graceful shutdown...")
        try:
            from api.services.health.scheduler import health_scheduler
            await health_scheduler.stop()
        except Exception as exc:
            logger.warning(f"Error stopping health monitoring scheduler: {exc}")
        try:
            from api.services.chatwoot.outbox import chatwoot_outbox_service
            await chatwoot_outbox_service.stop_outbox_worker()
        except Exception as exc:
            logger.warning(f"Error stopping Chatwoot outbox worker: {exc}")
        stop_livekit_worker()
        await sync_manager.stop()


app = FastAPI(
    title="SPX Voice API",
    description="API for the SPX Voice app",
    version="1.0.0",
    openapi_url=f"{API_PREFIX}/openapi.json",
    lifespan=lifespan,
    servers=[
        {"url": "http://localhost:8000", "description": "Local development"},
    ],
)


# Safe regex for request & trace IDs
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")


@app.middleware("http")
async def request_id_and_correlation_middleware(request: Request, call_next):
    # 1. Resolve or generate Request ID
    raw_req_id = (
        request.headers.get("X-Request-ID")
        or request.headers.get("X-Correlation-ID")
    )
    if raw_req_id and _SAFE_ID_RE.match(raw_req_id):
        request_id = raw_req_id
    else:
        request_id = f"req_{uuid.uuid4().hex[:16]}"

    # 2. Link call/webhook IDs if present (e.g. CallUUID, call_id, X-Call-ID, X-Webhook-ID)
    linked_id = (
        request.headers.get("X-Call-ID")
        or request.headers.get("X-Webhook-ID")
        or request.query_params.get("CallUUID")
        or request.query_params.get("call_id")
        or request.query_params.get("CallSid")
    )
    if linked_id and _SAFE_ID_RE.match(str(linked_id)):
        safe_linked = str(linked_id)[:24]
        effective_trace_id = f"{request_id}:{safe_linked}"
    else:
        effective_trace_id = request_id

    # 3. Propagate into context variable for Loguru injection
    set_current_request_id(effective_trace_id)
    request.state.request_id = effective_trace_id

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = effective_trace_id
        return response
    except Exception as exc:
        logger.error(
            f"[HTTP] Unhandled exception during request {effective_trace_id}: {type(exc).__name__}"
        )
        raise
    finally:
        set_current_request_id(None)


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "SPX Voice API",
        "health": f"{API_PREFIX}/health",
        "docs": "/docs",
    }


api_router = APIRouter()

# include subrouters here
api_router.include_router(main_router)

# main router with api prefix
app.include_router(api_router, prefix=API_PREFIX)

# Mount the MCP server — agents reach it at /api/v1/mcp over Streamable HTTP,
# authenticating with the same X-API-Key header used by the REST API.
# Mounted under /api/v1 so existing reverse-proxy rules (nginx etc.) route it
# without any extra configuration.
if mcp_app is not None:
    app.mount(f"{API_PREFIX}/mcp", mcp_app)
