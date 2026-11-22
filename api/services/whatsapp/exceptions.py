"""Custom exceptions for Meta WhatsApp Cloud API delivery adapter."""

from __future__ import annotations


class MetaWhatsAppError(Exception):
    """Base exception for Meta WhatsApp Cloud API operations."""
    pass


class MetaSendingDisabledError(MetaWhatsAppError):
    """Raised when real Meta sending is attempted while safety lock is active."""
    pass


class MetaConfigurationError(MetaWhatsAppError):
    """Raised when Meta credentials or Graph API configuration is missing or invalid."""
    pass


class MetaAuthenticationError(MetaWhatsAppError):
    """Raised when Meta access token is invalid, expired, or unauthorized (HTTP 401/403)."""
    pass


class InvalidTemplateError(MetaWhatsAppError):
    """Raised when template is not in approved transactional categories or parameters are invalid."""
    pass


class UnconfirmedAppointmentError(MetaWhatsAppError):
    """Raised when attempting to send an appointment confirmation without an authoritative confirmed booking."""
    pass


class TenantIsolationViolationError(MetaWhatsAppError):
    """Raised when cross-tenant credentials, recipients, or documents are detected."""
    pass


class WhatsAppOptOutError(MetaWhatsAppError):
    """Raised when recipient has explicitly opted out of WhatsApp messaging."""
    pass


class MetaRateLimitError(MetaWhatsAppError):
    """Raised when Meta Cloud API rate limit / tier capacity is exceeded (HTTP 429 - retryable)."""
    pass


class MetaTransientNetworkError(MetaWhatsAppError):
    """Raised on transient network timeouts or 5xx server errors (retryable)."""
    pass


class MetaPermanentClientError(MetaWhatsAppError):
    """Raised on unrecoverable 4xx client errors (non-retryable)."""
    pass

