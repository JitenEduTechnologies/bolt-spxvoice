"""Providers that exist in the registry but cannot be used in this deployment.

The model-configuration API used to drop such providers silently. It now lists
them separately with a stable id, an availability state and a fixed,
sanitized reason (code + message). Messages are static text: they never include
paths, exception text, URLs or credentials. Unavailable providers are never
part of the selectable schema sets; saving one is also rejected server-side by
``_ensure_livekit_runtime_supported`` in api/routes/user.py.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

STATE_UNAVAILABLE = "unavailable"
REASON_UNSUPPORTED_RUNTIME = "unsupported_by_voice_runtime"
REASON_HOSTED_DISABLED = "hosted_services_disabled"

_MESSAGES = {
    REASON_UNSUPPORTED_RUNTIME: (
        "Not available: the LiveKit voice runtime used by this deployment "
        "cannot run this provider yet."
    ),
    REASON_HOSTED_DISABLED: (
        "Not available: hosted cloud services are disabled in this deployment."
    ),
}


def describe_unavailable(provider_id: str, hosted_provider: bool) -> Dict[str, str]:
    code = REASON_HOSTED_DISABLED if hosted_provider else REASON_UNSUPPORTED_RUNTIME
    return {
        "id": str(provider_id),
        "state": STATE_UNAVAILABLE,
        "reason_code": code,
        "message": _MESSAGES[code],
    }


def build_unavailable(
    registered: Iterable[str],
    offered: Iterable[str],
    hosted_enabled: bool = True,
) -> List[Dict[str, str]]:
    """Registered-but-not-offered providers, sorted by id for a stable payload.

    The hosted provider is attributed to disabled hosted services only when they
    really are disabled; otherwise the voice-runtime reason applies.
    """
    def pid(p) -> str:  # registry keys may be enum members; compare by value
        return str(getattr(p, "value", p))

    offered_ids = {pid(p) for p in offered}
    return [
        describe_unavailable(p, hosted_provider=(p == "dograh" and not hosted_enabled))
        for p in sorted(pid(r) for r in registered)
        if p not in offered_ids
    ]
