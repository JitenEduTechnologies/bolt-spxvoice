"""Safe Idempotent Business Action Execution Controller for SPX Voice.

Prevents duplicate appointments, duplicate COD orders, repeated tool calls,
and race conditions during customer turns, barge-in, or worker timeouts.
Backed by PostgreSQL table `action_idempotency_records`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine, Dict, Optional, Tuple
from loguru import logger
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import ActionIdempotencyRecordModel


def generate_idempotency_key(
    organization_id: int,
    action_type: str,
    unique_identifiers: Dict[str, Any],
) -> str:
    """Generates a stable, collision-resistant idempotency key."""
    serialized = json.dumps(unique_identifiers, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return f"{organization_id}:{action_type}:{digest}"


class ActionSafeguardsController:
    """Manages idempotent execution of business actions with retry safety."""

    async def execute_idempotent_action(
        self,
        action_type: str,
        idempotency_key: str,
        organization_id: int,
        workflow_run_id: Optional[int],
        action_payload: Dict[str, Any],
        action_handler: Callable[[], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> Tuple[bool, Dict[str, Any]]:
        """Executes a business action idempotently.

        Returns:
            Tuple of (is_new_execution: bool, result: Dict[str, Any])
        """
        async with db_client.async_session() as session:
            stmt = select(ActionIdempotencyRecordModel).where(
                ActionIdempotencyRecordModel.idempotency_key == idempotency_key
            )
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()

            if existing:
                if existing.status == "COMPLETED":
                    logger.info(
                        f"[Action Safeguard] Returning cached idempotent result for {action_type} (key: {idempotency_key})"
                    )
                    return False, existing.result or {}
                elif existing.status == "IN_PROGRESS":
                    logger.warning(
                        f"[Action Safeguard] Action {action_type} is already in progress (key: {idempotency_key})"
                    )
                    return False, {
                        "status": "in_progress",
                        "message": "Action is already being processed. Duplicate execution blocked.",
                    }

            # Create in-progress record
            record = ActionIdempotencyRecordModel(
                idempotency_key=idempotency_key,
                organization_id=organization_id,
                workflow_run_id=workflow_run_id,
                action_type=action_type,
                action_payload=action_payload,
                status="IN_PROGRESS",
            )
            session.add(record)
            await session.commit()

        # Execute handler safely
        try:
            result = await action_handler()
            async with db_client.async_session() as session:
                stmt = select(ActionIdempotencyRecordModel).where(
                    ActionIdempotencyRecordModel.idempotency_key == idempotency_key
                )
                res = await session.execute(stmt)
                rec = res.scalar_one_or_none()
                if rec:
                    rec.status = "COMPLETED"
                    rec.result = result
                    await session.commit()
            logger.info(f"[Action Safeguard] Action {action_type} completed successfully (key: {idempotency_key})")
            return True, result
        except Exception as e:
            logger.error(f"[Action Safeguard] Action {action_type} failed: {e}")
            async with db_client.async_session() as session:
                stmt = select(ActionIdempotencyRecordModel).where(
                    ActionIdempotencyRecordModel.idempotency_key == idempotency_key
                )
                res = await session.execute(stmt)
                rec = res.scalar_one_or_none()
                if rec:
                    rec.status = "FAILED"
                    rec.result = {"error": str(e), "failed_at": datetime.now(UTC).isoformat()}
                    await session.commit()
            raise e


action_safeguards = ActionSafeguardsController()

