from datetime import UTC, datetime
from typing import Any, Dict

from loguru import logger
from sqlalchemy import text

from api.db import db_client
from api.services.campaign.campaign_event_publisher import (
    get_campaign_event_publisher,
)
from api.services.campaign.circuit_breaker import BreakerState, circuit_breaker
from api.tasks.arq import enqueue_job
from api.tasks.function_names import FunctionNames


class CampaignRunnerService:
    """Orchestrates campaign execution"""

    async def start_campaign(self, campaign_id: int) -> None:
        """Entry point - updates state to 'syncing' and enqueues sync task"""
        # Get campaign
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state != "created":
            raise ValueError(
                f"Campaign must be in 'created' state to start, current state: {campaign.state}"
            )

        # Redial campaigns have queued_runs pre-seeded from the parent campaign,
        # so skip source sync and transition straight to 'running'.
        is_redial = bool(
            (campaign.orchestrator_metadata or {}).get("parent_campaign_id")
        )
        if is_redial:
            now = datetime.now(UTC)
            await db_client.update_campaign(
                campaign_id=campaign_id,
                state="running",
                started_at=now,
                source_last_synced_at=now,
            )
            publisher = await get_campaign_event_publisher()
            await publisher.publish_sync_completed(
                campaign_id=campaign_id,
                total_rows=campaign.total_rows or 0,
                source_type=campaign.source_type,
                source_id=campaign.source_id,
            )
            logger.info(f"Redial campaign {campaign_id} started, source sync skipped")
            return

        # Update campaign state to syncing
        await db_client.update_campaign(
            campaign_id=campaign_id,
            state="syncing",
            started_at=datetime.now(UTC),
            source_sync_status="in_progress",
        )

        # Enqueue the sync task
        await enqueue_job(FunctionNames.SYNC_CAMPAIGN_SOURCE, campaign_id)

        logger.info(f"Campaign {campaign_id} started, syncing source data")

    async def pause_campaign(self, campaign_id: int) -> None:
        """Pauses active campaign processing"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state not in ["running", "syncing"]:
            raise ValueError(
                f"Campaign must be in 'running' or 'syncing' state to pause, current state: {campaign.state}"
            )

        # Update state to paused
        await db_client.update_campaign(campaign_id=campaign_id, state="paused")

        logger.info(f"Campaign {campaign_id} paused")

    async def resume_campaign(self, campaign_id: int) -> None:
        """Resumes paused campaign"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state != "paused":
            raise ValueError(
                f"Campaign must be in 'paused' state to resume, current state: {campaign.state}"
            )

        # Safety first: verify the breaker dependency and reset before the
        # campaign is allowed to run again. Any failure keeps it paused.
        cb_state, _ = await circuit_breaker.is_circuit_open(campaign_id)
        if cb_state == BreakerState.UNKNOWN:
            await db_client.append_campaign_log(
                campaign_id=campaign_id,
                level="warning",
                event="circuit_breaker_resume_blocked",
                message=(
                    "Resume blocked: circuit breaker dependency unavailable. "
                    "Campaign remains paused."
                ),
                details={"breaker_state": BreakerState.UNKNOWN.value},
            )
            raise ValueError(
                "Circuit breaker dependency unavailable; campaign remains paused. "
                "Verify Redis health before resuming."
            )

        reset_ok = await circuit_breaker.reset(campaign_id)
        if not reset_ok:
            await db_client.append_campaign_log(
                campaign_id=campaign_id,
                level="warning",
                event="circuit_breaker_resume_blocked",
                message=(
                    "Resume blocked: circuit breaker reset failed. "
                    "Campaign remains paused."
                ),
                details={"breaker_state": BreakerState.UNKNOWN.value},
            )
            raise ValueError(
                "Circuit breaker reset failed; campaign remains paused. "
                "Verify Redis health before resuming."
            )

        # Update state to running. Do not queue batch since campaign orchestrator's
        # stale campaign checker would do that if there are pending work.
        await db_client.update_campaign(campaign_id=campaign_id, state="running")

        logger.info(f"Campaign {campaign_id} resumed")

    async def cancel_campaign(
        self,
        campaign_id: int,
        user_id: int | None = None,
        reason: str = "operator_cancelled",
    ) -> None:
        """Cancels an active or paused campaign and marks pending runs as failed/cancelled."""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        if campaign.state in ["completed"]:
            logger.info(f"Campaign {campaign_id} is already completed/cancelled")
            return

        now = datetime.now(UTC)
        cancellation_info = {
            "cancellation_reason": reason,
            "cancelled_at": now.isoformat(),
            "cancelled_by_user_id": user_id,
        }
        meta = dict(campaign.orchestrator_metadata or {})
        meta.update(cancellation_info)

        # Atomically update campaign state and cancel all pending/in-flight runs in one transaction
        async with db_client.async_session() as session:
            from sqlalchemy import update
            from api.db.models import CampaignModel, QueuedRunModel

            stmt_campaign = (
                update(CampaignModel)
                .where(CampaignModel.id == campaign_id)
                .values(
                    state="completed",
                    completed_at=now,
                    orchestrator_metadata=meta,
                )
            )
            await session.execute(stmt_campaign)

            stmt_runs = (
                update(QueuedRunModel)
                .where(
                    QueuedRunModel.campaign_id == campaign_id,
                    QueuedRunModel.state.in_(["queued", "processing"]),
                )
                .values(
                    state="failed",
                    retry_reason="campaign_cancelled",
                    processed_at=now,
                )
            )
            await session.execute(stmt_runs)
            await session.commit()

        await db_client.append_campaign_log(
            campaign_id=campaign_id,
            level="warning",
            event="campaign_cancelled",
            message="Campaign cancelled by operator; no further calls will be dispatched.",
            details=cancellation_info,
        )
        logger.info(f"Campaign {campaign_id} cancelled by user={user_id}")

    async def get_campaign_status(self, campaign_id: int) -> Dict[str, Any]:
        """Returns detailed campaign status"""
        campaign = await db_client.get_campaign_by_id(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")

        # Count failed calls from workflow runs
        failed_calls = await self._count_failed_campaign_calls(campaign_id)

        effective_state = campaign.state
        meta = campaign.orchestrator_metadata or {}
        if campaign.state == "completed" and (
            meta.get("cancellation_reason")
            or any(
                entry.get("event") == "campaign_cancelled"
                for entry in (campaign.logs or [])
                if isinstance(entry, dict)
            )
        ):
            effective_state = "cancelled"

        return {
            "campaign_id": campaign_id,
            "state": effective_state,
            "total_rows": campaign.total_rows or 0,
            "processed_rows": campaign.processed_rows,
            "failed_calls": failed_calls,
            "progress_percentage": (
                (campaign.processed_rows / campaign.total_rows * 100)
                if campaign.total_rows and campaign.total_rows > 0
                else 0
            ),
            "source_sync": {
                "status": campaign.source_sync_status,
                "last_synced_at": campaign.source_last_synced_at,
                "error": campaign.source_sync_error,
            },
            "rate_limit": campaign.rate_limit_per_second,
            "started_at": campaign.started_at,
            "completed_at": campaign.completed_at,
        }

    async def _count_failed_campaign_calls(self, campaign_id: int) -> int:
        """Count failed calls by examining workflow_run telephony callbacks"""
        # Get all workflow runs for this campaign
        workflow_runs = await db_client.get_workflow_runs_by_campaign(campaign_id)

        failed_count = 0
        for run in workflow_runs:
            callbacks = run.logs.get("telephony_status_callbacks", [])
            if callbacks:
                # Check final status
                final_status = callbacks[-1].get("status", "").lower()
                if final_status in ["failed", "busy", "no-answer"]:
                    failed_count += 1

        return failed_count


# Global instance
campaign_runner_service = CampaignRunnerService()
