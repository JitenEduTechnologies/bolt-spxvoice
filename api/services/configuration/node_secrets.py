"""Encryption at rest for secrets stored inside workflow node data.

Some node types carry their own credential in ``workflow_json`` (``qa`` ->
``qa_api_key``; integration nodes -> ``IntegrationNodeRegistration
.sensitive_fields``, e.g. tuner -> ``tuner_api_key``). Those values are sealed
with the same Fernet envelope as user configurations (at_rest.py), bound to the
workflow's organization: context ``workflow-node:org:<organization_id>``.

* Sealing happens in the DB layer (WorkflowClient.create_workflow /
  save_workflow_draft), so every writer - routes, MCP tools, business routes,
  duplicate - goes through it. Draft/publish/revert copy rows of the same
  workflow, so envelopes stay valid.
* An incoming envelope is accepted only if it decrypts under the target
  organization (blocks replay of another tenant's ciphertext). A value still
  containing the mask marker is rejected (a placeholder never becomes a key).
* Decryption happens only in the runtime consumer (QA LLM config, tuner
  delivery) via ``open_node_secret``; failures raise ConfigDecryptionError with
  a content-free message.
* Legacy plaintext is encrypted by api/scripts/config_secrets_maintenance.py and
  on the next save. Without an encryption key and without
  REQUIRE_CONFIG_ENCRYPTION=true, values stay plaintext (legacy behaviour).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterator, List, Optional, Tuple

from loguru import logger

from api.services.configuration.at_rest import (
    ConfigDecryptionError,
    ConfigEncryptionRequiredError,
    _require_encryption,
    decrypt_value,
    encrypt_value,
    encryption_available,
    reencrypt_value,
)
from api.utils.crypto import is_encrypted

MASK_MARKER = "***"


class NodeSecretError(ValueError):
    """A node secret cannot be stored as given. Message holds paths only."""


def _secret_fields(node_type: Optional[str]) -> Tuple[str, ...]:
    from api.services.configuration.masking import _secret_fields_for_node_type

    return _secret_fields_for_node_type(node_type)


def node_context(organization_id: Any) -> str:
    return f"workflow-node:org:{organization_id}"


def _slots(workflow_json: Optional[Dict]) -> Iterator[Tuple[str, str, Dict]]:
    if not isinstance(workflow_json, dict):
        return
    for node in workflow_json.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        for field in _secret_fields(node.get("type")):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                yield str(node.get("id", "?")), field, data


def find_plaintext_node_secrets(workflow_json: Optional[Dict]) -> List[str]:
    return [
        f"nodes[{nid}].data.{field}"
        for nid, field, data in _slots(workflow_json)
        if not is_encrypted(data[field])
    ]


def seal_workflow_node_secrets(
    workflow_json: Optional[Dict], organization_id: Any
) -> Optional[Dict]:
    """Return a copy with every node secret encrypted for *organization_id*."""
    if not isinstance(workflow_json, dict):
        return workflow_json
    ctx = node_context(organization_id)
    out = copy.deepcopy(workflow_json)
    can_encrypt = encryption_available()
    warned = False
    for nid, field, data in _slots(out):
        value = data[field]
        path = f"nodes[{nid}].data.{field}"
        if MASK_MARKER in value:
            raise NodeSecretError(
                f"{path} still holds a masked placeholder; re-enter the key."
            )
        if is_encrypted(value):
            try:
                decrypt_value(value, ctx)
            except ConfigDecryptionError:
                raise NodeSecretError(
                    f"{path} holds a credential that does not belong to this "
                    "organization or cannot be read; re-enter the key."
                ) from None
            continue
        if can_encrypt:
            data[field] = encrypt_value(value, ctx)
        elif _require_encryption():
            raise ConfigEncryptionRequiredError(
                "Credential encryption is required but no encryption key is "
                "configured; refusing to store node credentials in plaintext."
            )
        elif not warned:
            warned = True
            logger.warning(
                "No config encryption key is configured: workflow node "
                "credentials are being stored in plaintext."
            )
    return out


def open_node_secret(value: Optional[str], organization_id: Any) -> Optional[str]:
    """Decrypt one node secret for its runtime consumer (legacy plaintext passes)."""
    if not value:
        return value
    return decrypt_value(value, node_context(organization_id))


def reencrypt_node_secrets(
    workflow_json: Optional[Dict], organization_id: Any
) -> Tuple[Optional[Dict], int]:
    """Encrypt legacy plaintext and rotate old-key node secrets (maintenance)."""
    from api.utils.crypto import _load_keys

    if not isinstance(workflow_json, dict):
        return workflow_json, 0
    keys, primary = _load_keys()
    if not keys:
        raise ConfigEncryptionRequiredError("No encryption key is configured.")
    ctx = node_context(organization_id)
    out = copy.deepcopy(workflow_json)
    changed = 0
    for _nid, field, data in _slots(out):
        if MASK_MARKER in data[field]:
            continue  # not a key; nothing to protect
        data[field], did = reencrypt_value(data[field], ctx, primary)
        changed += int(did)
    return out, changed
