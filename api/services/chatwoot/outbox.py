from __future__ import annotations

import copy

"""Minimal Chatwoot / WhatsApp Asynchronous Outbox Worker (Phase 5 Workstream G).



Mandatory Architectural Safeguards:

- Strict non-blocking design: Voice workers enqueue an outbox job and continue immediately.

  The live voice/telephony pipeline NEVER awaits Chatwoot HTTP calls.

- Durable persistence: Outbox jobs are committed to PostgreSQL table `chatwoot_outbox_jobs`.

- Idempotency: Unique `idempotency_key` prevents duplicate message delivery.

- Opt-In & Compliance: Verifies recipient phone before enqueueing.

- Multi-tenant isolation: Scoped to organization_id.

- No real WhatsApp messages: Uses local safe mock transport unless live credentials are verified.

"""






import asyncio

import uuid

from datetime import UTC, datetime, timedelta

from typing import Any, Dict, List, Optional



from loguru import logger

from sqlalchemy import and_, or_, select, update



from api.db import db_client

from api.db.models import ChatwootOutboxJobModel

from api.services.telephony.compliance import compliance_manager, normalize_phone_number





class ChatwootOutboxService:

    """Manages asynchronous outbox queue and background delivery to Chatwoot."""



    def __init__(self, is_mock_transport: bool = True):

        self.is_mock_transport = is_mock_transport

        self.mock_sent_messages: List[Dict[str, Any]] = []
        self._worker_task: Optional[asyncio.Task] = None
        self._running: bool = False



    async def enqueue_message(

        self,

        organization_id: int,

        recipient_phone: str,

        message_type: str,

        payload: Dict[str, Any],

        workflow_run_id: Optional[int] = None,

        idempotency_key: Optional[str] = None,

    ) -> Dict[str, Any]:

        """Durably enqueues an asynchronous WhatsApp outbox job.



        Non-blocking: commits to DB and dispatches background task; returns immediately.

        """

        norm_phone = normalize_phone_number(recipient_phone)



        # Check DND suppression

        is_suppressed, reason = await compliance_manager.is_suppressed_db(

            organization_id, norm_phone

        )

        if is_suppressed:

            logger.warning(

                f"[Chatwoot Outbox] Message to {norm_phone} blocked by suppression: {reason}"

            )

            return {

                "status": "suppressed",

                "reason": f"dnc_suppressed: {reason}",

                "job_id": None,

            }



        if not idempotency_key:

            idempotency_key = f"cw_{organization_id}_{uuid.uuid4().hex[:16]}"



        now = datetime.now(UTC)



        async with db_client.async_session() as session:

            # Check for existing idempotent job

            stmt_existing = select(ChatwootOutboxJobModel).where(

                ChatwootOutboxJobModel.idempotency_key == idempotency_key

            )

            res = await session.execute(stmt_existing)

            existing_job = res.scalar_one_or_none()

            if existing_job:

                logger.info(

                    f"[Chatwoot Outbox] Idempotent duplicate job {existing_job.id} detected; returning existing record"

                )

                return {

                    "status": existing_job.status,

                    "job_id": existing_job.id,

                    "idempotency_key": existing_job.idempotency_key,

                    "recipient_phone": existing_job.recipient_phone,

                    "message_type": existing_job.message_type,

                    "is_duplicate": True,

                }



            # Create new outbox job

            new_job = ChatwootOutboxJobModel(

                organization_id=organization_id,

                workflow_run_id=workflow_run_id,

                message_type=message_type,

                recipient_phone=norm_phone,

                payload=payload,

                status="QUEUED",

                idempotency_key=idempotency_key,

                attempts=0,

                created_at=now,

            )

            session.add(new_job)

            await session.commit()

            await session.refresh(new_job)

            job_id = new_job.id



        logger.info(

            f"[Chatwoot Outbox] Enqueued job {job_id} ({message_type}) for {norm_phone} (Org {organization_id})"

        )



        # Non-blocking async background dispatch (fire-and-forget for voice worker)

        asyncio.create_task(self._deliver_job_background(job_id))



        return {

            "status": "QUEUED",

            "job_id": job_id,

            "idempotency_key": idempotency_key,

            "recipient_phone": norm_phone,

            "message_type": message_type,

            "is_duplicate": False,

        }



    async def _deliver_job_background(self, job_id: int) -> None:

        """Background worker process executing the delivery."""

        try:

            await self.process_outbox_job(job_id)

        except Exception as e:

            logger.error(f"[Chatwoot Outbox] Background delivery failed for job {job_id}: {e}")



    async def process_outbox_job(self, job_id: int, already_claimed: bool = False) -> Dict[str, Any]:

        """Processes a single outbox job with simulated/mock transport."""

        async with db_client.async_session() as session:

            stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)

            res = await session.execute(stmt)

            job = res.scalar_one_or_none()

            if not job:

                raise ValueError(f"Outbox job {job_id} not found")



            # Capture attributes eagerly before commit to avoid lazy loading outside greenlet

            rec_phone = job.recipient_phone

            msg_type = job.message_type

            msg_payload = copy.deepcopy(job.payload) if job.payload else {}



            if not already_claimed:
                job.attempts += 1
                job.status = "SENDING"

            # 1. Resolve delivery provider explicitly (Rule 3: Never silently fall back)
            provider = msg_payload.get("provider") if isinstance(msg_payload, dict) else None
            if not provider:
                org_config = await db_client.get_configuration(job.organization_id, "messaging_provider")
                if org_config and org_config.value and isinstance(org_config.value, dict):
                    provider = org_config.value.get("provider")

            # Default if unconfigured: "chatwoot" (preserves existing Phase 5 behavior 100%)
            if not provider:
                provider = "chatwoot"

            try:
                if provider == "meta_cloud":
                    # Explicit direct Meta WhatsApp Cloud API adapter
                    from api.services.whatsapp.meta_adapter import meta_whatsapp_adapter
                    from api.services.whatsapp.exceptions import (
                        MetaRateLimitError,
                        MetaTransientNetworkError,
                        MetaWhatsAppError,
                    )
                    try:
                        res = await meta_whatsapp_adapter.deliver_job(job, session)
                        await session.commit()
                        return res
                    except (MetaTransientNetworkError, MetaRateLimitError) as retryable_err:
                        job.status = "FAILED"
                        job.last_error = f"[MetaCloud:Retryable] {retryable_err}"
                        job.processed_at = datetime.now(UTC)
                        await session.commit()
                        logger.warning(
                            f"[Messaging Outbox] Job {job_id} Meta delivery transient error (attempt {job.attempts}): {retryable_err}"
                        )
                        return {"job_id": job_id, "status": "FAILED", "error": f"[MetaCloud:Retryable] {retryable_err}", "retryable": True}
                    except MetaWhatsAppError as non_retryable_err:
                        # Non-retryable Meta failure (NEVER silently fall back to Chatwoot or mock)
                        job.status = "FAILED"
                        job.last_error = f"[MetaCloud:Fatal] {non_retryable_err}"
                        job.processed_at = datetime.now(UTC)
                        await session.commit()
                        logger.warning(
                            f"[Messaging Outbox] Job {job_id} Meta delivery fatal error: {non_retryable_err}"
                        )
                        return {"job_id": job_id, "status": "FAILED", "error": f"[MetaCloud:Fatal] {non_retryable_err}", "retryable": False}

                elif provider == "chatwoot":
                    if self.is_mock_transport:
                        # Simulated local transport
                        delivered_record = {
                            "job_id": job_id,
                            "recipient_phone": rec_phone,
                            "message_type": msg_type,
                            "payload": msg_payload,
                            "delivered_at": datetime.now(UTC).isoformat(),
                        }
                        self.mock_sent_messages.append(delivered_record)
                        job.status = "SENT"
                        job.processed_at = datetime.now(UTC)
                        job.last_error = None
                        await session.commit()
                        logger.info(f"[Chatwoot Outbox] Job {job_id} delivered successfully (MOCK)")
                        return delivered_record
                    else:
                        raise NotImplementedError("Live Chatwoot API disabled in testing phase")
                else:
                    raise ValueError(f"Unsupported messaging provider '{provider}'. Must be 'chatwoot' or 'meta_cloud'.")

            except Exception as e:
                job.status = "FAILED"
                job.last_error = str(e)
                job.processed_at = datetime.now(UTC)
                await session.commit()
                logger.warning(f"[Chatwoot Outbox] Job {job_id} marked FAILED: {e}")
                return {"job_id": job_id, "status": "FAILED", "error": str(e)}



    async def claim_and_process_pending_jobs(
        self,
        batch_size: int = 20,
        max_attempts: int = 3,
        lease_seconds: int = 120,
    ) -> int:
        """Atomically claims and processes pending or timed-out outbox jobs (Gap 3).

        Recovers from server crashes, container restarts, or transient network failures.
        Uses SELECT ... FOR UPDATE SKIP LOCKED to prevent races between workers.
        """
        now = datetime.now(UTC)
        lease_cutoff = now - timedelta(seconds=lease_seconds)
        claimed_ids: List[int] = []

        async with db_client.async_session() as session:
            # 1. Mark exhausted jobs (attempts >= max_attempts) as FAILED
            stmt_exhausted = (
                update(ChatwootOutboxJobModel)
                .where(
                    ChatwootOutboxJobModel.status.in_(["QUEUED", "SENDING"]),
                    ChatwootOutboxJobModel.attempts >= max_attempts,
                )
                .values(
                    status="FAILED",
                    last_error=f"Exceeded max delivery attempts ({max_attempts})",
                    processed_at=now,
                )
            )
            await session.execute(stmt_exhausted)

            # 2. Select eligible jobs (QUEUED or stale SENDING) with FOR UPDATE SKIP LOCKED
            eligible_cond = or_(
                ChatwootOutboxJobModel.status == "QUEUED",
                and_(
                    ChatwootOutboxJobModel.status == "SENDING",
                    or_(
                        ChatwootOutboxJobModel.processed_at.is_(None),
                        ChatwootOutboxJobModel.processed_at < lease_cutoff,
                    ),
                ),
            )
            stmt = (
                select(ChatwootOutboxJobModel)
                .where(
                    eligible_cond,
                    ChatwootOutboxJobModel.attempts < max_attempts,
                )
                .order_by(ChatwootOutboxJobModel.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            res = await session.execute(stmt)
            claimed_jobs = res.scalars().all()
            for job in claimed_jobs:
                job.status = "SENDING"
                job.processed_at = now
                job.attempts += 1
                claimed_ids.append(job.id)

            await session.commit()

        if not claimed_ids:
            return 0

        # 3. Deliver each claimed job
        processed_count = 0
        for j_id in claimed_ids:
            try:
                await self.process_outbox_job(j_id, already_claimed=True)
                processed_count += 1
            except Exception as e:
                logger.error(f"[Chatwoot Outbox] Error delivering claimed job {j_id}: {e}")

        return processed_count

    async def start_outbox_worker(self, poll_interval_seconds: float = 5.0) -> None:
        """Starts the background outbox recovery loop during application startup."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(
            self._outbox_worker_loop(poll_interval_seconds)
        )
        logger.info("[Chatwoot Outbox] Started background outbox worker loop")

    async def stop_outbox_worker(self) -> None:
        """Stops the background outbox recovery loop during graceful shutdown."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("[Chatwoot Outbox] Stopped background outbox worker loop")

    async def _outbox_worker_loop(self, poll_interval_seconds: float = 5.0) -> None:
        """Continuous recovery loop running in the background of the FastAPI lifespan."""
        logger.info("[Chatwoot Outbox] Outbox worker loop active")
        while self._running:
            try:
                processed = await self.claim_and_process_pending_jobs()
                if processed > 0:
                    logger.debug(f"[Chatwoot Outbox] Processed {processed} recovered outbox jobs")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[Chatwoot Outbox] Unexpected error in recovery loop: {e}")

            try:
                await asyncio.sleep(poll_interval_seconds)
            except asyncio.CancelledError:
                break

    async def list_outbox_jobs(

        self, organization_id: int, limit: int = 50, offset: int = 0

    ) -> List[Dict[str, Any]]:

        """Paginated listing of outbox jobs scoped to an organization."""

        async with db_client.async_session() as session:

            stmt = (

                select(ChatwootOutboxJobModel)

                .where(ChatwootOutboxJobModel.organization_id == organization_id)

                .order_by(ChatwootOutboxJobModel.created_at.desc())

                .offset(offset)

                .limit(limit)

            )

            res = await session.execute(stmt)

            rows = res.scalars().all()

            return [

                {

                    "id": r.id,

                    "organization_id": r.organization_id,

                    "workflow_run_id": r.workflow_run_id,

                    "message_type": r.message_type,

                    "recipient_phone": r.recipient_phone,

                    "payload": r.payload,

                    "status": r.status,

                    "idempotency_key": r.idempotency_key,

                    "attempts": r.attempts,

                    "last_error": r.last_error,

                    "created_at": r.created_at.isoformat() if r.created_at else None,

                    "processed_at": r.processed_at.isoformat() if r.processed_at else None,

                }

                for r in rows

            ]





# Singleton instance

chatwoot_outbox_service = ChatwootOutboxService(is_mock_transport=True)

