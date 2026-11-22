"""External Outage & Incident Notification Adapter (Phase C3).

Dispatches alerts and recovery events to external endpoints (e.g. HTTPS Webhook).
Enforces:
- No hardcoded recipients, credentials, or secrets.
- If unconfigured, marks status as: BLOCKED — OWNER CONFIGURATION.
- Bounded retries (3 attempts) with backoff and timeout.
- Delivery failure logging without crashing caller.
- Recovery notifications on resolution.
- Sanitized payloads (no API keys, passwords, or customer PII).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime
from typing import Any, Callable, Dict, Optional

import httpx
from loguru import logger

from api.services.health.models import IncidentEvent

# Environment-based configuration
ENV_WEBHOOK_URL = "SPX_ALERT_WEBHOOK_URL"
ENV_WEBHOOK_SECRET = "SPX_ALERT_WEBHOOK_SECRET"

SENSITIVE_KEYS_RE = re.compile(
    r"(password|secret|token|api_key|authorization|bearer|cookie|credential|phone|mobile)",
    re.IGNORECASE,
)


def sanitize_context(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively redacts secrets and potential customer PII from event context."""
    sanitized: Dict[str, Any] = {}
    for k, v in data.items():
        if SENSITIVE_KEYS_RE.search(k):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_context(v)
        elif isinstance(v, str) and len(v) > 200:
            sanitized[k] = v[:200] + "... [TRUNCATED]"
        else:
            sanitized[k] = v
    return sanitized


class ExternalNotifier:
    """Dispatches operational alerts to external notification channels."""

    def __init__(self) -> None:
        self._mock_handler: Optional[Callable[[Dict[str, Any]], Any]] = None

    def set_mock_transport(self, handler: Optional[Callable[[Dict[str, Any]], Any]]) -> None:
        """Injects a mock destination for hermetic local testing."""
        self._mock_handler = handler

    def get_configuration_status(self) -> str:
        """Reports whether an external destination is configured."""
        webhook_url = os.getenv(ENV_WEBHOOK_URL)
        if webhook_url:
            return "CONFIGURED"
        return "BLOCKED — OWNER CONFIGURATION"

    def build_payload(self, incident: IncidentEvent, is_recovery: bool = False) -> Dict[str, Any]:
        """Builds clean, sanitized external notification payload."""
        return {
            "event_type": "alert.resolved" if is_recovery else "alert.fired",
            "incident_id": incident.incident_id,
            "component": incident.component,
            "severity": incident.severity.value,
            "health_status": incident.health_status.value,
            "error_code": incident.error_code,
            "message": incident.message,
            "correlation_id": incident.correlation_id,
            "call_id": incident.call_id,
            "first_seen": incident.first_seen.isoformat(),
            "latest_seen": incident.latest_seen.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "count": incident.count,
            "context": sanitize_context(incident.context),
            "environment": os.getenv("ENVIRONMENT", "local"),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def notify(self, incident: IncidentEvent, is_recovery: bool = False) -> bool:
        """Dispatches an incident alert or recovery notification."""
        payload = self.build_payload(incident, is_recovery=is_recovery)

        # 1. Test mock handler check
        if self._mock_handler is not None:
            try:
                res = self._mock_handler(payload)
                if asyncio.iscoroutine(res):
                    await res
                return True
            except Exception as exc:
                logger.error(f"[Alert Notifier] Mock transport delivery failed: {exc}")
                return False

        # 2. Production webhook check
        webhook_url = os.getenv(ENV_WEBHOOK_URL)
        if not webhook_url:
            logger.warning(
                f"[Alert Outage] No external alert webhook configured ({ENV_WEBHOOK_URL} missing). "
                f"Incident {incident.incident_id} ({incident.component}) logged locally only."
            )
            return False

        secret = os.getenv(ENV_WEBHOOK_SECRET, "")
        body = json.dumps(payload, separators=(",", ":"), default=str)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "SPX-Voice-Health-Monitor/1.0",
        }
        if secret:
            sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
            headers["X-SPX-Signature"] = sig

        # Bounded retries (3 attempts, exponential backoff)
        max_attempts = 3
        backoff = 0.2
        timeout = 3.0

        for attempt in range(1, max_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(webhook_url, content=body, headers=headers)
                    if resp.status_code < 300:
                        logger.info(
                            f"[Alert Notifier] Delivered {payload['event_type']} for {incident.incident_id} "
                            f"(attempt {attempt})"
                        )
                        return True
                    logger.warning(
                        f"[Alert Notifier] Delivery returned HTTP {resp.status_code} "
                        f"(attempt {attempt}/{max_attempts})"
                    )
            except Exception as exc:
                logger.warning(
                    f"[Alert Notifier] Delivery attempt {attempt}/{max_attempts} failed: {type(exc).__name__}"
                )

            if attempt < max_attempts:
                await asyncio.sleep(backoff)
                backoff *= 2

        logger.error(
            f"[Alert Delivery Failed] Could not deliver alert {incident.incident_id} "
            f"after {max_attempts} attempts"
        )
        return False


external_notifier = ExternalNotifier()

