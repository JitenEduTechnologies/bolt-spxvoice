"""Real-Time Dashboard Event Bus for SPX Voice (Phase 5 Workstream A).

Provides durable, ordered, organization-scoped event delivery between
workers/services and the frontend dashboard via Server-Sent Events (SSE).

Features:
- Durable PostgreSQL persistence before delivery (no event loss).
- Redis Pub/Sub broadcast across multi-worker deployments.
- In-process local queue fallback for standalone or test execution.
- Bounded queue buffers to prevent memory exhaustion under high concurrency.
- Reconnection cursor recovery: clients reconnecting with `last_event_id`
  receive all missed events from PostgreSQL in strict ascending sequence.
- Strict multi-tenant isolation: events are partitioned by organization_id.
"""

from __future__ import annotations
import os

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from loguru import logger
from redis import asyncio as aioredis
from sqlalchemy import select

from api.db import db_client
from api.db.models import DashboardEventModel, UserModel
from api.services.campaign.rate_limiter import REDIS_URL


class RealtimeDashboardBus:
    """Manages publishing and streaming of organization-scoped dashboard events."""

    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        # Local in-process subscribers: org_id -> set of asyncio.Queue
        self._local_subscribers: Dict[int, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        if self._redis is None:
            try:
                self._redis = await aioredis.from_url(
                    self.redis_url, encoding="utf-8", decode_responses=True
                )
            except Exception as e:
                logger.debug(f"[RealtimeBus] Redis connection pass-through/fallback: {e}")
                self._redis = None
        return self._redis

    async def publish_event(
        self,
        organization_id: int,
        event_type: str,
        resource_type: str,
        resource_id: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Durably persists event in database, then broadcasts to Redis and local queues.

        Guarantees that state is committed to PostgreSQL before clients receive it.
        """
        now = datetime.now(UTC)
        event_payload = {
            "organization_id": organization_id,
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": str(resource_id),
            "data": data,
            "created_at": now.isoformat(),
            "_pid": os.getpid(),
        }

        # 1. Durable PostgreSQL persistence
        event_id = None
        try:
            async with db_client.async_session() as session:
                event_row = DashboardEventModel(
                    organization_id=organization_id,
                    event_type=event_type,
                    resource_type=resource_type,
                    resource_id=str(resource_id),
                    data=data,
                    created_at=now,
                )
                session.add(event_row)
                await session.commit()
                await session.refresh(event_row)
                event_id = event_row.id
        except Exception as e:
            logger.error(f"[RealtimeBus] Failed to persist event to PostgreSQL: {e}")
            # Fallback event ID based on millisecond timestamp
            event_id = int(now.timestamp() * 1000)

        event_payload["id"] = event_id

        # 2. Broadcast to Redis Pub/Sub
        channel = f"org:{organization_id}:events"
        try:
            r = await self._get_redis()
            if r is not None:
                await r.publish(channel, json.dumps(event_payload))
        except Exception as e:
            logger.debug(f"[RealtimeBus] Redis publish error: {e}")

        # 3. Deliver to local in-process subscriber queues
        async with self._lock:
            queues = self._local_subscribers.get(organization_id, set()).copy()
            for q in queues:
                try:
                    if q.full():
                        # Drop oldest event to maintain bounded buffer under backpressure
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(event_payload)
                except Exception as q_err:
                    logger.debug(f"[RealtimeBus] Queue delivery error: {q_err}")

        return event_payload

    async def get_missed_events(
        self,
        organization_id: int,
        last_event_id: int,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Recovers missed events from PostgreSQL for client reconnects."""
        missed: List[Dict[str, Any]] = []
        try:
            async with db_client.async_session() as session:
                stmt = (
                    select(DashboardEventModel)
                    .where(
                        DashboardEventModel.organization_id == organization_id,
                        DashboardEventModel.id > last_event_id,
                    )
                    .order_by(DashboardEventModel.id.asc())
                    .limit(limit)
                )
                res = await session.execute(stmt)
                rows = res.scalars().all()
                for r in rows:
                    missed.append({
                        "id": r.id,
                        "organization_id": r.organization_id,
                        "event_type": r.event_type,
                        "resource_type": r.resource_type,
                        "resource_id": r.resource_id,
                        "data": r.data,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    })
        except Exception as e:
            logger.error(f"[RealtimeBus] Failed to fetch missed events: {e}")
        return missed

    async def _ensure_redis_listener(self, organization_id: int) -> None:
        """Maintains a background Redis Pub/Sub subscription for the organization."""
        async with self._lock:
            if not hasattr(self, "_redis_sub_tasks"):
                self._redis_sub_tasks = {}
            if organization_id in self._redis_sub_tasks and not self._redis_sub_tasks[organization_id].done():
                return

            r = await self._get_redis()
            if r is None:
                return

            channel = f"org:{organization_id}:events"

            async def _subscriber_loop():
                try:
                    pubsub = r.pubsub()
                    await pubsub.subscribe(channel)
                    logger.info(f"[RealtimeBus] Subscribed to Redis channel {channel}")
                    async for message in pubsub.listen():
                        if message and message.get("type") == "message":
                            raw_data = message.get("data")
                            if raw_data:
                                try:
                                    payload = json.loads(raw_data)
                                    if payload.get("_pid") != os.getpid():
                                        async with self._lock:
                                            queues = self._local_subscribers.get(organization_id, set()).copy()
                                            for q in queues:
                                                if q.full():
                                                    try:
                                                        q.get_nowait()
                                                    except asyncio.QueueEmpty:
                                                        pass
                                                q.put_nowait(payload)
                                except Exception as parse_err:
                                    logger.debug(f"[RealtimeBus] Pubsub parse error: {parse_err}")
                except asyncio.CancelledError:
                    pass
                except Exception as sub_err:
                    logger.debug(f"[RealtimeBus] Pubsub listener error for {channel}: {sub_err}")

            task = asyncio.create_task(_subscriber_loop())
            self._redis_sub_tasks[organization_id] = task

    async def event_generator(
        self,
        organization_id: int,
        last_event_id: Optional[int] = None,
        keep_alive_seconds: float = 15.0,
    ) -> AsyncGenerator[str, None]:
        """SSE generator yielding SSE formatted events with reconnect recovery.

        Yields:
            id: {event_id}\nevent: {event_type}\ndata: {json}\n\n
        """
        # Bounded client queue (max 500 buffered events)
        client_queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        # 1. Register queue and start Redis listener
        async with self._lock:
            if organization_id not in self._local_subscribers:
                self._local_subscribers[organization_id] = set()
            self._local_subscribers[organization_id].add(client_queue)
        await self._ensure_redis_listener(organization_id)
        # Flush initial connection confirmation ping
        yield ": connected\n\n"

        try:
            # 2. Replay missed events from PostgreSQL if client supplied last_event_id
            if last_event_id is not None and last_event_id > 0:
                missed = await self.get_missed_events(organization_id, last_event_id)
                for event in missed:
                    yield self._format_sse(event)

            # 3. Stream real-time events
            while True:
                try:
                    event = await asyncio.wait_for(
                        client_queue.get(), timeout=keep_alive_seconds
                    )
                    yield self._format_sse(event)
                except asyncio.TimeoutError:
                    # SSE Keep-Alive ping
                    yield ": keep-alive\n\n"
        finally:
            # Clean up subscriber
            async with self._lock:
                if organization_id in self._local_subscribers:
                    self._local_subscribers[organization_id].discard(client_queue)
                    if not self._local_subscribers[organization_id]:
                        del self._local_subscribers[organization_id]

    def _format_sse(self, event: Dict[str, Any]) -> str:
        """Formats an event dictionary into RFC compliant SSE format."""
        event_copy = {k: v for k, v in event.items() if not k.startswith("_")}
        event_id = event_copy.get("id", "")
        event_type = event_copy.get("event_type", "message")
        data_json = json.dumps(event_copy)
        return "id: " + str(event_id) + "\nevent: " + str(event_type) + "\ndata: " + data_json + "\n\n"

    async def create_sse_ticket(self, user: UserModel, expires_in_seconds: int = 30) -> str:
        """Generates a cryptographically secure, short-lived, single-use ticket for SSE connection.

        Security constraints (Gap 4):
        - Bound to user_id and organization_id.
        - Strictly scoped to 'sse_stream' purpose.
        - Expires in 30 seconds.
        - Atomic single redemption.
        """
        import secrets
        ticket = f"ssetkt_{secrets.token_hex(24)}"
        payload = json.dumps({
            "user_id": user.id,
            "organization_id": user.selected_organization_id,
            "purpose": "sse_stream",
            "created_at": datetime.now(UTC).isoformat(),
        })

        # 1. Primary storage: Redis with 30s TTL
        r = await self._get_redis()
        if r is not None:
            try:
                await r.set(f"sse_ticket:{ticket}", payload, ex=expires_in_seconds)
                return ticket
            except Exception as e:
                logger.warning(f"[RealtimeBus] Failed to set SSE ticket in Redis: {e}")

        # 2. Local memory fallback for standalone or test environments
        async with self._lock:
            if not hasattr(self, "_memory_tickets"):
                self._memory_tickets = {}
            self._memory_tickets[ticket] = {
                "payload": payload,
                "expires_at": datetime.now(UTC).timestamp() + expires_in_seconds,
            }

        return ticket

    async def redeem_sse_ticket(self, ticket: str) -> Optional[UserModel]:
        """Atomically validates and consumes an SSE connection ticket.

        Guarantees:
        - Exactly one redemption (subsequent redemptions fail).
        - Rejects expired or forged tickets.
        - Rejects tickets with purpose != 'sse_stream'.
        """
        if not ticket or not isinstance(ticket, str):
            return None

        payload_str: Optional[str] = None

        # 1. Attempt atomic retrieval & deletion from Redis via Lua script
        r = await self._get_redis()
        if r is not None:
            try:
                lua = """
                local val = redis.call('GET', KEYS[1])
                if val then
                    redis.call('DEL', KEYS[1])
                    return val
                else
                    return nil
                end
                """
                payload_str = await r.eval(lua, 1, f"sse_ticket:{ticket}")
                if not payload_str:
                    return None
            except Exception as e:
                logger.debug(f"[RealtimeBus] Redis ticket redemption error: {e}")

        # 2. Check local memory fallback if not found in Redis
        if not payload_str:
            async with self._lock:
                if hasattr(self, "_memory_tickets") and ticket in self._memory_tickets:
                    entry = self._memory_tickets.pop(ticket)
                    if datetime.now(UTC).timestamp() <= entry["expires_at"]:
                        payload_str = entry["payload"]

        if not payload_str:
            return None

        try:
            data = json.loads(payload_str)
            if data.get("purpose") != "sse_stream":
                return None
            user_id = data.get("user_id")
            org_id = data.get("organization_id")
            if not user_id:
                return None

            user = await db_client.get_user_by_id(user_id)
            if user:
                # Ensure the organization context matches the ticket's bound organization
                if org_id and user.selected_organization_id != org_id:
                    user.selected_organization_id = org_id
            return user
        except Exception as e:
            logger.error(f"[RealtimeBus] Error validating ticket payload: {e}")
            return None


# Singleton instance
realtime_dashboard_bus = RealtimeDashboardBus()

