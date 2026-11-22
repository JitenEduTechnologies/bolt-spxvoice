"""Operational guardrails for the CP-02 owner endpoints.

* Kill switches: an operator can switch a risky capability off with an environment variable
  and a restart, without rolling the release back
  (``CP02_EXPORTS_ENABLED``, ``CP02_BULK_ACTIONS_ENABLED``, ``CP02_RECORDING_BUNDLE_ENABLED``).
* Rate limits for heavy endpoints (exports, bulk actions, bulk re-analysis). Fixed window per
  user, stored in Redis so it holds across API workers; if Redis is unreachable it falls back
  to a per-process window (bounded memory) rather than failing the request.

Rate limiting is skipped when ``ENVIRONMENT=test`` unless ``CP02_RATE_LIMIT_FORCE=1`` so unit
tests that call handlers repeatedly are unaffected.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from loguru import logger

_FALSE = {"0", "false", "off", "no", "disabled"}

#: bucket -> (max requests, window seconds)
LIMITS: Dict[str, Tuple[int, int]] = {
    "export": (12, 60),
    "bulk": (30, 60),
    "analyze": (6, 60),
}

_local_windows: Dict[str, Tuple[int, int]] = {}
_redis: Any = None


def feature_enabled(name: str) -> bool:
    """``CP02_<NAME>_ENABLED`` (default on). Read at call time so a restart is enough."""
    return os.getenv(f"CP02_{name.upper()}_ENABLED", "true").strip().lower() not in _FALSE


def require_feature(name: str, message: str) -> None:
    if not feature_enabled(name):
        raise HTTPException(status_code=503, detail=f"{message} It has been switched off by an administrator.")


def _rate_limit_active() -> bool:
    if os.getenv("CP02_RATE_LIMIT_DISABLED", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.getenv("ENVIRONMENT", "").strip().lower() == "test":
        return os.getenv("CP02_RATE_LIMIT_FORCE", "").strip().lower() in {"1", "true", "yes"}
    return True


def _get_redis() -> Any:
    global _redis
    if _redis is None:
        from redis import asyncio as aioredis

        from api.constants import REDIS_URL

        _redis = aioredis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
    return _redis


def reset_for_tests() -> None:
    global _redis
    _redis = None
    _local_windows.clear()


async def check_rate_limit(bucket: str, subject: Any, *, now: Optional[float] = None) -> None:
    """Raise HTTP 429 (with ``Retry-After``) when ``subject`` exceeded ``bucket``'s window."""
    if not _rate_limit_active():
        return
    limit, window = LIMITS[bucket]
    now = time.time() if now is None else now
    window_index = int(now // window)
    key = f"cp02:rl:{bucket}:{subject}:{window_index}"
    retry_after = max(1, int((window_index + 1) * window - now))
    try:
        client = _get_redis()
        count = int(await client.incr(key))
        if count == 1:
            await client.expire(key, window + 5)
    except Exception as exc:  # Redis down/unreachable: degrade to a per-process window
        logger.debug(f"[RateLimit] redis unavailable ({type(exc).__name__}); using in-process window")
        previous_window, previous_count = _local_windows.get(key, (window_index, 0))
        count = previous_count + 1 if previous_window == window_index else 1
        _local_windows[key] = (window_index, count)
        if len(_local_windows) > 10_000:  # bounded memory
            for stale in [k for k, (w, _) in _local_windows.items() if w != window_index]:
                _local_windows.pop(stale, None)
    if count > limit:
        raise HTTPException(
            status_code=429,
            detail=f"Too many {bucket} requests. Please wait {retry_after} seconds and try again.",
            headers={"Retry-After": str(retry_after)},
        )
