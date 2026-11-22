"""Emergency disaster-recovery utility to decrypt telephony credentials back to plaintext.

CRITICAL SAFETY DIRECTIVE:
This utility must NEVER be run automatically.
It requires explicit owner authorization flags (--emergency-decrypt and --confirm-i-understand-risk).

Usage:
    python -m api.scripts.emergency_decrypt_telephony_credentials --dry-run
    python -m api.scripts.emergency_decrypt_telephony_credentials --emergency-decrypt --confirm-i-understand-risk
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from loguru import logger
from sqlalchemy import select, update

from api.db import db_client
from api.db.models import TelephonyConfigurationModel
from api.services.telephony.crypto_helper import (
    decrypt_provider_credentials,
    get_sensitive_fields_for_provider,
)
from api.utils.crypto import is_encrypted


async def run_emergency_decryption(
    commit: bool = False, session_factory=None
) -> dict:
    mode_str = "COMMIT" if commit else "DRY-RUN"
    logger.warning(f"Starting EMERGENCY telephony credentials decryption in [{mode_str}] mode...")

    factory = session_factory or db_client.async_session
    stats = {
        "total_configs": 0,
        "already_plaintext": 0,
        "decrypted": 0,
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

                encrypted_fields = [f for f in sensitive_fields if is_encrypted(creds.get(f))]
                if not encrypted_fields:
                    stats["already_plaintext"] += 1
                    continue

                try:
                    decrypted_creds = decrypt_provider_credentials(provider, creds)
                except Exception as exc:
                    logger.error(
                        f"Config ID {row.id} ({provider}): Decryption failed: {exc}"
                    )
                    stats["errors"] += 1
                    continue

                if commit:
                    upd_result = await session.execute(
                        update(TelephonyConfigurationModel)
                        .where(TelephonyConfigurationModel.id == row.id)
                        .values(credentials=decrypted_creds)
                    )
                    if upd_result.rowcount != 1:
                        raise RuntimeError(
                            f"Update rowcount mismatch for config ID {row.id}: expected 1, got {upd_result.rowcount}"
                        )

                stats["decrypted"] += 1
                logger.warning(
                    f"Config ID {row.id} ({provider}): {'Restored to plaintext' if commit else 'Would restore to plaintext'} "
                    f"{len(encrypted_fields)} sensitive field(s)."
                )

            if stats["errors"] > 0:
                logger.error(f"Emergency decryption encountered {stats['errors']} error(s). Rolling back transaction.")
                await session.rollback()
                return stats

            if not commit:
                logger.info("Dry-run complete. Rolling back transaction (zero database writes).")
                await session.rollback()
            else:
                logger.warning("Commit mode: emergency decryption committed to database.")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Emergency telephony credentials decryption.")
    parser.add_argument(
        "--emergency-decrypt",
        action="store_true",
        help="Acknowledge emergency decryption procedure.",
    )
    parser.add_argument(
        "--confirm-i-understand-risk",
        action="store_true",
        help="Explicit confirmation that plaintext secrets will be written to database.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate emergency decryption without modifying database.",
    )
    args = parser.parse_args()

    if args.emergency_decrypt and not args.confirm_i-understand_risk and not args.dry_run:
        print("ERROR: --confirm-i-understand-risk is required for live emergency decryption.")
        sys.exit(1)

    commit = args.emergency_decrypt and args.confirm_i_understand_risk and not args.dry_run

    result = asyncio.run(run_emergency_decryption(commit=commit))
    if result.get("errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()

