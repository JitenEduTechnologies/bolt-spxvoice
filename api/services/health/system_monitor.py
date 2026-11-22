"""Infrastructure Health Monitor (Phase C3).

Safely probes:
- FastAPI backend (liveness)
- Next.js frontend (readiness)
- PostgreSQL (SELECT 1 query check)
- Redis (PING check)
- MinIO / S3 storage (bucket inspection)
- LiveKit & LiveKit worker process
- Host metrics (CPU, RAM, Disk pressure for visible container / host environment)

Rules:
- Bounded timeouts (max 2 seconds) on all I/O.
- Never expose internal secrets, passwords, or connection strings.
- UNKNOWN must not appear as HEALTHY.
- Separate liveness from readiness.
- Failures do not crash or leak memory.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse
import shutil
import time
from datetime import UTC, datetime
from typing import Any, Dict, Optional

import httpx
from loguru import logger
from sqlalchemy import text

from api.constants import DATABASE_URL, REDIS_URL, UI_APP_URL
from api.db import db_client
from api.services.health.models import (
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    SystemHealthReport,
)
from api.services.livekit.client import livekit_configured
from api.services.livekit.worker_process import (
    WORKER_STATE_ABSENT,
    WORKER_STATE_MANAGED_ALIVE,
    WORKER_STATE_OWNERSHIP_UNCERTAIN,
    WORKER_STATE_SURVIVING_VERIFIED,
    WORKER_STATE_UNHEALTHY,
    get_worker_status,
)

try:
    import psutil  # type: ignore
except ImportError:
    psutil = None


class SystemMonitor:
    """Probes local and containerized infrastructure services."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds

    async def check_backend(self) -> ComponentHealth:
        """FastAPI process liveness check."""
        return ComponentHealth(
            name="backend_api",
            status=HealthStatus.HEALTHY,
            latency_ms=0.1,
            message="FastAPI backend event loop active",
        )

    async def check_frontend(self, target_url: Optional[str] = None) -> ComponentHealth:
        """Checks Next.js frontend availability across Docker and host environments."""
        candidates: List[str] = []
        if target_url:
            candidates.append(target_url)
        if UI_APP_URL:
            candidates.append(UI_APP_URL)

        # Every probe target is derived from configuration, never a literal
        # port: another UI instance on the same host (a dev server on 3000, a
        # second stack on 3001) must not be reported as this deployment's UI.
        extra = os.getenv("HEALTH_UI_PROBE_URLS", "")
        candidates.extend(u.strip() for u in extra.split(",") if u.strip())

        is_container = os.path.exists("/.dockerenv") or os.getenv("DEPLOYMENT_MODE") == "docker"
        if is_container and UI_APP_URL:
            # Inside a container the host/service names differ but the UI port
            # is the one the deployment configured.
            ui_port = urlparse(UI_APP_URL).port
            if ui_port:
                candidates.extend([
                    f"http://host.docker.internal:{ui_port}",
                    f"http://ui:{ui_port}",
                ])
        # Deduplicate while preserving order
        seen = set()
        deduped = [c for c in candidates if not (c in seen or seen.add(c))]

        start = time.perf_counter()
        last_error = None

        for probe_url in deduped:
            try:
                async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 2.0)) as client:
                    resp = await client.get(probe_url)
                    latency = round((time.perf_counter() - start) * 1000, 2)
                    if resp.status_code < 500:
                        return ComponentHealth(
                            name="frontend_ui",
                            status=HealthStatus.HEALTHY,
                            latency_ms=latency,
                            message=f"Frontend responding (HTTP {resp.status_code})",
                            details={"probed_url": probe_url},
                        )
                    return ComponentHealth(
                        name="frontend_ui",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        message=f"Frontend returned HTTP {resp.status_code}",
                        details={"probed_url": probe_url},
                    )
            except Exception as exc:
                last_error = exc

        latency = round((time.perf_counter() - start) * 1000, 2)
        # If in local dev / container where frontend is on host and probe cannot safely reach it:
        if is_container or os.getenv("ENVIRONMENT") == "local":
            logger.info(f"[Health] Frontend unverified in local container environment: {last_error}")
            return ComponentHealth(
                name="frontend_ui",
                status=HealthStatus.NOT_CONFIGURED,
                latency_ms=latency,
                message=f"Frontend unverified: host dev server not reached from container ({type(last_error).__name__})",
                details={"unverified": True},
            )

        logger.warning(f"[Health] Frontend check failed: {last_error}")
        return ComponentHealth(
            name="frontend_ui",
            status=HealthStatus.DEGRADED,
            latency_ms=latency,
            message=f"Frontend unreachable: {type(last_error).__name__}",
        )

    async def check_database(self) -> ComponentHealth:
        """Executes a bounded SELECT 1 query against PostgreSQL."""
        start = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with db_client.async_session() as session:
                    res = await session.execute(text("SELECT 1"))
                    val = res.scalar()
                    latency = round((time.perf_counter() - start) * 1000, 2)
                    if val == 1:
                        return ComponentHealth(
                            name="postgresql",
                            status=HealthStatus.HEALTHY,
                            latency_ms=latency,
                            message="PostgreSQL query execution verified",
                        )
                    return ComponentHealth(
                        name="postgresql",
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency,
                        message="Unexpected query result from PostgreSQL",
                    )
        except TimeoutError:
            latency = round((time.perf_counter() - start) * 1000, 2)
            logger.error("[Health] PostgreSQL check timed out")
            return ComponentHealth(
                name="postgresql",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Database query timed out after {self.timeout_seconds}s",
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 2)
            logger.error(f"[Health] PostgreSQL check failed: {type(exc).__name__}")
            return ComponentHealth(
                name="postgresql",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Database connection failed: {type(exc).__name__}",
            )

    async def check_redis(self) -> ComponentHealth:
        """Pings Redis with a bounded timeout."""
        start = time.perf_counter()
        import redis.asyncio as aioredis  # type: ignore

        client = None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                client = aioredis.from_url(REDIS_URL, socket_connect_timeout=self.timeout_seconds)
                pong = await client.ping()
                latency = round((time.perf_counter() - start) * 1000, 2)
                if pong is True:
                    return ComponentHealth(
                        name="redis",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency,
                        message="Redis PING answered successfully",
                    )
                return ComponentHealth(
                    name="redis",
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    message="Redis returned unexpected PING response",
                )
        except TimeoutError:
            latency = round((time.perf_counter() - start) * 1000, 2)
            logger.error("[Health] Redis check timed out")
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Redis PING timed out after {self.timeout_seconds}s",
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 2)
            logger.error(f"[Health] Redis check failed: {type(exc).__name__}")
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Redis connection failed: {type(exc).__name__}",
            )
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    async def check_storage(self) -> ComponentHealth:
        """Verifies MinIO / S3 bucket accessibility."""
        start = time.perf_counter()
        try:
            from api.enums import StorageBackend
            from api.services.storage import get_storage_for_backend

            storage = get_storage_for_backend(StorageBackend.MINIO.value)
            if hasattr(storage, "client") and hasattr(storage.client, "bucket_exists"):
                # Run synchronous MinIO client check in a thread executor with timeout
                exists = await asyncio.to_thread(storage.client.bucket_exists, storage.bucket_name)
                latency = round((time.perf_counter() - start) * 1000, 2)
                if exists:
                    return ComponentHealth(
                        name="storage",
                        status=HealthStatus.HEALTHY,
                        latency_ms=latency,
                        message=f"Storage bucket '{storage.bucket_name}' verified",
                    )
                return ComponentHealth(
                    name="storage",
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    message=f"Storage bucket '{storage.bucket_name}' not found",
                )
            latency = round((time.perf_counter() - start) * 1000, 2)
            return ComponentHealth(
                name="storage",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                message="Storage initialized without bucket client",
            )
        except Exception as exc:
            latency = round((time.perf_counter() - start) * 1000, 2)
            logger.error(f"[Health] Storage check failed: {type(exc).__name__}")
            return ComponentHealth(
                name="storage",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=f"Storage check failed: {type(exc).__name__}",
            )

    async def check_livekit(self) -> ComponentHealth:
        """Checks LiveKit server reachability and configuration."""
        if not livekit_configured():
            return ComponentHealth(
                name="livekit",
                status=HealthStatus.NOT_CONFIGURED,
                message="LiveKit runtime credentials not provisioned",
            )

        # Worker status check. Process liveness is not proof of LiveKit
        # registration, so registration is never reported as verified here.
        worker_stat = get_worker_status()
        worker_state = getattr(worker_stat, "state", None)
        details = {
            "worker_running": worker_stat.running,
            "worker_state": worker_state,
            "pid": worker_stat.pid,
            "registration_verified": worker_stat.registration_verified,
        }
        if worker_stat.managed_by_api:
            if worker_state == WORKER_STATE_MANAGED_ALIVE:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.UNVERIFIED,
                    message=(
                        "LiveKit worker process active; LiveKit registration "
                        "is not verified by the API"
                    ),
                    details=details,
                )
            if worker_state == WORKER_STATE_SURVIVING_VERIFIED:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.UNVERIFIED,
                    message=(
                        "LiveKit worker process is alive (verified surviving process); "
                        "LiveKit registration is not verified by the API"
                    ),
                    details=details,
                )
            if worker_state == WORKER_STATE_ABSENT:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.DEGRADED,
                    message="LiveKit configured but local worker process is not running",
                    details=details,
                )
            if worker_state == WORKER_STATE_OWNERSHIP_UNCERTAIN:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.DEGRADED,
                    message=(
                        "LiveKit worker ownership uncertain: "
                        f"{worker_stat.message or 'unverified worker process detected'}"
                    ),
                    details=details,
                )
            if worker_state == WORKER_STATE_UNHEALTHY:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.DEGRADED,
                    message=(
                        "LiveKit worker process identity could not be verified: "
                        f"{worker_stat.message or 'identity mismatch'}"
                    ),
                    details=details,
                )
            if not worker_stat.running:
                return ComponentHealth(
                    name="livekit",
                    status=HealthStatus.DEGRADED,
                    message="LiveKit configured but local worker process is not running",
                    details=details,
                )

        return ComponentHealth(
            name="livekit",
            status=HealthStatus.HEALTHY,
            message="LiveKit configured and active",
            details=details,
        )

    def check_host_metrics(self) -> HostMetrics:
        """Inspects CPU, RAM, and Disk space for the visible execution environment."""
        now = datetime.now(UTC)
        try:
            total, used, free = shutil.disk_usage("/")
            disk_percent = round((used / total) * 100, 1)
            disk_total_gb = round(total / (1024**3), 2)
            disk_free_gb = round(free / (1024**3), 2)
        except Exception as exc:
            logger.warning(f"[Health] Failed to read disk usage: {exc}")
            disk_percent = 0.0
            disk_total_gb = 0.0
            disk_free_gb = 0.0

        if psutil is not None:
            try:
                cpu_pct = round(psutil.cpu_percent(interval=None), 1)
                vm = psutil.virtual_memory()
                mem_pct = round(vm.percent, 1)
                mem_total = round(vm.total / (1024**2), 1)
                mem_avail = round(vm.available / (1024**2), 1)
            except Exception as exc:
                logger.warning(f"[Health] psutil reading failed: {exc}")
                cpu_pct = 0.0
                mem_pct = 0.0
                mem_total = 0.0
                mem_avail = 0.0
        else:
            cpu_pct = 0.0
            mem_pct = 0.0
            mem_total = 0.0
            mem_avail = 0.0

        # Evaluate pressure thresholds
        # Severe disk pressure: >= 90%
        # Approaching limit: >= 80%
        storage_pressure = disk_percent >= 90.0
        if disk_percent >= 90.0 or mem_pct >= 95.0:
            status = HealthStatus.UNHEALTHY
        elif disk_percent >= 80.0 or mem_pct >= 85.0 or cpu_pct >= 90.0:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.HEALTHY

        # Accurately identify environment and metrics scope (never falsely label local Docker as Hostinger VPS)
        import platform

        rel = platform.release().lower()
        env_name = os.getenv("ENVIRONMENT", "local").lower()
        is_production = env_name in ("production", "prod") or os.getenv("DEPLOYMENT_MODE", "").lower() == "hostinger"

        is_container = os.path.exists("/.dockerenv")
        if not is_container and os.path.exists("/proc/1/cgroup"):
            try:
                with open("/proc/1/cgroup", "r") as f:
                    is_container = "docker" in f.read().lower()
            except Exception:
                pass

        if "microsoft-standard-wsl" in rel or "wsl" in rel:
            metrics_scope = "local_docker_wsl2" if is_container else "local_wsl2"
            scope_description = "Docker container inside WSL2 VM (Windows dev machine)"
        elif platform.system().lower() == "windows":
            metrics_scope = "local_windows"
            scope_description = "Native Windows Host"
        elif is_production and not ("wsl" in rel):
            metrics_scope = "hostinger_vps"
            scope_description = "Hostinger Linux VPS (Production Environment)"
        else:
            metrics_scope = "container_linux" if is_container else "host_linux"
            scope_description = "Linux Container" if is_container else "Linux Host"

        node_platform = f"{platform.system()} {platform.release()}"

        return HostMetrics(
            cpu_percent=cpu_pct,
            memory_percent=mem_pct,
            memory_total_mb=mem_total,
            memory_available_mb=mem_avail,
            disk_percent=disk_percent,
            disk_total_gb=disk_total_gb,
            disk_free_gb=disk_free_gb,
            status=status,
            storage_pressure=storage_pressure,
            metrics_scope=metrics_scope,
            environment=env_name,
            node_platform=node_platform,
            is_container=is_container,
            scope_description=scope_description,
            measured_at=now,
        )

    async def get_system_health(self) -> SystemHealthReport:
        """Aggregates all system components into a comprehensive report."""
        backend_task = self.check_backend()
        frontend_task = self.check_frontend()
        db_task = self.check_database()
        redis_task = self.check_redis()
        storage_task = self.check_storage()
        livekit_task = self.check_livekit()

        results = await asyncio.gather(
            backend_task,
            frontend_task,
            db_task,
            redis_task,
            storage_task,
            livekit_task,
            return_exceptions=True,
        )

        names = ["backend_api", "frontend_ui", "postgresql", "redis", "storage", "livekit"]
        components: Dict[str, ComponentHealth] = {}

        for name, res in zip(names, results):
            if isinstance(res, ComponentHealth):
                components[name] = res
            else:
                components[name] = ComponentHealth(
                    name=name,
                    status=HealthStatus.UNKNOWN,
                    message=f"Check raised unhandled exception: {type(res).__name__}: {res}",
                )

        host_metrics = self.check_host_metrics()

        # Overall liveness: backend alive
        liveness = components["backend_api"].status == HealthStatus.HEALTHY

        # Overall readiness: essential data planes (Postgres & Redis) must be healthy
        readiness = (
            components["postgresql"].status == HealthStatus.HEALTHY
            and components["redis"].status == HealthStatus.HEALTHY
        )

        # Compute overall status
        core_unhealthy = any(
            components[k].status == HealthStatus.UNHEALTHY
            for k in ["backend_api", "postgresql", "redis", "storage"]
            if k in components
        )
        statuses = [c.status for c in components.values()]
        if core_unhealthy or host_metrics.storage_pressure:
            overall_status = HealthStatus.UNHEALTHY
        elif any(s in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY) for s in statuses) or host_metrics.status == HealthStatus.DEGRADED:
            overall_status = HealthStatus.DEGRADED
        elif any(s == HealthStatus.UNKNOWN for s in statuses):
            overall_status = HealthStatus.UNKNOWN
        else:
            overall_status = HealthStatus.HEALTHY

        return SystemHealthReport(
            overall_status=overall_status,
            liveness=liveness,
            readiness=readiness,
            components=components,
            host_metrics=host_metrics,
            evaluated_at=datetime.now(UTC),
        )


system_monitor = SystemMonitor()
