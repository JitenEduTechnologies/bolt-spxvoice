"""Provider readiness: is the model configuration usable for a voice call yet?

A fresh deployment legitimately starts without any model-provider key; the owner
adds one through the dashboard. That state is reported as
``provider_unconfigured`` (an expected, actionable state) rather than as a
service failure. Messages here never include key values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

PROVIDER_CONFIGURED = "configured"
PROVIDER_UNCONFIGURED = "provider_unconfigured"
PROVIDER_UNKNOWN = "unknown"

# Providers that authenticate without a plain api_key.
_KEYLESS_PROVIDERS = frozenset({"speaches", "google_vertex_realtime", "aws_bedrock"})

UNCONFIGURED_ACTION = (
    "Open the dashboard, go to the model configuration page, choose "
    "'Google Realtime' (Gemini), paste your API key and save. "
    "Then retry Test Audio."
)


@dataclass
class ProviderReadiness:
    status: str
    missing: List[str] = field(default_factory=list)
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.status == PROVIDER_CONFIGURED


def _provider_value(section: Any) -> str:
    provider = getattr(section, "provider", None)
    return str(getattr(provider, "value", provider) or "")


_MASK_MARKER = "***"  # masked display placeholder, never a usable key


def _usable(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and _MASK_MARKER not in v


def _has_key(value: Any) -> bool:
    # Inactive sections may legitimately keep a stale masked placeholder (see
    # check_for_masked_keys); it must never count as a configured key.
    if isinstance(value, str):
        return _usable(value)
    if isinstance(value, (list, tuple)):
        return any(_usable(v) for v in value)
    return False


def section_is_configured(section: Any) -> bool:
    if section is None:
        return False
    if _provider_value(section) in _KEYLESS_PROVIDERS:
        return True
    return _has_key(getattr(section, "api_key", None))


def evaluate_user_config(user_config: Any) -> ProviderReadiness:
    """Readiness of the *active* mode of an (effective) UserConfiguration."""
    if getattr(user_config, "is_realtime", False):
        required: Iterable[str] = ("realtime",)
    else:
        required = ("llm", "stt", "tts")

    missing = [
        name
        for name in required
        if not section_is_configured(getattr(user_config, name, None))
    ]
    if not missing:
        return ProviderReadiness(PROVIDER_CONFIGURED)

    if "realtime" in missing:
        what = "The Gemini realtime API key is not configured."
    else:
        what = "These model providers have no API key: " + ", ".join(missing) + "."
    return ProviderReadiness(
        PROVIDER_UNCONFIGURED,
        missing=missing,
        message=f"{what} {UNCONFIGURED_ACTION}",
    )


def evaluate_with_overrides(user_config: Any, model_overrides: Optional[dict]) -> ProviderReadiness:
    from api.services.configuration.resolve import resolve_effective_config

    return evaluate_user_config(resolve_effective_config(user_config, model_overrides))


# ---------------------------------------------------------------------------
# Deployment-level status for the public health endpoint
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 5.0
_cache: dict[str, Any] = {"at": 0.0, "value": PROVIDER_UNKNOWN}


def reset_cache() -> None:
    _cache["at"] = 0.0
    _cache["value"] = PROVIDER_UNKNOWN


async def deployment_provider_status(db_client: Any, timeout_seconds: float = 1.5) -> str:
    """configured | provider_unconfigured | unknown (DB unreachable).

    "configured" means at least one saved user configuration holds a realtime
    provider credential. Intended for single-owner deployments such as the
    acceptance stack; it never reads or returns a credential value.
    """
    import asyncio

    now = time.monotonic()
    if now - _cache["at"] < _CACHE_TTL_SECONDS:
        return _cache["value"]
    try:
        configured = await asyncio.wait_for(
            db_client.any_realtime_provider_configured(), timeout=timeout_seconds
        )
        value = PROVIDER_CONFIGURED if configured else PROVIDER_UNCONFIGURED
    except Exception:
        value = PROVIDER_UNKNOWN
    _cache["at"] = now
    _cache["value"] = value
    return value
