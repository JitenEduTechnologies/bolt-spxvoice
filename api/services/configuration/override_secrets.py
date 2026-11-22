"""Workflow ``model_overrides`` must never carry provider secrets.

Provider keys belong to the owner's encrypted user configuration (see
at_rest.py). A workflow only *selects* a provider/model/voice; at run time
``resolve_effective_config`` supplies the key from the user configuration for
that provider.

Legacy workflows may still hold override keys. They are encrypted in place by
api/scripts/config_secrets_maintenance.py and decrypted when overrides are
resolved, but no *new* plaintext secret can be saved: the route rejects it and
the DB layer (save_workflow_draft) refuses it as a second line of defence.

Limitation: legacy override envelopes use the fixed context
"workflow-override", so unlike user-configuration secrets they are NOT bound to
an owner. The route only accepts an envelope that decrypts to the value already
stored in the same slot, which blocks cross-workflow replay through the API;
replay by direct database write is not prevented.
Messages here name paths only, never values.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from api.services.configuration.at_rest import (
    SECRET_FIELDS,
    decrypt_value,
    encrypt_value,
)
from api.utils.crypto import is_encrypted

OVERRIDE_SECTIONS = ("llm", "tts", "stt", "embeddings", "realtime")
OVERRIDE_CONTEXT = "workflow-override"


class WorkflowSecretError(ValueError):
    """A workflow configuration tried to store a provider secret."""


def _iter_secret_slots(configurations: Optional[Dict]):
    overrides = (configurations or {}).get("model_overrides")
    if not isinstance(overrides, dict):
        return
    for section in OVERRIDE_SECTIONS:
        block = overrides.get(section)
        if not isinstance(block, dict):
            continue
        for field in SECRET_FIELDS:
            if field in block:
                yield section, field, block


def _values(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v.strip()]
    return []


def find_plaintext_override_secrets(configurations: Optional[Dict]) -> List[str]:
    """Paths (``model_overrides.llm.api_key``) holding a non-encrypted secret."""
    return [
        f"model_overrides.{section}.{field}"
        for section, field, block in _iter_secret_slots(configurations)
        if any(not is_encrypted(v) for v in _values(block[field]))
    ]


def assert_no_plaintext_override_secrets(configurations: Optional[Dict]) -> None:
    paths = find_plaintext_override_secrets(configurations)
    if paths:
        raise WorkflowSecretError(
            "API keys cannot be stored in workflow settings "
            f"({', '.join(paths)}). Save the key once in Model Configuration; "
            "workflows use it automatically."
        )


def adopt_unchanged_legacy_secrets(
    incoming: Optional[Dict], existing: Optional[Dict]
) -> Optional[Dict]:
    """Encrypt legacy plaintext override secrets that the client did not change.

    The settings UI round-trips masked keys and the merge step restores the
    stored value; without this, a workflow that still holds a legacy plaintext
    key could not save any unrelated setting. A key the user typed (different
    from what is stored) is NOT adopted and is rejected by validation.
    """
    if not incoming or not existing:
        return incoming
    existing_slots = {
        (s, f): _values(b[f]) for s, f, b in _iter_secret_slots(existing)
    }
    out = copy.deepcopy(incoming)
    try:
        for section, field, block in _iter_secret_slots(out):
            values = _values(block[field])
            if values and values == existing_slots.get((section, field)):
                block[field], _ = _encrypt_slot(block[field])
    except Exception:
        return incoming  # no usable encryption key: validation will reject
    return out


def _encrypt_slot(value: Any) -> Tuple[Any, int]:
    if isinstance(value, str):
        if value.strip() and not is_encrypted(value):
            return encrypt_value(value, OVERRIDE_CONTEXT), 1
        return value, 0
    if isinstance(value, list):
        new, n = [], 0
        for item in value:
            if isinstance(item, str) and item.strip() and not is_encrypted(item):
                new.append(encrypt_value(item, OVERRIDE_CONTEXT))
                n += 1
            else:
                new.append(item)
        return new, n
    return value, 0


def validate_incoming_override_secrets(
    incoming: Optional[Dict], existing: Optional[Dict]
) -> None:
    """Reject new secrets. Existing encrypted legacy values may pass unchanged."""
    assert_no_plaintext_override_secrets(incoming)

    def plain(values: List[str]) -> Optional[List[str]]:
        try:
            return [decrypt_value(v, OVERRIDE_CONTEXT) for v in values]
        except Exception:
            return None  # undecryptable/tampered: never equal to anything stored

    existing_slots = {
        (s, f): plain(_values(b[f])) for s, f, b in _iter_secret_slots(existing)
    }
    for section, field, block in _iter_secret_slots(incoming):
        values = _values(block[field])
        # An envelope is only acceptable if it decrypts and carries exactly the
        # secret already stored for this slot (the unchanged legacy value).
        decrypted = plain(values) if values else []
        if values and (
            decrypted is None or decrypted != existing_slots.get((section, field))
        ):
            raise WorkflowSecretError(
                "API keys cannot be stored in workflow settings "
                f"(model_overrides.{section}.{field}). Save the key once in "
                "Model Configuration; workflows use it automatically."
            )


def encrypt_override_secrets(
    configurations: Optional[Dict],
) -> Tuple[Optional[Dict], int]:
    """Encrypt legacy plaintext override secrets in place (migration only)."""
    if not configurations:
        return configurations, 0
    out = copy.deepcopy(configurations)
    changed = 0
    for _section, field, block in _iter_secret_slots(out):
        block[field], n = _encrypt_slot(block[field])
        changed += n
    return out, changed


def reencrypt_override_secrets(
    configurations: Optional[Dict],
) -> Tuple[Optional[Dict], int]:
    """Encrypt legacy plaintext and rotate old-key override secrets (maintenance)."""
    from api.services.configuration.at_rest import reencrypt_value
    from api.utils.crypto import _load_keys

    if not configurations:
        return configurations, 0
    keys, primary = _load_keys()
    if not keys:
        raise ValueError("No encryption key is configured.")
    out = copy.deepcopy(configurations)
    changed = 0
    for _section, field, block in _iter_secret_slots(out):
        value = block[field]
        if isinstance(value, str) and value.strip():
            block[field], did = reencrypt_value(value, OVERRIDE_CONTEXT, primary)
            changed += int(did)
        elif isinstance(value, list):
            new = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    item, did = reencrypt_value(item, OVERRIDE_CONTEXT, primary)
                    changed += int(did)
                new.append(item)
            block[field] = new
    return out, changed


def decrypt_model_override_secrets(model_overrides: Optional[Dict]) -> Optional[Dict]:
    """Return overrides with legacy encrypted secrets decrypted for runtime use."""
    if not isinstance(model_overrides, dict):
        return model_overrides
    out = copy.deepcopy(model_overrides)
    for section in OVERRIDE_SECTIONS:
        block = out.get(section)
        if not isinstance(block, dict):
            continue
        for field in SECRET_FIELDS:
            value = block.get(field)
            if isinstance(value, str):
                block[field] = decrypt_value(value, OVERRIDE_CONTEXT)
            elif isinstance(value, list):
                block[field] = [
                    decrypt_value(v, OVERRIDE_CONTEXT) if isinstance(v, str) else v
                    for v in value
                ]
    return out
