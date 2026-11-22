"""Authenticated encryption utilities for sensitive data at rest.

Uses authenticated symmetric encryption (Fernet: AES-128-CBC + HMAC-SHA256)
with a versioned envelope format:
    enc:v1:<key_id>:<base64_payload>

Keys are loaded from environment variables TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS
or TELEPHONY_ENCRYPTION_KEYS (also supports single-key _KEY variants).
Supports key rotation: comma-separated key pairs like:
    k1:key1_base64,k2:key2_base64
The first key in the list is always used for encryption; any known key can decrypt.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple
from loguru import logger
from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoError(Exception):
    """Base exception for cryptographic operations."""
    pass


class CryptoConfigurationError(CryptoError):
    """Raised when keys are missing or misconfigured."""
    pass


class CryptoDecryptionError(CryptoError):
    """Raised when ciphertext authentication or decryption fails."""
    pass


ENVELOPE_PREFIX = "enc:v1:"


def is_encrypted(val: object) -> bool:
    """Check if a string matches the encrypted envelope format."""
    return isinstance(val, str) and val.startswith(ENVELOPE_PREFIX)


def _load_keys() -> Tuple[List[Tuple[str, bytes]], str]:
    """Load and parse encryption keys from environment.
    
    Returns:
        A tuple of ([(key_id, raw_key_bytes), ...], primary_key_id).
    """
    candidate_vars = (
        "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS",
        "TELEPHONY_ENCRYPTION_KEYS",
        "TELEPHONY_CREDENTIALS_ENCRYPTION_KEY",
        "TELEPHONY_ENCRYPTION_KEY",
    )
    configured: Dict[str, str] = {}
    for var_name in candidate_vars:
        val = os.getenv(var_name, "").strip()
        if val:
            configured[var_name] = val

    if not configured:
        return [], ""

    unique_vals = set(configured.values())
    if len(unique_vals) > 1:
        # Names only: never echo any part of a key value into an error/log.
        details = ", ".join(sorted(configured))
        raise CryptoConfigurationError(
            f"Conflicting encryption key configurations detected across environment variables: {details}"
        )

    raw_keys_env = next(iter(unique_vals))




    parsed: List[Tuple[str, bytes]] = []
    items = [item.strip() for item in raw_keys_env.split(",") if item.strip()]

    for item in items:
        if ":" in item:
            key_id, key_str = item.split(":", 1)
            key_id = key_id.strip()
            key_bytes = key_str.strip().encode("utf-8")
        else:
            # Default key ID if none specified
            key_id = "k1"
            key_bytes = item.strip().encode("utf-8")

        try:
            # Validate key format with Fernet constructor
            Fernet(key_bytes)
            parsed.append((key_id, key_bytes))
        except Exception as e:
            raise CryptoConfigurationError(
                f"Invalid cryptographic key format for key_id '{key_id}'"
            ) from e

    if not parsed:
        return [], ""

    primary_key_id = parsed[0][0]
    return parsed, primary_key_id


def encrypt_string(plaintext: str, key_id: Optional[str] = None) -> str:
    """Encrypt a plaintext string using authenticated encryption.
    
    Args:
        plaintext: The sensitive string to encrypt.
        key_id: Optional specific key ID to use; defaults to the primary key.
        
    Returns:
        Envelope-tagged ciphertext: enc:v1:<key_id>:<payload>
    """
    if plaintext is None:
        return None
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)

    # Avoid double encryption
    if is_encrypted(plaintext):
        return plaintext

    keys, primary_id = _load_keys()
    if not keys:
        raise CryptoConfigurationError(
            "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS is not configured. Cannot encrypt sensitive data."
        )

    target_id = key_id or primary_id
    target_key = next((k_bytes for k_id, k_bytes in keys if k_id == target_id), None)
    if not target_key:
        raise CryptoConfigurationError(f"Encryption key ID '{target_id}' not found in configuration.")

    fernet = Fernet(target_key)
    ciphertext_bytes = fernet.encrypt(plaintext.encode("utf-8"))
    return f"{ENVELOPE_PREFIX}{target_id}:{ciphertext_bytes.decode('ascii')}"


def decrypt_string(ciphertext: str, allow_plaintext: bool = True) -> str:
    """Decrypt an envelope-tagged ciphertext.
    
    Args:
        ciphertext: The encrypted envelope string (or legacy plaintext if allow_plaintext=True).
        allow_plaintext: If True and ciphertext is not encrypted, returns as-is.
                         If False and ciphertext is unencrypted, raises CryptoDecryptionError.
                         
    Returns:
        The decrypted plaintext string.
    """
    if ciphertext is None:
        return None

    if not is_encrypted(ciphertext):
        is_strict = os.getenv("STRICT_CREDENTIAL_ENCRYPTION", "false").lower() == "true"
        if is_strict or not allow_plaintext:
            raise CryptoDecryptionError(
                "Unencrypted credential encountered while strict credential encryption is active."
            )
        return ciphertext

    # Parse envelope: enc:v1:<key_id>:<payload>
    without_prefix = ciphertext[len(ENVELOPE_PREFIX):]
    if ":" not in without_prefix:
        raise CryptoDecryptionError("Malformed ciphertext envelope: missing key ID delimiter.")

    key_id, payload_str = without_prefix.split(":", 1)
    try:
        payload_bytes = payload_str.encode("ascii")
    except UnicodeEncodeError as e:
        raise CryptoDecryptionError("Malformed ciphertext envelope: non-ASCII payload.") from e

    keys, _ = _load_keys()
    if not keys:
        raise CryptoConfigurationError(
            "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS is not configured. Cannot decrypt ciphertext."
        )

    # First try the specific key ID tagged in the envelope
    matched_key = next((k_bytes for k_id, k_bytes in keys if k_id == key_id), None)
    if matched_key:
        try:
            decrypted_bytes = Fernet(matched_key).decrypt(payload_bytes)
            return decrypted_bytes.decode("utf-8")
        except InvalidToken as e:
            raise CryptoDecryptionError(
                f"Ciphertext authentication/integrity verification failed for key ID '{key_id}'."
            ) from e

    # Fallback to MultiFernet across all configured keys for key rotation flexibility
    fernets = [Fernet(k_bytes) for _, k_bytes in keys]
    multi = MultiFernet(fernets)
    try:
        decrypted_bytes = multi.decrypt(payload_bytes)
        return decrypted_bytes.decode("utf-8")
    except InvalidToken as e:
        raise CryptoDecryptionError(
            "Ciphertext authentication failed across all configured encryption keys."
        ) from e


def generate_new_key() -> str:
    """Generate a fresh base64-encoded 256-bit Fernet key."""
    return Fernet.generate_key().decode("ascii")

