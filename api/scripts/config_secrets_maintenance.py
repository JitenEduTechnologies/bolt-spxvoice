"""Encrypt / rotate stored provider secrets.

Brings every stored secret onto the primary encryption key:
  * user_configurations.configuration  (owner-bound, context "user:<id>")
  * workflows.workflow_configurations and
    workflow_definitions.workflow_configurations  (legacy model_overrides keys)
  * node credentials inside workflows.workflow_definition and
    workflow_definitions.workflow_json (qa_api_key, integration sensitive
    fields), bound to the workflow's organization

Plaintext values are encrypted; values under an older key id are re-encrypted
with the primary key; every envelope is verified to decrypt first. Dry run by
default; ``--apply`` writes. Output never contains secrets or ciphertext, only
table, row id and counts.

Run from the repository root with the same environment as the API, e.g.:
    python -m api.scripts.config_secrets_maintenance            # dry run
    python -m api.scripts.config_secrets_maintenance --apply

Rotation: put the new key FIRST in TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS
("k2:<new>,k1:<old>"), run with --apply, then drop the old key once the run
reports zero remaining changes and a second dry run is clean.
Exit code 1 if any row could not be processed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from api.db import db_client
from api.db.models import (
    UserConfigurationModel,
    WorkflowDefinitionModel,
    WorkflowModel,
)
from api.services.configuration.at_rest import (
    ConfigDecryptionError,
    reencrypt_configuration_secrets,
)
from api.services.configuration.node_secrets import reencrypt_node_secrets
from api.services.configuration.override_secrets import reencrypt_override_secrets


async def run(apply: bool) -> int:
    failures = 0
    totals = {"user_configurations": 0, "workflows": 0, "workflow_definitions": 0}

    async with db_client.async_session() as session:
        rows = (await session.execute(select(UserConfigurationModel))).scalars().all()
        for row in rows:
            try:
                new, changed = reencrypt_configuration_secrets(
                    row.configuration or {}, context=f"user:{row.user_id}"
                )
            except ConfigDecryptionError as exc:
                failures += 1
                print(f"FAIL user_configurations id={row.id}: {exc.reason}")
                continue
            if changed:
                totals["user_configurations"] += changed
                if apply:
                    row.configuration = new

        workflows = (await session.execute(select(WorkflowModel))).scalars().all()
        org_of = {w.id: w.organization_id for w in workflows}
        definitions = (
            (await session.execute(select(WorkflowDefinitionModel))).scalars().all()
        )
        for label, rows, json_attr, org in (
            ("workflows", workflows, "workflow_definition", lambda r: r.organization_id),
            ("workflow_definitions", definitions, "workflow_json", lambda r: org_of.get(r.workflow_id)),
        ):
            for row in rows:
                try:
                    new_cfg, c1 = reencrypt_override_secrets(row.workflow_configurations)
                    new_json, c2 = reencrypt_node_secrets(getattr(row, json_attr), org(row))
                except ConfigDecryptionError as exc:
                    failures += 1
                    print(f"FAIL {label} id={row.id}: {exc.reason}")
                    continue
                if c1 or c2:
                    totals[label] += c1 + c2
                    if apply:
                        if c1:
                            row.workflow_configurations = new_cfg
                        if c2:
                            setattr(row, json_attr, new_json)

        if apply and not failures:
            await session.commit()
        else:
            await session.rollback()

    verb = "applied" if apply and not failures else "would change"
    for table, count in totals.items():
        print(f"{table}: {count} secret value(s) {verb}")
    if failures:
        print(f"{failures} row(s) failed; nothing was written.")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
