"""Local, format-tolerant sanity checks for provider API keys.

Provider key formats change (length, prefix, new token families), so this does
NOT pin a prefix or exact length. It only rejects values that cannot be a key
(empty, whitespace/control characters, quotes left over from copy-paste, absurd
length) and values that are clearly credentials for a different system. It never
claims a key is valid: real validity is only established by the first
authorised call to the provider.
"""

from __future__ import annotations

MIN_LENGTH = 8
MAX_LENGTH = 512

# Credentials of other systems that people paste by mistake.
_WRONG_SYSTEM_PREFIXES = {
    "dgr": "an SPX Voice/Dograh API key (dgr...)",
    "mps": "a Dograh service key (mps...)",
}


def check_key_format(provider_label: str, key: object) -> None:
    """Raise ValueError (never containing the key) if *key* cannot be a key."""
    if not isinstance(key, str):
        raise ValueError(f"The {provider_label} API key must be text.")
    if not key:
        raise ValueError(f"The {provider_label} API key is empty.")
    if key != key.strip():
        raise ValueError(
            f"The {provider_label} API key has leading or trailing whitespace."
        )
    if key[0] in "\"'`" or key[-1] in "\"'`":
        raise ValueError(
            f"The {provider_label} API key includes quotation marks; paste only the key."
        )
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise ValueError(
            f"The {provider_label} API key contains spaces or non-printable characters."
        )
    if not MIN_LENGTH <= len(key) <= MAX_LENGTH:
        raise ValueError(
            f"The {provider_label} API key length looks wrong "
            f"(expected {MIN_LENGTH}-{MAX_LENGTH} characters)."
        )
    lowered = key.lower()
    for prefix, what in _WRONG_SYSTEM_PREFIXES.items():
        if lowered.startswith(prefix):
            raise ValueError(
                f"The {provider_label} API key looks like {what}, not a "
                f"{provider_label} key."
            )


def check_key_or_keys(provider_label: str, api_key: object) -> bool:
    """Format-check a single key or a list of keys. Returns True when plausible."""
    keys = api_key if isinstance(api_key, list) else [api_key]
    if not keys:
        raise ValueError(f"The {provider_label} API key is empty.")
    for key in keys:
        check_key_format(provider_label, key)
    return True
