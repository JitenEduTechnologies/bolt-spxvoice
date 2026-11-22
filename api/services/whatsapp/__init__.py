"""Meta WhatsApp Cloud API delivery adapter module."""

from api.services.whatsapp.exceptions import (
    InvalidTemplateError,
    MetaAuthenticationError,
    MetaConfigurationError,
    MetaPermanentClientError,
    MetaRateLimitError,
    MetaSendingDisabledError,
    MetaTransientNetworkError,
    MetaWhatsAppError,
    TenantIsolationViolationError,
    UnconfirmedAppointmentError,
    WhatsAppOptOutError,
)
from api.services.whatsapp.meta_adapter import (
    DEFAULT_GRAPH_VERSION,
    SUPPORTED_GRAPH_VERSIONS,
    MetaWhatsAppCloudAdapter,
    meta_whatsapp_adapter,
)
from api.services.whatsapp.template_validator import (
    APPROVED_TRANSACTIONAL_TEMPLATES,
    TemplatePayloadValidator,
)

__all__ = [
    "MetaWhatsAppError",
    "MetaSendingDisabledError",
    "MetaConfigurationError",
    "MetaAuthenticationError",
    "InvalidTemplateError",
    "UnconfirmedAppointmentError",
    "TenantIsolationViolationError",
    "WhatsAppOptOutError",
    "MetaRateLimitError",
    "MetaTransientNetworkError",
    "MetaPermanentClientError",
    "SUPPORTED_GRAPH_VERSIONS",
    "DEFAULT_GRAPH_VERSION",
    "MetaWhatsAppCloudAdapter",
    "meta_whatsapp_adapter",
    "APPROVED_TRANSACTIONAL_TEMPLATES",
    "TemplatePayloadValidator",
]

