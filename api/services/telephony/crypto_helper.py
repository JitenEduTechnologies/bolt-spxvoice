"""Provider-aware credential encryption and decryption helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from loguru import logger


from api.utils.crypto import (
    CryptoConfigurationError,
    decrypt_string,
    encrypt_string,
    is_encrypted,
)

# Derived sensitive fields for Vobiz LiveKit integration
VOBIZ_LIVEKIT_SENSITIVE_FIELDS = {"vobiz_sip_password"}


def get_sensitive_fields_for_provider(provider_name: str) -> List[str]:
    """Retrieve all sensitive field names for a given provider.
    
    Raises:
        CryptoConfigurationError: If the provider is unregistered, preventing
            unregistered sensitive fields from silently escaping encryption.
    """
    from api.services.telephony import registry as telephony_registry
    spec = telephony_registry.get_optional(provider_name)
    if spec is None:
        raise CryptoConfigurationError(
            f"Unregistered telephony provider '{provider_name}' cannot be securely processed."
        )

    fields: List[str] = []
    if spec.ui_metadata is not None:
        fields = [f.name for f in spec.ui_metadata.fields if f.sensitive]

    if provider_name == "vobiz":
        for vf in VOBIZ_LIVEKIT_SENSITIVE_FIELDS:
            if vf not in fields:
                fields.append(vf)

    return fields


def encrypt_provider_credentials(
    provider_name: str, credentials: Dict[str, Any]
) -> Dict[str, Any]:
    """Encrypt only the sensitive fields of a provider's credentials dict.
    
    Non-sensitive routing fields (e.g. sip_host, connection_id, from_numbers)
    remain unencrypted JSON.
    """
    if not credentials or not isinstance(credentials, dict):
        return credentials or {}

    sensitive_fields = get_sensitive_fields_for_provider(provider_name)
    out = dict(credentials)

    for field_name in sensitive_fields:
        val = out.get(field_name)
        if val is not None and isinstance(val, str) and not is_encrypted(val):
            out[field_name] = encrypt_string(val)

    return out


def decrypt_provider_credentials(
    provider_name: str, credentials: Dict[str, Any], allow_plaintext: bool = True
) -> Dict[str, Any]:
    """Decrypt encrypted sensitive fields in memory for authorized runtime execution.
    
    If allow_plaintext is True, legacy unencrypted values are passed through.
    If allow_plaintext is False (or STRICT_CREDENTIAL_ENCRYPTION is active),
    unencrypted values will raise a decryption error.
    """
    if not credentials or not isinstance(credentials, dict):
        return credentials or {}

    sensitive_fields = get_sensitive_fields_for_provider(provider_name)
    out = dict(credentials)

    for field_name in sensitive_fields:
        val = out.get(field_name)
        if val is not None and isinstance(val, str) and is_encrypted(val):
            out[field_name] = decrypt_string(val, allow_plaintext=allow_plaintext)

    return out


def is_all_sensitive_encrypted(
    provider_name: str, credentials: Dict[str, Any]
) -> bool:
    """Verify that all non-empty sensitive fields are encrypted."""
    if not credentials or not isinstance(credentials, dict):
        return True

    sensitive_fields = get_sensitive_fields_for_provider(provider_name)
    for field_name in sensitive_fields:
        val = credentials.get(field_name)
        if val is not None and isinstance(val, str) and val.strip():
            if not is_encrypted(val):
                return False
    return True
