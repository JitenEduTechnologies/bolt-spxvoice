"""At-rest protection for model-provider secrets stored in user configurations.

Masking (api/services/configuration/masking.py) is a display concern only. This
module is what protects the persisted JSON: secret fields are wrapped in the
Fernet envelope from api/utils/crypto.py (``enc:v1:<key_id>:<payload>``) before
they reach the database and unwrapped when read back.

Properties
----------
* Owner binding: the Fernet plaintext is ``<context> US <secret>`` (US = 0x1f)
  where *context* names the owning row (``user:<id>``). A ciphertext copied into
  another owner's row authenticates but fails the context check, so a stolen or
  SQL-injected envelope cannot be replayed under another tenant.
* Rotation: crypto.py accepts ``id1:key1,id2:key2``; the first key encrypts, any
  listed key decrypts, and the envelope carries the key id. Every save
  re-encrypts with the primary key; api/scripts/config_secrets_maintenance.py
  does it in bulk (provider configs and workflow overrides only: telephony
  credentials share the key but are NOT rotated by it).
* Owner binding covers user-configuration secrets only; legacy workflow
  override envelopes are not owner-bound (see override_secrets.py).
* Fail closed on read: wrong/missing key, tampering, malformed or mis-bound
  envelopes raise ConfigDecryptionError (content-free message, no key or secret
  material, never the ciphertext).
* Never double-encrypt, never accept user-supplied envelopes: an envelope-shaped
  *plaintext* is rejected (a legitimate secret never looks like one).
* Legacy plaintext rows still load and are encrypted on the next save.
* If no encryption key is configured, values are stored unchanged (legacy
  behaviour, logged once) unless REQUIRE_CONFIG_ENCRYPTION=true, in which case
  saving a secret fails closed.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable, Dict

from loguru import logger

from api.utils.crypto import (
    CryptoError,
    _load_keys,
    decrypt_string,
    encrypt_string,
    is_encrypted,
)

SERVICE_SECTIONS = ("llm", "tts", "stt", "embeddings", "realtime")
SECRET_FIELDS = ("api_key", "aws_secret_key", "credentials")
_SEP = "\x1f"
_warned_plaintext = False


class ConfigEncryptionRequiredError(RuntimeError):
    """Raised when encryption is mandatory but no key is configured."""


class ConfigDecryptionError(RuntimeError):
    """Stored credentials cannot be read. Message and reason never hold secrets."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(
            "Stored provider credentials could not be decrypted "
            f"({reason}). Check the configuration encryption key; the saved "
            "keys may need to be re-entered."
        )


def _require_encryption() -> bool:
    return os.getenv("REQUIRE_CONFIG_ENCRYPTION", "false").strip().lower() == "true"


def encryption_available() -> bool:
    try:
        keys, _ = _load_keys()
    except CryptoError:
        return False
    return bool(keys)


def encrypt_value(plaintext: str, context: str) -> str:
    if is_encrypted(plaintext):
        raise ValueError("Refusing to store an envelope-shaped value as a secret.")
    return encrypt_string(f"{context}{_SEP}{plaintext}")


def decrypt_value(value: str, context: str) -> str:
    if not is_encrypted(value):
        return value  # legacy plaintext
    try:
        raw = decrypt_string(value)
    except CryptoError as exc:
        text = str(exc).lower()
        reason = (
            "no encryption key configured"
            if "not configured" in text
            else "integrity check failed"
            if "authentication" in text or "integrity" in text
            else "malformed ciphertext"
        )
        raise ConfigDecryptionError(reason) from None
    except Exception:
        raise ConfigDecryptionError("malformed ciphertext") from None
    ctx, sep, secret = raw.partition(_SEP)
    if not sep or ctx != context:
        raise ConfigDecryptionError("ciphertext belongs to a different owner")
    return secret


def _map_secret(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value) if value else value
    if isinstance(value, list):
        return [fn(v) if isinstance(v, str) and v else v for v in value]
    return value


def _walk(configuration: Dict[str, Any], fn: Callable[[str], str]) -> Dict[str, Any]:
    out = copy.deepcopy(configuration)
    for section in SERVICE_SECTIONS:
        block = out.get(section)
        if not isinstance(block, dict):
            continue
        for field in SECRET_FIELDS:
            if field in block and block[field] is not None:
                block[field] = _map_secret(block[field], fn)
    return out


def contains_envelope_input(configuration: Dict[str, Any]) -> bool:
    """True if any incoming secret field looks like a stored ciphertext."""
    found = False

    def probe(value: str) -> str:
        nonlocal found
        found = found or is_encrypted(value)
        return value

    _walk(configuration, probe)
    return found


def _has_secret(configuration: Dict[str, Any]) -> bool:
    found = False

    def probe(value: str) -> str:
        nonlocal found
        found = found or bool(value)
        return value

    _walk(configuration, probe)
    return found


def encrypt_configuration_secrets(
    configuration: Dict[str, Any], *, context: str
) -> Dict[str, Any]:
    """Return a copy of *configuration* with secret fields encrypted."""
    global _warned_plaintext
    if not encryption_available():
        if _has_secret(configuration):
            if _require_encryption():
                raise ConfigEncryptionRequiredError(
                    "Credential encryption is required but no encryption key is "
                    "configured; refusing to store provider keys in plaintext."
                )
            if not _warned_plaintext:
                _warned_plaintext = True
                logger.warning(
                    "No configuration encryption key is set: provider keys are "
                    "stored in plaintext. Set TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS "
                    "and REQUIRE_CONFIG_ENCRYPTION=true."
                )
        return copy.deepcopy(configuration)
    return _walk(configuration, lambda v: encrypt_value(v, context))


def decrypt_configuration_secrets(
    configuration: Dict[str, Any], *, context: str
) -> Dict[str, Any]:
    """Return a copy of *configuration* with secret fields decrypted."""
    return _walk(configuration, lambda v: decrypt_value(v, context))


def reencrypt_value(value: str, context: str, primary_key_id: str) -> tuple[str, bool]:
    """Bring one stored value onto the primary key. Returns (value, changed).

    Verifies every envelope decrypts (and is bound to *context*) even when it
    already uses the primary key, so corruption is reported, not carried along.
    """
    if not is_encrypted(value):
        return encrypt_value(value, context), True
    plain = decrypt_value(value, context)
    key_id = value.split(":", 3)[2]
    if key_id == primary_key_id:
        return value, False
    return encrypt_value(plain, context), True


def reencrypt_configuration_secrets(
    configuration: Dict[str, Any], *, context: str
) -> tuple[Dict[str, Any], int]:
    """Encrypt plaintext and rotate old-key secrets onto the primary key."""
    keys, primary = _load_keys()
    if not keys:
        raise ConfigEncryptionRequiredError("No encryption key is configured.")
    changed = 0

    def fn(value: str) -> str:
        nonlocal changed
        new, did = reencrypt_value(value, context, primary)
        changed += int(did)
        return new

    return _walk(configuration, fn), changed
