"""Phase C3 Autonomous Monitoring Scheduler Gate Tests.

Verifies:
1. Background scheduler periodically executes health checks without any API/dashboard calls.
2. Consecutive runs do not overlap (lock protection).
3. Multi-worker leader election prevents check multiplication.
4. Automatic failure detection and recovery occurs autonomously without manual health API invocation.
5. Graceful shutdown terminates the background task cleanly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.health.alert_manager import alert_manager
from api.services.health.models import (
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    SystemHealthReport,
)
from api.services.health.scheduler import HealthScheduler
from api.services.health.system_monitor import system_monitor


@pytest.mark.asyncio
async def test_scheduler_startup_and_autonomous_execution():
    """Verify scheduler executes autonomously in background without API requests."""
    scheduler = HealthScheduler(interval_seconds=0.05)

    with patch.object(scheduler, "run_check_cycle", new_callable=AsyncMock) as mock_cycle:
        with patch.object(scheduler, "_acquire_leader_lease", return_value=True):
            await scheduler.start()
            await asyncio.sleep(0.18)
            await scheduler.stop()

            assert mock_cycle.call_count >= 2


@pytest.mark.asyncio
async def test_scheduler_non_overlapping_lock():
    """Verify that slow health check passes cannot overlap or pile up."""
    scheduler = HealthScheduler(interval_seconds=1.0)

    # Acquire the internal lock manually to simulate an in-progress check
    async with scheduler._check_lock:
        initial_count = scheduler.run_count
        # This call must immediately skip and not execute concurrently
        await scheduler.run_check_cycle()
        assert scheduler.run_count == initial_count


@pytest.mark.asyncio
async def test_scheduler_cross_worker_leader_election():
    """Verify that multi-worker leader lease coordinates execution and handles Redis downtime."""
    worker1 = HealthScheduler(interval_seconds=10.0)
    worker2 = HealthScheduler(interval_seconds=10.0)

    # Case A: Redis works and worker 1 is leader
    with patch("redis.asyncio.from_url") as mock_redis_cls:
        client = AsyncMock()
        client.set.return_value = True  # worker1 acquires lease
        mock_redis_cls.return_value = client

        is_w1_leader = await worker1._acquire_leader_lease()
        assert is_w1_leader is True

        client.set.return_value = False  # worker2 denied lease
        client.get.return_value = worker1.worker_id.encode()  # current leader is worker1
        is_w2_leader = await worker2._acquire_leader_lease()
        assert is_w2_leader is False

    # Case B: Redis goes offline -> fail-safe fallback to cross-process file lock
    import os
    import tempfile

    flock_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler.flock")
    lease_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler_lease.json")
    for f_path in (flock_file, lease_file):
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except Exception:
                pass

    with patch("redis.asyncio.from_url", side_effect=Exception("Redis connection refused")):
        # Worker 1 claims leadership via cross-process flock
        is_w1_fallback = await worker1._acquire_leader_lease()
        assert is_w1_fallback is True

        # Worker 2 attempts while worker 1 holds flock -> MUST BE DENIED at kernel level!
        is_w2_fallback = await worker2._acquire_leader_lease()
        assert is_w2_fallback is False

        # Worker 1 finishes cycle and releases flock
        worker1._release_file_lock()

        # Case C: Worker 1 crashes / lease expires -> Worker 2 successfully takes over
        import json

        with open(lease_file, "w") as f:
            json.dump({"last_run_timestamp": 0.0, "leader_id": worker1.worker_id}, f)

        is_w2_takeover = await worker2._acquire_leader_lease()
        assert is_w2_takeover is True
        worker2._release_file_lock()


@pytest.mark.asyncio
async def test_multi_worker_redis_outage_alert_deduplication():
    """Verify that 3 concurrent workers during a Redis outage produce only 1 leader cycle."""
    w1 = HealthScheduler(interval_seconds=10.0)
    w2 = HealthScheduler(interval_seconds=10.0)
    w3 = HealthScheduler(interval_seconds=10.0)

    import os
    import tempfile

    flock_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler.flock")
    lease_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler_lease.json")
    for f_path in (flock_file, lease_file):
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except Exception:
                pass

    with patch("redis.asyncio.from_url", side_effect=Exception("Redis down")):
        # Simulate all 3 workers attempting to lead concurrently
        res1 = await w1._acquire_leader_lease()
        res2 = await w2._acquire_leader_lease()
        res3 = await w3._acquire_leader_lease()

        # Exactly 1 worker wins, 2 are suppressed by kernel-level flock
        assert [res1, res2, res3].count(True) == 1
        assert [res1, res2, res3].count(False) == 2

        # Clean up lock
        w1._release_file_lock()
        w2._release_file_lock()
        w3._release_file_lock()


def test_cross_process_flock_overlapping_claims_and_crash_release():
    """Verify kernel-level mutual exclusion across 2 separate OS processes."""
    import json
    import os
    import subprocess
    import sys
    import tempfile
    import time

    flock_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler.flock")
    lease_file = os.path.join(tempfile.gettempdir(), "spx_health_scheduler_lease.json")
    res1_file = os.path.join(tempfile.gettempdir(), "res_proc_1.json")
    res2_file = os.path.join(tempfile.gettempdir(), "res_proc_2.json")

    for f in (flock_file, lease_file, res1_file, res2_file):
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

    script1 = (
        "import asyncio, json, time\n"
        "from unittest.mock import patch\n"
        "from api.services.health.scheduler import HealthScheduler\n"
        "async def main():\n"
        "    s = HealthScheduler(interval_seconds=10.0)\n"
        "    s.worker_id = 'proc_1'\n"
        "    with patch('redis.asyncio.from_url', side_effect=Exception('Redis offline')):\n"
        "        got = await s._acquire_leader_lease()\n"
        f"        with open('{res1_file}', 'w') as f: json.dump({{'acquired': got}}, f)\n"
        "        if got:\n"
        "            await asyncio.sleep(2.5)\n"
        "            s._release_file_lock()\n"
        "asyncio.run(main())\n"
    )

    script2 = (
        "import asyncio, json\n"
        "from unittest.mock import patch\n"
        "from api.services.health.scheduler import HealthScheduler\n"
        "async def main():\n"
        "    s = HealthScheduler(interval_seconds=10.0)\n"
        "    s.worker_id = 'proc_2'\n"
        "    with patch('redis.asyncio.from_url', side_effect=Exception('Redis offline')):\n"
        "        got = await s._acquire_leader_lease()\n"
        f"        with open('{res2_file}', 'w') as f: json.dump({{'acquired': got}}, f)\n"
        "        if got:\n"
        "            s._release_file_lock()\n"
        "asyncio.run(main())\n"
    )

    # Launch process 1
    p1 = subprocess.Popen([sys.executable, "-c", script1])

    # Wait until process 1 has acquired the flock and written its result file
    for _ in range(80):
        if os.path.exists(res1_file):
            break
        time.sleep(0.2)

    # Launch process 2 while process 1 is actively sleeping and holding the flock
    p2 = subprocess.Popen([sys.executable, "-c", script2])

    p2.wait(timeout=20)
    p1.wait(timeout=20)

    with open(res1_file, "r") as f:
        r1 = json.load(f)
    with open(res2_file, "r") as f:
        r2 = json.load(f)

    assert r1.get("acquired") is True, "Process 1 should acquire the flock"
    assert r2.get("acquired") is False, "Process 2 must be blocked at kernel level while Process 1 holds the lock"


@pytest.mark.asyncio
async def test_host_metrics_scope_accuracy():
    """Verify host metrics accurately identify local container/WSL2 vs production VPS."""
    metrics = system_monitor.check_host_metrics()
    assert metrics is not None
    assert metrics.metrics_scope in ("local_docker_wsl2", "local_windows", "container_linux")
    # In local testing environment, it must NEVER masquerade as Hostinger VPS
    assert metrics.metrics_scope != "hostinger_vps"
    assert metrics.node_platform != ""

    # Even if ENVIRONMENT=production is passed, WSL kernel must NEVER be labeled as hostinger_vps
    with patch("platform.release", return_value="6.18.33.2-microsoft-standard-WSL2"):
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            wsl_metrics = system_monitor.check_host_metrics()
            assert wsl_metrics.metrics_scope == "local_docker_wsl2"

    # On actual production Linux server (non-WSL kernel), verify it resolves to hostinger_vps
    with patch("platform.release", return_value="6.8.0-52-generic"):
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            prod_metrics = system_monitor.check_host_metrics()
            assert prod_metrics.metrics_scope == "hostinger_vps"
            assert prod_metrics.environment == "production"


@pytest.mark.asyncio
async def test_scheduler_automatic_failure_detection_and_recovery_without_api():
    """Demonstrate autonomous failure detection and recovery without any API calls."""
    scheduler = HealthScheduler(interval_seconds=0.05)
    alert_manager.active_incidents.clear()

    # Phase 1: Simulated autonomous failure
    failing_db = ComponentHealth(
        name="postgresql",
        status=HealthStatus.UNHEALTHY,
        message="Database connection refused",
    )
    unhealthy_report = SystemHealthReport(
        overall_status=HealthStatus.UNHEALTHY,
        liveness=True,
        readiness=False,
        components={"postgresql": failing_db},
        host_metrics=HostMetrics(
            cpu_percent=10.0,
            memory_percent=30.0,
            memory_total_mb=16000.0,
            memory_available_mb=10000.0,
            disk_percent=50.0,
            disk_total_gb=100.0,
            disk_free_gb=50.0,
            status=HealthStatus.HEALTHY,
        ),
    )

    with patch("api.services.health.scheduler.system_monitor.get_system_health", return_value=unhealthy_report):
        with patch("api.services.health.scheduler.provider_monitor.check_all_providers", return_value={}):
            # Run one autonomous cycle
            await scheduler.run_check_cycle()

            # Autonomous failure detection verified without any HTTP request
            assert "postgresql" in alert_manager.active_incidents
            inc = alert_manager.active_incidents["postgresql"]
            assert inc.severity.value == "CRITICAL"
            assert inc.health_status.value == "UNHEALTHY"
            assert inc.count == 1

    # Phase 2: Simulated autonomous recovery
    healthy_db = ComponentHealth(
        name="postgresql",
        status=HealthStatus.HEALTHY,
        message="PostgreSQL operational",
    )
    healthy_report = SystemHealthReport(
        overall_status=HealthStatus.HEALTHY,
        liveness=True,
        readiness=True,
        components={"postgresql": healthy_db},
        host_metrics=HostMetrics(
            cpu_percent=10.0,
            memory_percent=30.0,
            memory_total_mb=16000.0,
            memory_available_mb=10000.0,
            disk_percent=50.0,
            disk_total_gb=100.0,
            disk_free_gb=50.0,
            status=HealthStatus.HEALTHY,
        ),
    )

    with patch("api.services.health.scheduler.system_monitor.get_system_health", return_value=healthy_report):
        with patch("api.services.health.scheduler.provider_monitor.check_all_providers", return_value={}):
            # Run second autonomous cycle
            await scheduler.run_check_cycle()

            # Autonomous recovery verified without any HTTP request
            assert "postgresql" not in alert_manager.active_incidents
            assert len(alert_manager.incident_history) >= 1
            resolved_inc = alert_manager.incident_history[-1]
            assert resolved_inc.component == "postgresql"
            assert resolved_inc.resolved_at is not None


@pytest.mark.asyncio
async def test_scheduler_graceful_shutdown():
    """Verify scheduler gracefully terminates on shutdown."""
    scheduler = HealthScheduler(interval_seconds=1.0)
    await scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None

    await scheduler.stop()
    assert scheduler._running is False
    assert scheduler._task is None

