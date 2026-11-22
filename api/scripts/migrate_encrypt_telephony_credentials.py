"""Idempotent database migration script to encrypt sensitive telephony credentials at rest.

Usage:
    python -m api.scripts.migrate_encrypt_telephony_credentials --dry-run
    python -m api.scripts.migrate_encrypt_telephony_credentials --commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional
from loguru import logger
from sqlalchemy import select, update

from api.db import db_client
from api.db.models import TelephonyConfigurationModel
from api.services.telephony.crypto_helper import (
    decrypt_provider_credentials,
    encrypt_provider_credentials,
    get_sensitive_fields_for_provider,
    is_all_sensitive_encrypted,
)
from api.utils.crypto import is_encrypted


async def run_migration(
    commit: bool = False, session_factory=None
) -> dict:
    """Run credential encryption migration.
    
    Args:
        commit: If True, writes changes to the database. If False, runs in dry-run mode.
        session_factory: Optional alternative session factory for testing with synthetic fixtures.
        
    Returns:
        dict with migration summary counts.
    """
    mode_str = "COMMIT" if commit else "DRY-RUN"
    logger.info(f"Starting telephony credentials encryption migration in [{mode_str}] mode...")

    factory = session_factory or db_client.async_session
    stats = {
        "total_configs": 0,
        "already_encrypted": 0,
        "migrated": 0,
        "errors": 0,
    }

    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                select(TelephonyConfigurationModel).order_by(TelephonyConfigurationModel.id)
            )
            rows = list(result.scalars().all())
            stats["total_configs"] = len(rows)

            for row in rows:
                provider = row.provider
                creds = dict(row.credentials or {})

                try:
                    sensitive_fields = get_sensitive_fields_for_provider(provider)
                except Exception as exc:
                    logger.error(
                        f"Config ID {row.id} ({provider}): Failed to resolve provider fields: {exc}"
                    )
                    stats["errors"] += 1
                    continue

                if is_all_sensitive_encrypted(provider, creds):
                    stats["already_encrypted"] += 1
                    logger.debug(f"Config ID {row.id} ({provider}): All sensitive fields already encrypted.")
                    continue

                # Encrypt sensitive fields
                try:
                    encrypted_creds = encrypt_provider_credentials(provider, creds)
                    # Verify round-trip decryption parity in memory before writing
                    decrypted_verification = decrypt_provider_credentials(provider, encrypted_creds)

                    for sf in sensitive_fields:
                        orig_val = creds.get(sf)
                        dec_val = decrypted_verification.get(sf)
                        if orig_val is not None and str(orig_val) != str(dec_val):
                            raise ValueError(f"Round-trip validation mismatch on field '{sf}'")

                except Exception as exc:
                    logger.error(
                        f"Config ID {row.id} ({provider}): Encryption/verification failed: {exc}"
                    )
                    stats["errors"] += 1
                    continue

                if commit:
                    # Execute row update with exact matching
                    upd_result = await session.execute(
                        update(TelephonyConfigurationModel)
                        .where(TelephonyConfigurationModel.id == row.id)
                        .values(credentials=encrypted_creds)
                    )
                    if upd_result.rowcount != 1:
                        raise RuntimeError(
                            f"Update rowcount mismatch for config ID {row.id}: expected 1, got {upd_result.rowcount}"
                        )

                stats["migrated"] += 1
                logger.info(
                    f"Config ID {row.id} ({provider}): {'Encrypted' if commit else 'Would encrypt'} "
                    f"{len([f for f in sensitive_fields if f in creds])} sensitive field(s)."
                )

            if stats["errors"] > 0:
                logger.error(f"Migration encountered {stats['errors']} error(s). Rolling back transaction.")
                await session.rollback()
                return stats

            if not commit:
                logger.info("Dry-run complete. Rolling back transaction (zero database writes).")
                await session.rollback()
            else:
                logger.info("Commit mode: all row updates verified. Transaction committed.")

    logger.info(f"Migration summary [{mode_str}]: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Telephony credentials encryption migration.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply changes to the database (defaults to dry-run if omitted).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without modifying the database.",
    )
    args = parser.parse_args()

    commit = args.commit and not args.dry_run

    result = asyncio.run(run_migration(commit=commit))
    if result.get("errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

