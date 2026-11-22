from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from api.routes.agent_stream import router as agent_stream_router
from api.routes.auth import router as auth_router
from api.routes.business import router as business_router
from api.routes.campaign import router as campaign_router
from api.routes.credentials import router as credentials_router
from api.routes.knowledge_base import router as knowledge_base_router
from api.routes.livekit import router as livekit_router
from api.routes.node_types import router as node_types_router
from api.routes.organization import router as organization_router
from api.routes.organization_usage import router as organization_usage_router
from api.routes.public_agent import router as public_agent_router
from api.routes.public_download import router as public_download_router
from api.routes.public_embed import router as public_embed_router
from api.routes.reports import router as reports_router
from api.routes.s3_signed_url import router as s3_router
from api.routes.service_keys import router as service_keys_router
from api.routes.superuser import router as superuser_router
from api.routes.telephony import router as telephony_router
from api.routes.tool import router as tool_router
from api.routes.turn_credentials import router as turn_credentials_router
from api.routes.user import router as user_router
from api.routes.workflow import router as workflow_router
from api.routes.workflow_embed import router as workflow_embed_router
from api.routes.workflow_recording import router as workflow_recording_router
from api.routes.workflow_text_chat import router as workflow_text_chat_router
from api.routes.whatsapp_webhook import router as whatsapp_webhook_router
from api.services.integrations import all_routers

router = APIRouter(
    tags=["main"],
    responses={404: {"description": "Not found"}},
)

router.include_router(telephony_router)
router.include_router(superuser_router)
router.include_router(workflow_router)
router.include_router(user_router)
router.include_router(campaign_router)
router.include_router(credentials_router)
router.include_router(tool_router)
router.include_router(organization_router)
router.include_router(s3_router)
router.include_router(service_keys_router)
router.include_router(organization_usage_router)
router.include_router(reports_router)
router.include_router(turn_credentials_router)
router.include_router(public_embed_router)
router.include_router(public_agent_router)
router.include_router(public_download_router)
router.include_router(workflow_embed_router)
router.include_router(knowledge_base_router)
router.include_router(workflow_recording_router)
router.include_router(workflow_text_chat_router)
router.include_router(auth_router)
router.include_router(business_router)
router.include_router(node_types_router)
router.include_router(agent_stream_router)
router.include_router(livekit_router)
router.include_router(whatsapp_webhook_router)

for _integration_router in all_routers():
    router.include_router(_integration_router)

from api.routes.health import HealthResponse, router as health_router

router.include_router(health_router)
