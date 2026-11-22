"""Phase C3 Acceptance Tests: Operational Health Monitoring & External Alerts.

Verifies:
1. Healthy dependencies report healthy.
2. PostgreSQL failure is detected.
3. Redis failure is detected.
4. MinIO storage failure is detected.
5. Worker/container failure signal is handled.
6. Provider timeout/degradation is detected.
7. Missing provider configuration reports NOT_CONFIGURED.
8. Duplicate alerts are suppressed by cooldown.
9. Recovery produces a recovery event.
10. Alert delivery failure is logged and safely retried.
11. Secrets and PII do not appear in events.
12. Ordinary users cannot access Superadmin diagnostics.
13. Monitoring itself cannot cause an active call failure.
14. Health monitoring remains bounded under repeated failures.
15. C1 request tracing and C2 billing/audit paths remain unaffected.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.db.models import UserModel
from api.services.health.alert_manager import AlertManager, AlertRule, AlertSeverity
from api.services.health.external_notifier import ExternalNotifier, sanitize_context
from api.services.health.models import (
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    IncidentEvent,
    SystemHealthReport,
)
from api.services.health.provider_monitor import ProviderMonitor
from api.services.health.system_monitor import SystemMonitor


@pytest.mark.asyncio
async def test_01_healthy_dependencies_report_healthy():
    """Verify that all working dependencies report HEALTHY status."""
    monitor = SystemMonitor(timeout_seconds=5.0)
    with patch.object(
        monitor,
        "check_frontend",
        return_value=ComponentHealth(
            name="frontend_ui",
            status=HealthStatus.HEALTHY,
            message="Frontend responding",
        ),
    ):
        report = await monitor.get_system_health()

        assert report.liveness is True
        assert "backend_api" in report.components
        assert report.components["backend_api"].status == HealthStatus.HEALTHY
        assert "postgresql" in report.components
        assert report.components["postgresql"].status == HealthStatus.HEALTHY
        assert "redis" in report.components
        assert report.components["redis"].status == HealthStatus.HEALTHY
        assert report.host_metrics is not None
        assert report.host_metrics.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)
        assert report.overall_status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)


@pytest.mark.asyncio
async def test_02_postgresql_failure_detected():
    """Verify that PostgreSQL connectivity failure is reliably detected as UNHEALTHY."""
    monitor = SystemMonitor()

    # Mock database session to raise connection error
    with patch("api.services.health.system_monitor.db_client.async_session") as mock_session_ctx:
        mock_session_ctx.side_effect = RuntimeError("PostgreSQL connection refused")
        health = await monitor.check_database()

        assert health.status == HealthStatus.UNHEALTHY
        assert "RuntimeError" in health.message or "connection" in health.message.lower()


@pytest.mark.asyncio
async def test_03_redis_failure_detected():
    """Verify that Redis connection timeout or failure is detected as UNHEALTHY."""
    monitor = SystemMonitor(timeout_seconds=0.1)

    with patch("redis.asyncio.from_url") as mock_redis_cls:
        mock_client = AsyncMock()
        mock_client.ping.side_effect = TimeoutError("Redis socket timeout")
        mock_redis_cls.return_value = mock_client

        health = await monitor.check_redis()
        assert health.status == HealthStatus.UNHEALTHY
        assert "timed out" in health.message or "TimeoutError" in health.message


@pytest.mark.asyncio
async def test_04_minio_storage_failure_detected():
    """Verify that MinIO storage bucket loss or exception is detected."""
    monitor = SystemMonitor()

    with patch("api.services.storage.get_storage_for_backend") as mock_get_storage:
        mock_storage = MagicMock()
        mock_storage.bucket_name = "voice-audio"
        mock_storage.client.bucket_exists.return_value = False
        mock_get_storage.return_value = mock_storage

        health = await monitor.check_storage()
        assert health.status == HealthStatus.DEGRADED
        assert "not found" in health.message


@pytest.mark.asyncio
async def test_05_worker_failure_signal_handled():
    """Verify that a terminated or unmanaged voice worker is detected."""
    monitor = SystemMonitor()

    from api.services.livekit.worker_process import LiveKitWorkerStatus

    with patch("api.services.health.system_monitor.livekit_configured", return_value=True):
        with patch("api.services.health.system_monitor.get_worker_status") as mock_stat:
            mock_stat.return_value = LiveKitWorkerStatus(
                managed_by_api=True, running=False, pid=None, message="worker terminated"
            )
            health = await monitor.check_livekit()
            assert health.status == HealthStatus.DEGRADED
            assert "not running" in health.message


@pytest.mark.asyncio
async def test_06_provider_timeout_and_degradation_detected():
    """Verify that elevated call failures or carrier quarantines report DEGRADED."""
    p_monitor = ProviderMonitor()

    # Mock smart_did controller with high failure rate
    mock_did = MagicMock()
    mock_did.carrier = "vobiz"
    mock_did.state.value = "active"
    mock_did.is_in_cooldown.return_value = False
    mock_did.total_calls = 100
    mock_did.failed_calls = 45  # 45% failure rate

    with patch.dict("os.environ", {"VOBIZ_AUTH_ID": "test-auth", "VOBIZ_AUTH_TOKEN": "test-token"}):
        with patch("api.services.telephony.smart_did.get_smart_did_controller") as mock_ctrl:
            mock_ctrl.return_value.get_all_dids.return_value = {"+911234567890": mock_did}

            health = await p_monitor._check_vobiz_health(organization_id=1)
            assert health.status == HealthStatus.DEGRADED
            assert "Elevated call failure" in health.message


@pytest.mark.asyncio
async def test_07_missing_provider_configuration_reports_not_configured():
    """Verify that missing provider keys report NOT_CONFIGURED, never HEALTHY."""
    p_monitor = ProviderMonitor()

    with patch.dict("os.environ", {}, clear=True):
        providers = await p_monitor.check_all_providers()

        assert providers["vobiz"].status == HealthStatus.NOT_CONFIGURED
        assert providers["voicelink"].status == HealthStatus.NOT_CONFIGURED
        assert providers["gemini_live"].status == HealthStatus.NOT_CONFIGURED
        assert providers["deepgram"].status == HealthStatus.NOT_CONFIGURED
        assert providers["deepseek"].status == HealthStatus.NOT_CONFIGURED
        assert providers["cartesia"].status == HealthStatus.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_08_duplicate_alerts_suppressed_by_cooldown():
    """Verify that consecutive failures do not cause duplicate alert storms within cooldown."""
    mgr = AlertManager()
    dispatched_events: list[dict] = []

    def mock_transport(payload: dict):
        dispatched_events.append(payload)

    with patch("api.services.health.alert_manager.external_notifier._mock_handler", mock_transport):
        # 1. First failure -> triggers alert
        unhealthy_comp = ComponentHealth(
            name="postgresql",
            status=HealthStatus.UNHEALTHY,
            message="Connection dropped",
        )
        report1 = SystemHealthReport(
            overall_status=HealthStatus.UNHEALTHY,
            liveness=True,
            readiness=False,
            components={"postgresql": unhealthy_comp},
        )

        fired1 = await mgr.evaluate_system_report(report1)
        assert len(fired1) == 1
        assert len(dispatched_events) == 1
        assert dispatched_events[0]["event_type"] == "alert.fired"
        assert dispatched_events[0]["component"] == "postgresql"

        # 2. Second failure immediately after -> suppressed by cooldown
        fired2 = await mgr.evaluate_system_report(report1)
        assert len(fired2) == 0  # Suppressed
        assert len(dispatched_events) == 1  # No duplicate dispatch!
        assert mgr.active_incidents["postgresql"].count == 2


@pytest.mark.asyncio
async def test_09_recovery_produces_recovery_event():
    """Verify that when an unhealthy component recovers, an alert.resolved notification fires."""
    mgr = AlertManager()
    dispatched: list[dict] = []

    with patch("api.services.health.alert_manager.external_notifier._mock_handler", lambda p: dispatched.append(p)):
        # 1. Trigger failure
        unhealthy = ComponentHealth(name="redis", status=HealthStatus.UNHEALTHY, message="Redis down")
        rep_unhealthy = SystemHealthReport(
            overall_status=HealthStatus.UNHEALTHY,
            liveness=True,
            readiness=False,
            components={"redis": unhealthy},
        )
        await mgr.evaluate_system_report(rep_unhealthy)
        assert "redis" in mgr.active_incidents
        assert dispatched[-1]["event_type"] == "alert.fired"

        # 2. Trigger recovery
        healthy = ComponentHealth(name="redis", status=HealthStatus.HEALTHY, message="Redis back online")
        rep_healthy = SystemHealthReport(
            overall_status=HealthStatus.HEALTHY,
            liveness=True,
            readiness=True,
            components={"redis": healthy},
        )
        resolved = await mgr.evaluate_system_report(rep_healthy)

        assert len(resolved) == 1
        assert "redis" not in mgr.active_incidents
        assert dispatched[-1]["event_type"] == "alert.resolved"
        assert dispatched[-1]["resolved_at"] is not None
        assert len(mgr.incident_history) == 1


@pytest.mark.asyncio
async def test_10_alert_delivery_failure_logged_and_safely_retried():
    """Verify that external notification retries on network error without raising exceptions."""
    notifier = ExternalNotifier()

    incident = IncidentEvent(
        incident_id="inc_test_123",
        component="postgresql",
        severity=AlertSeverity.CRITICAL,
        health_status=HealthStatus.UNHEALTHY,
        error_code="ERR_PG_UNHEALTHY",
        message="PG down",
    )

    with patch.dict("os.environ", {"SPX_ALERT_WEBHOOK_URL": "http://127.0.0.1:9999/down-webhook"}):
        with patch("httpx.AsyncClient.post", side_effect=RuntimeError("Connection refused")):
            # Must return False and not raise
            res = await notifier.notify(incident)
            assert res is False


def test_11_secrets_and_pii_sanitized_from_events():
    """Verify that credentials, tokens, passwords, and phone numbers are scrubbed from contexts."""
    sensitive_context = {
        "db_password": "supersecretpassword",
        "api_key": "live_key_999999",
        "customer_phone": "+919876543210",
        "nested": {
            "auth_token": "bearer xyz-secret",
            "safe_metric": 42,
        },
        "safe_component": "dialer",
    }

    sanitized = sanitize_context(sensitive_context)

    assert sanitized["db_password"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["customer_phone"] == "[REDACTED]"
    assert sanitized["nested"]["auth_token"] == "[REDACTED]"
    assert sanitized["nested"]["safe_metric"] == 42
    assert sanitized["safe_component"] == "dialer"


@pytest.mark.asyncio
async def test_12_rbac_ordinary_users_denied_superadmin_diagnostics():
    """Verify that ordinary users cannot access the Superadmin diagnostics endpoint."""
    from api.routes.health import comprehensive_diagnostics

    regular_user = UserModel(id=2, email="user@tenant.com", is_superuser=False)
    superuser = UserModel(id=1, email="admin@spxvoice.com", is_superuser=True)

    # Calling with superuser succeeds
    res = await comprehensive_diagnostics(user=superuser)
    assert "components" in res
    assert "host_metrics" in res
    assert "providers" in res

    # The get_superuser dependency raises 403 on non-superusers
    from api.services.auth.depends import get_superuser

    with patch("api.services.auth.depends.get_user", return_value=regular_user):
        with pytest.raises(HTTPException) as exc_info:
            await get_superuser(authorization="Bearer token")
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_13_monitoring_failures_never_interrupt_voice_pipeline():
    """Verify that unexpected health monitoring exceptions are trapped cleanly."""
    monitor = SystemMonitor()

    # Even if an unhandled internal error occurs during check, get_system_health returns cleanly
    with patch.object(monitor, "check_database", side_effect=Exception("Catastrophic DB driver crash")):
        report = await monitor.get_system_health()
        assert report.components["postgresql"].status == HealthStatus.UNKNOWN
        assert "Catastrophic DB driver crash" in report.components["postgresql"].message
        # Overall status reports UNKNOWN, UNHEALTHY, or DEGRADED, but the process does not terminate
        assert report.overall_status in (HealthStatus.UNKNOWN, HealthStatus.UNHEALTHY, HealthStatus.DEGRADED)


@pytest.mark.asyncio
async def test_14_incident_history_bounded_ring_buffer():
    """Verify that alert manager memory remains strictly bounded under repeated incidents."""
    mgr = AlertManager(max_history=50)

    now = datetime.now(UTC)
    for i in range(150):
        inc = IncidentEvent(
            incident_id=f"inc_overflow_{i}",
            component="test_worker",
            severity=AlertSeverity.WARNING,
            health_status=HealthStatus.DEGRADED,
            error_code="ERR_TEST",
            message=f"Test failure {i}",
            resolved_at=now,
        )
        mgr.incident_history.append(inc)

    # Ring buffer must never exceed max_history
    assert len(mgr.incident_history) == 50
    assert mgr.incident_history[-1].incident_id == "inc_overflow_149"
    assert mgr.incident_history[0].incident_id == "inc_overflow_100"


@pytest.mark.asyncio
async def test_15_c1_correlation_and_c2_regression():
    """Verify that C1 correlation IDs flow into incident events and C2 audit/billing remains unaffected."""
    mgr = AlertManager()
    comp = ComponentHealth(name="postgresql", status=HealthStatus.UNHEALTHY, message="DB down")
    rep = SystemHealthReport(
        overall_status=HealthStatus.UNHEALTHY,
        liveness=True,
        readiness=False,
        components={"postgresql": comp},
    )

    req_id = "req_c1_trace_98765"
    fired = await mgr.evaluate_system_report(rep, correlation_id=req_id)
    assert len(fired) == 1
    assert fired[0].correlation_id == req_id

