"""Autonomous Operational Health Monitoring Scheduler (Phase C3).

Periodically evaluates system health and alert rules in the background
without requiring manual dashboard or API interactions.

Safeguards:
- Non-overlapping execution: Internal check lock prevents concurrent ticks.
- Cross-worker coordination: Uses Redis leader election key ('spx:health:scheduler_leader')
  when Redis is available, with safe single-worker local fallback when Redis is down.
- Zero call disruption: Isolated asyncio task with bounded timeouts; exceptions cannot crash
  the event loop or interrupt live voice sessions.
- Graceful shutdown: Cleanly handles cancellation during FastAPI lifespan termination.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from datetime import UTC, datetime
from typing import Optional

try:
    import fcntl
except ImportError:
    fcntl = None  # Non-POSIX / Windows dev fallback

from loguru import logger

from api.constants import REDIS_URL
from api.services.health.alert_manager import alert_manager
from api.services.health.provider_monitor import provider_monitor
from api.services.health.system_monitor import system_monitor

ENV_INTERVAL_SECONDS = "SPX_HEALTH_CHECK_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 30.0


class HealthScheduler:
    """Autonomous background scheduler for continuous health monitoring."""

    def __init__(self, interval_seconds: Optional[float] = None) -> None:
        self.interval_seconds = interval_seconds or float(
            os.getenv(ENV_INTERVAL_SECONDS, DEFAULT_INTERVAL_SECONDS)
        )
        self.worker_id = f"worker_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._check_lock = asyncio.Lock()
        self.last_run_at: Optional[datetime] = None
        self.run_count: int = 0
        self.last_duration_ms: float = 0.0
        self._file_lock_fd: Optional[int] = None
        self._holds_file_lock: bool = False

    async def start(self) -> None:
        """Starts the autonomous background monitoring loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop(), name="spx_health_scheduler")
        logger.info(
            f"[Health Scheduler] Started background monitor (interval={self.interval_seconds}s, worker={self.worker_id})"
        )

    async def stop(self) -> None:
        """Gracefully stops the background monitoring loop."""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"[Health Scheduler] Stopped background monitor (worker={self.worker_id})")

    async def _scheduler_loop(self) -> None:
        """Continuous execution loop."""
        # Initial brief delay to allow main application startup to settle
        initial_delay = min(0.5, self.interval_seconds)
        await asyncio.sleep(initial_delay)

        while self._running:
            try:
                # 1. Coordinate across multiple workers if Redis is available
                is_leader = await self._acquire_leader_lease()
                if is_leader:
                    try:
                        await self.run_check_cycle()
                    finally:
                        if self._holds_file_lock:
                            self._release_file_lock()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[Health Scheduler] Unexpected error in check loop: {exc}")
            finally:
                if self._holds_file_lock:
                    self._release_file_lock()

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _acquire_leader_lease(self) -> bool:
        """Coordinates across multi-worker deployments to prevent duplicate check runs."""
        import redis.asyncio as aioredis  # type: ignore

        client = None
        lease_seconds = int(max(10, self.interval_seconds * 1.5))
        try:
            client = aioredis.from_url(REDIS_URL, socket_connect_timeout=1.5)
            # Try to acquire or refresh leader lease
            acquired = await client.set(
                "spx:health:scheduler_leader",
                self.worker_id,
                nx=True,
                ex=lease_seconds,
            )
            if acquired:
                return True

            # Check if this worker already holds the lease
            current_leader = await client.get("spx:health:scheduler_leader")
            if current_leader and current_leader.decode() == self.worker_id:
                # Refresh lease
                await client.expire("spx:health:scheduler_leader", lease_seconds)
                return True

            return False
        except Exception:
            # When Redis is offline or inaccessible, fallback to file-based leader election
            # across workers sharing /tmp to prevent duplicate alerts and alert storms!
            return await self._acquire_file_leader_lease(lease_seconds)
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    def _release_file_lock(self) -> None:
        """Releases the cross-process fcntl.flock if held."""
        if self._file_lock_fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._file_lock_fd, fcntl.LOCK_UN)
                os.close(self._file_lock_fd)
            except Exception:
                pass
            finally:
                self._file_lock_fd = None
                self._holds_file_lock = False

    async def _acquire_file_leader_lease(self, lease_seconds: int) -> bool:
        """Cross-process leader coordination using fcntl.flock when Redis is offline.

        Provides kernel-enforced mutual exclusion across worker processes sharing /tmp.
        Held exclusively throughout the monitoring cycle.
        """
        tmp_dir = tempfile.gettempdir()
        lock_file = os.path.join(tmp_dir, "spx_health_scheduler.flock")
        lease_file = os.path.join(tmp_dir, "spx_health_scheduler_lease.json")
        now = time.time()

        try:
            # 1. Open lock file
            fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)

            # 2. Attempt non-blocking kernel lock
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError):
                    # Another process is holding the lock and actively executing a check cycle
                    os.close(fd)
                    return False

            # Lock acquired! Check if another worker completed a cycle recently
            min_spacing = self.interval_seconds * 0.8
            if os.path.exists(lease_file):
                try:
                    with open(lease_file, "r") as f:
                        meta = json.loads(f.read())
                    last_run = float(meta.get("last_run_timestamp", 0.0))
                    if (now - last_run) < min_spacing:
                        # Another worker ran recently; release lock and skip
                        if fcntl is not None:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)
                        return False
                except Exception:
                    pass

            # Retain lock for the duration of the monitoring cycle
            self._file_lock_fd = fd
            self._holds_file_lock = True
            return True

        except Exception as exc:
            logger.warning(f"[Health Scheduler] Cross-process lock error: {exc}")
            return True

    async def run_check_cycle(self) -> None:
        """Executes a single non-overlapping health check and alert evaluation pass."""
        if self._check_lock.locked():
            logger.debug("[Health Scheduler] Check cycle already in progress; skipping overlap")
            return

        async with self._check_lock:
            start = time.perf_counter()
            try:
                # 1. System infrastructure health
                system_report = await system_monitor.get_system_health()

                # 2. Telephony and AI provider health
                providers = await provider_monitor.check_all_providers()

                # 3. Evaluate rules and trigger/resolve alerts
                await alert_manager.evaluate_system_report(
                    report=system_report,
                    provider_components=providers,
                    correlation_id=f"sched_{int(start)}",
                )

                self.last_run_at = datetime.now(UTC)
                self.run_count += 1
                self.last_duration_ms = round((time.perf_counter() - start) * 1000, 2)

                # Record completion timestamp for multi-process pacing
                try:
                    tmp_dir = tempfile.gettempdir()
                    lease_file = os.path.join(tmp_dir, "spx_health_scheduler_lease.json")
                    with open(lease_file, "w") as f:
                        json.dump({
                            "last_run_timestamp": time.time(),
                            "leader_id": self.worker_id,
                            "cycle_duration_ms": self.last_duration_ms,
                        }, f)
                except Exception:
                    pass

                logger.debug(
                    f"[Health Scheduler] Completed cycle #{self.run_count} in {self.last_duration_ms}ms "
                    f"(overall={system_report.overall_status.value})"
                )
            except Exception as exc:
                self.last_duration_ms = round((time.perf_counter() - start) * 1000, 2)
                logger.error(f"[Health Scheduler] Check cycle failed: {exc}")


health_scheduler = HealthScheduler()
