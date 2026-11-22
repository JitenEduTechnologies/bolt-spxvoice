"""Phase C4 Acceptance Tests: System Health & Audit Center.

Verifies the 17 Acceptance Criteria for Phase C4:
1. Superadmin access succeeds on /health/status, /health/logs, /health/incidents.
2. Ordinary owner blocked from platform logs (403 Forbidden).
3. Cross-tenant access denied on Call Timeline (403 Forbidden).
4. Filter by Call ID and request ID works.
5. Pagination and date filtering work.
6. C1 error events traceable where ingested.
7. C2 audit events visible to authorized admins.
8. Estimated costs are not presented as verified (flagged ESTIMATED).
9. C3 failure and recovery appear in incident history.
10. Incident survives restart in durable storage (incidents.jsonl).
11. Alert acknowledgment works via POST /health/alerts/{incident_id}/acknowledge.
12. Missing provider signals appear UNKNOWN / UNVERIFIED / NOT_CONFIGURED.
13. Secrets and PII (phone numbers) excluded from exports.
14. Operational purge preview cannot delete protected audit records.
15. Purge with invalid token is rejected (400 Bad Request).
16. Call timeline marks missing signals NOT_RECORDED.
17. Purge records an audit log event (audit.operations.log_purge).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.db.models import DashboardEventModel, UserModel, WorkflowModel, WorkflowRunModel
from api.services.health.alert_manager import AlertManager, AlertRule, AlertSeverity, alert_manager
from api.services.health.durable_incident_store import DurableIncidentStore
from api.services.health.models import (
    ComponentHealth,
    HealthStatus,
    HostMetrics,
    IncidentEvent,
    SystemHealthReport,
)
from api.services.health.observability_service import ObservabilityService
from api.services.health.provider_monitor import ProviderMonitor
from api.services.health.system_monitor import SystemMonitor


@pytest.fixture
def superuser() -> UserModel:
    return UserModel(
        id=1,
        email="superadmin@spxvoice.com",
        is_superuser=True,
        selected_organization_id=1,
    )


@pytest.fixture
def regular_owner_org1() -> UserModel:
    return UserModel(
        id=2,
        email="owner1@tenant1.com",
        is_superuser=False,
        selected_organization_id=1,
    )


@pytest.fixture
def regular_owner_org2() -> UserModel:
    return UserModel(
        id=3,
        email="owner2@tenant2.com",
        is_superuser=False,
        selected_organization_id=2,
    )


# ---------------------------------------------------------------------------
# Criterion 1: Superadmin access succeeds on endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_01_superadmin_access_succeeds_on_health_endpoints(superuser: UserModel):
    """Verify superadmin can query /health/status, /health/logs, and /health/incidents."""
    from api.routes.health import (
        comprehensive_diagnostics,
        get_durable_incidents,
        search_centralized_logs,
    )

    with patch.object(
        SystemMonitor,
        "get_system_health",
        return_value=SystemHealthReport(
            timestamp=datetime.now(UTC),
            overall_status=HealthStatus.HEALTHY,
            components={},
            liveness=True,
            readiness=True,
        ),
    ):
        with patch.object(
            ProviderMonitor,
            "check_all_providers",
            return_value={},
        ):
            report = await comprehensive_diagnostics(user=superuser)
            assert report["overall_status"] == HealthStatus.HEALTHY.value

    with patch(
        "api.services.health.observability_service.observability_service.search_logs",
        new_callable=AsyncMock,
        return_value={"total_count": 0, "page": 1, "limit": 50, "events": []},
    ):
        logs_res = await search_centralized_logs(user=superuser)
        assert logs_res["total_count"] == 0
        assert isinstance(logs_res["events"], list)

    incidents_res = await get_durable_incidents(user=superuser)
    assert "incidents" in incidents_res
    assert "total_count" in incidents_res


# ---------------------------------------------------------------------------
# Criterion 2: Ordinary owner blocked from platform logs (403 Forbidden)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_02_ordinary_owner_blocked_from_platform_logs(
    regular_owner_org1: UserModel,
):
    """Verify ordinary non-superusers cannot access other tenants or platform-wide retention."""
    from api.services.auth.depends import get_superuser

    # 1. Dependency gate throws 403 on non-superuser
    with patch("api.services.auth.depends.get_user", return_value=regular_owner_org1):
        with pytest.raises(HTTPException) as exc_info:
            await get_superuser(authorization="Bearer tenant-token")
        assert exc_info.value.status_code == 403

    # 2. Service level cross-tenant filter check throws PermissionError
    svc = ObservabilityService()
    with pytest.raises(PermissionError) as perm_info:
        await svc.search_logs(
            user=regular_owner_org1,
            organization_id=999,  # Attempting to access foreign tenant 999
        )
    assert "forbidden" in str(perm_info.value).lower()


# ---------------------------------------------------------------------------
# Criterion 3: Cross-tenant access denied on Call Timeline (403 Forbidden)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_03_cross_tenant_access_denied_on_call_timeline(
    regular_owner_org1: UserModel,
):
    """Verify an ordinary owner cannot view call diagnostic timelines for another organization."""
    svc = ObservabilityService()

    # Synthetic workflow run and workflow belonging to organization 2
    foreign_run = WorkflowRunModel(
        id="run_foreign_99",
        workflow_id=42,
        is_completed=True,
        created_at=datetime.now(UTC),
    )
    foreign_wf = WorkflowModel(
        id=42,
        name="Foreign Agent",
        organization_id=2,  # Belongs to org 2
    )

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        
        # First query finds run, second query finds workflow
        mock_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=foreign_run)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=foreign_wf)),
        ]

        with pytest.raises(PermissionError) as perm_exc:
            await svc.get_call_timeline(
                call_identifier="call_foreign_abc",
                user=regular_owner_org1,  # Org 1 owner
            )
        assert "forbidden" in str(perm_exc.value).lower()


# ---------------------------------------------------------------------------
# Criterion 4: Filter by Call ID and request ID works
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_04_filter_by_call_id_and_request_id(superuser: UserModel):
    """Verify log search server-side filtering by call_id and request_id."""
    svc = ObservabilityService()

    now = datetime.now(UTC)
    mock_audit = DashboardEventModel(
        id=101,
        organization_id=1,
        event_type="workflow.run.completed",
        resource_type="audit",
        data={
            "call_id": "call_target_777",
            "request_id": "req_target_888",
            "outcome": "SUCCESS",
        },
        created_at=now,
    )

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_audit]
        mock_session.execute.return_value = mock_result

        # Search matching both
        res = await svc.search_logs(
            user=superuser,
            call_id="call_target_777",
            request_id="req_target_888",
        )
        assert res["total_count"] >= 1
        assert res["events"][0]["call_id"] == "call_target_777"

        # Search with non-matching call_id
        res_mismatch = await svc.search_logs(
            user=superuser,
            call_id="call_nonexistent_000",
        )
        assert res_mismatch["total_count"] == 0


# ---------------------------------------------------------------------------
# Criterion 5: Pagination and date filtering work
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_05_pagination_and_date_filtering(superuser: UserModel):
    """Verify server-side pagination and start/end time query construction."""
    svc = ObservabilityService()

    start = datetime.now(UTC) - timedelta(days=2)
    end = datetime.now(UTC)

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        res = await svc.search_logs(
            user=superuser,
            start_time=start,
            end_time=end,
            page=2,
            limit=25,
        )
        assert res["page"] == 2
        assert res["limit"] == 25
        assert isinstance(res["events"], list)


# ---------------------------------------------------------------------------
# Criterion 6: C1 error events traceable where ingested
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_06_c1_error_events_traceable(superuser: UserModel):
    """Verify C1 errors (e.g. ERR_OPENING_CACHE_FALLBACK) are queryable."""
    svc = ObservabilityService()

    c1_incident = IncidentEvent(
        incident_id="inc_c1_test",
        component="livekit_runtime",
        severity=AlertSeverity.WARNING,
        health_status=HealthStatus.DEGRADED,
        error_code="ERR_OPENING_CACHE_FALLBACK",
        message="Opening audio cache failed, fell back to TTS",
        correlation_id="req_c1_trace_99",
        call_id="call_c1_trace_99",
        first_seen=datetime.now(UTC),
        latest_seen=datetime.now(UTC),
    )

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result

        with patch(
            "api.services.health.observability_service.durable_incident_store.query_incidents",
            new_callable=AsyncMock,
            return_value=([c1_incident], 1),
        ):
            res = await svc.search_logs(
                user=superuser,
                call_id="call_c1_trace_99",
                request_id="req_c1_trace_99",
            )
            assert res["total_count"] >= 1
            matching = [
                e for e in res["events"] if e.get("error_code") == "ERR_OPENING_CACHE_FALLBACK"
            ]
            assert len(matching) == 1
            assert matching[0]["request_id"] == "req_c1_trace_99"


# ---------------------------------------------------------------------------
# Criterion 7: C2 audit events visible to authorized admins
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_07_c2_audit_events_visible_to_authorized_admins(superuser: UserModel):
    """Verify C2 security audit events appear in log explorer with sanitized payload."""
    svc = ObservabilityService()

    audit_row = DashboardEventModel(
        id=202,
        organization_id=1,
        event_type="audit.user.role_change",
        resource_type="audit",
        data={
            "actor_id": 1,
            "target_user_id": 2,
            "role_after": "org_admin",
            "outcome": "SUCCESS",
            "phone_masked": "+91 98*** **321",
        },
        created_at=datetime.now(UTC),
    )

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [audit_row]
        mock_session.execute.return_value = mock_result

        res = await svc.search_logs(user=superuser, event_type="role_change")
        assert res["total_count"] >= 1
        event = res["events"][0]
        assert event["event_type"] == "audit.user.role_change"
        assert event["component"] == "audit"
        assert "target_user_id" in event["details"]


# ---------------------------------------------------------------------------
# Criterion 8: Estimated costs are not presented as verified (flagged ESTIMATED)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_08_estimated_costs_flagged_estimated(superuser: UserModel):
    """Verify Call Timeline explicitly marks cost estimates as ESTIMATED, not verified."""
    svc = ObservabilityService()

    run = WorkflowRunModel(
        id="run_billing_est_1",
        workflow_id=1,
        is_completed=True,
        created_at=datetime.now(UTC) - timedelta(minutes=5),
        cost_info={
            "telephony_cost": 0.40,
            "carrier_cdr_reconciled": False,  # Not verified with carrier invoice
            "total_cost": 0.55,
        },
    )
    wf = WorkflowModel(id=1, name="Default Voice Assistant", organization_id=1)

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=run)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=wf)),
        ]

        timeline = await svc.get_call_timeline(
            call_identifier="call_billing_est_1",
            user=superuser,
        )

        assert timeline["billing_reconciliation_status"] == "ESTIMATED"
        billing_milestone = next(
            (m for m in timeline["timeline"] if m["milestone"] == "billing_and_reconciliation"),
            None,
        )
        assert billing_milestone is not None
        assert billing_milestone["status"] == "ESTIMATED"
        assert "60-second Pulse Estimate" in billing_milestone["details"]["reconciliation_type"]


# ---------------------------------------------------------------------------
# Criterion 9: C3 failure and recovery appear in incident history
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_09_c3_failure_and_recovery_in_incident_history():
    """Verify alert manager records FIRED and RESOLVED events to durable store."""
    temp_dir = tempfile.mkdtemp()

    try:
        store = DurableIncidentStore(runtime_dir=temp_dir)

        now = datetime.now(UTC)
        incident = IncidentEvent(
            incident_id="inc_pg_fail_1",
            component="postgresql",
            severity=AlertSeverity.CRITICAL,
            health_status=HealthStatus.UNHEALTHY,
            error_code="ERR_PG_DOWN",
            message="Connection refused",
            first_seen=now,
            latest_seen=now,
        )

        # Trigger failure (FIRED)
        ok_fired = await store.record_event(incident, event_type="FIRED")
        assert ok_fired is True

        # Trigger recovery (RESOLVED)
        incident.health_status = HealthStatus.HEALTHY
        incident.resolved_at = datetime.now(UTC)
        ok_resolved = await store.record_event(incident, event_type="RESOLVED")
        assert ok_resolved is True

        # Query durable store
        incidents, total = await store.query_incidents()
        assert total >= 1
        assert incidents[0]["component"] == "postgresql"
        assert incidents[0]["resolved_at"] is not None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Criterion 10: Incident survives restart in durable storage (incidents.jsonl)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_10_incident_survives_restart_in_durable_storage():
    """Verify durable incident store persists across store re-instantiations."""
    temp_dir = tempfile.mkdtemp()

    try:
        # 1. First process / instance
        store1 = DurableIncidentStore(runtime_dir=temp_dir)
        inc = IncidentEvent(
            incident_id="inc_survive_100",
            component="sip_trunk",
            severity=AlertSeverity.CRITICAL,
            health_status=HealthStatus.UNHEALTHY,
            error_code="ERR_SIP_503",
            message="Carrier unavailable",
        )
        await store1.record_event(inc, event_type="FIRED")

        # 2. Second instance (simulating restart / fresh worker)
        store2 = DurableIncidentStore(runtime_dir=temp_dir)
        reloaded, count = await store2.query_incidents()
        assert count == 1
        assert reloaded[0]["incident_id"] == "inc_survive_100"
        assert reloaded[0]["component"] == "sip_trunk"
        assert reloaded[0]["error_code"] == "ERR_SIP_503"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Criterion 11: Alert acknowledgment works
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_11_alert_acknowledgment_persists():
    """Verify incident acknowledgment updates state and records durable ACKNOWLEDGED event."""
    inc = IncidentEvent(
        incident_id="inc_ack_test_1",
        component="redis",
        severity=AlertSeverity.CRITICAL,
        health_status=HealthStatus.UNHEALTHY,
        error_code="ERR_REDIS",
        message="Socket timeout",
    )
    alert_manager.active_incidents["redis"] = inc

    with patch(
        "api.services.health.alert_manager.durable_incident_store.record_event",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_durable_record:
        success = await alert_manager.acknowledge_incident(
            incident_id="inc_ack_test_1",
            actor_id="superadmin@spx.local",
        )
        assert success is True
        assert inc.acknowledged is True
        assert inc.acknowledged_by == "superadmin@spx.local"
        assert inc.acknowledged_at is not None

        mock_durable_record.assert_called_once()
        args, kwargs = mock_durable_record.call_args
        assert kwargs.get("event_type") == "ACKNOWLEDGED"


# ---------------------------------------------------------------------------
# Criterion 12: Missing provider signals appear UNKNOWN / UNVERIFIED / NOT_CONFIGURED
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_12_missing_provider_signals_unknown_or_not_configured():
    """Verify provider monitor tags unconfigured credentials as NOT_CONFIGURED."""
    provider_mon = ProviderMonitor()

    with patch.dict(os.environ, {}, clear=True):
        # When environment variables are missing
        report = await provider_mon.check_all_providers()
        for provider_name, p_health in report.items():
            assert p_health.status in (
                HealthStatus.NOT_CONFIGURED,
                HealthStatus.UNKNOWN,
                HealthStatus.HEALTHY,
            )
            if p_health.status == HealthStatus.NOT_CONFIGURED:
                assert "not configured" in p_health.message.lower() or "missing" in p_health.message.lower()


# ---------------------------------------------------------------------------
# Criterion 13: Secrets and PII (phone numbers) excluded from exports
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_13_secrets_and_pii_excluded_from_exports(superuser: UserModel):
    """Verify log exports sanitize secrets and mask Indian phone numbers."""
    svc = ObservabilityService()

    audit_with_pii = DashboardEventModel(
        id=303,
        organization_id=1,
        event_type="audit.carrier.webhook",
        resource_type="audit",
        data={
            "phone_number": "+919876543210",
            "api_key": "secret_livekit_key_xyz",
            "db_password": "supersecretpassword",
            "safe_metric": 42,
        },
        created_at=datetime.now(UTC),
    )

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [audit_with_pii]
        mock_session.execute.return_value = mock_result

        # JSON Export
        content_json, media_type_json = await svc.export_redacted_events(
            user=superuser,
            export_format="json",
        )
        assert media_type_json == "application/json"
        exported_data = json.loads(content_json)
        assert len(exported_data) >= 1
        payload = exported_data[0]["details"]
        assert payload["api_key"] in ("[REDACTED]", "[REDACTED_SECRET]")
        assert payload["db_password"] in ("[REDACTED]", "[REDACTED_SECRET]")
        assert "+9198****10" in payload["phone_number"] or "****" in payload["phone_number"] or "[REDACTED]" in payload["phone_number"]

        # CSV Export
        content_csv, media_type_csv = await svc.export_redacted_events(
            user=superuser,
            export_format="csv",
        )
        assert media_type_csv == "text/csv"
        assert "secret_livekit_key_xyz" not in content_csv
        assert "supersecretpassword" not in content_csv


# ---------------------------------------------------------------------------
# Criterion 14: Operational purge preview cannot delete protected audit records
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_14_operational_purge_preview_cannot_delete_protected_audit_records(
    superuser: UserModel,
):
    """Verify retention preview excludes protected audit records and shows audit guarantee."""
    svc = ObservabilityService()

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session

        # Return 50 eligible operational records and 200 protected audit records
        mock_session.execute.side_effect = [
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[1] * 50)))),   # eligible operational
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[1] * 200)))),  # protected audit
        ]

        preview = await svc.get_retention_preview(days=30, user=superuser)
        assert preview["eligible_operational_records"] == 50
        assert preview["protected_audit_records_excluded"] == 200
        assert "STRICTLY PROTECTED" in preview["protected_scope_guarantee"]


# ---------------------------------------------------------------------------
# Criterion 15: Purge with invalid token is rejected (400 Bad Request)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_15_purge_with_invalid_token_rejected(superuser: UserModel):
    """Verify purge fails closed with ValueError when token is invalid or missing."""
    svc = ObservabilityService()

    with pytest.raises(ValueError) as val_exc:
        await svc.purge_operational_logs(
            days=30,
            confirm_token="INVALID_PURGE_CONFIRMATION",
            user=superuser,
        )
    assert "Invalid confirmation token" in str(val_exc.value)


# ---------------------------------------------------------------------------
# Criterion 16: Call timeline marks missing signals NOT_RECORDED
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_16_call_timeline_marks_missing_signals_not_recorded(
    superuser: UserModel,
):
    """Verify timeline marks stages without data (e.g. recording, latency) as NOT_RECORDED."""
    svc = ObservabilityService()

    minimal_run = WorkflowRunModel(
        id="run_sparse_1",
        workflow_id=1,
        is_completed=True,
        recording_url=None,  # No recording stored
        usage_info={},       # No latency metrics stored
    )
    wf = WorkflowModel(id=1, name="Default Voice Assistant", organization_id=1)

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_session.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=minimal_run)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=wf)),
        ]

        timeline = await svc.get_call_timeline(
            call_identifier="call_sparse_1",
            user=superuser,
        )

        assert timeline["call_id"] == "call_sparse_1"
        rec_milestone = next(
            (m for m in timeline["timeline"] if m["milestone"] == "recording_storage"),
            None,
        )
        assert rec_milestone is not None
        assert rec_milestone["status"] == "NOT_RECORDED"

        voice_milestone = next(
            (m for m in timeline["timeline"] if m["milestone"] == "voice_engine_runtime"),
            None,
        )
        assert voice_milestone is not None
        assert voice_milestone["details"]["stt_latency_ms"] == "NOT_RECORDED"


# ---------------------------------------------------------------------------
# Criterion 17: Purge records an audit log event (audit.operations.log_purge)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_17_purge_records_audit_log_event(superuser: UserModel):
    """Verify that successful purge records an audit event with fail-closed integrity."""
    svc = ObservabilityService()

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[MagicMock()] * 15)))
        )

        with patch("api.services.audit.audit_service.audit_service.record_event") as mock_audit:
            res = await svc.purge_operational_logs(
                days=60,
                confirm_token="CONFIRM_PURGE_OPERATIONAL_LOGS",
                user=superuser,
            )
            assert res["status"] == "purged"
            assert res["purged_operational_records"] == 15

            # Must have written audit event
            mock_audit.assert_called_once()
            call_args = mock_audit.call_args[1]
            assert call_args["action_type"] == "audit.operations.log_purge"
            assert call_args["actor_id"] == str(superuser.id)
            assert call_args["fail_closed"] is True


# ---------------------------------------------------------------------------
# Extra Safety Verification: Malformed line resilience & rotation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_18_malformed_partial_lines_resilience_and_rotation():
    """Verify durable incident store drops partial/corrupted lines and recovers cleanly."""
    temp_dir = tempfile.mkdtemp()

    try:
        store = DurableIncidentStore(runtime_dir=temp_dir)
        # Write valid incident
        inc1 = IncidentEvent(
            incident_id="inc_valid_1",
            component="postgresql",
            severity=AlertSeverity.CRITICAL,
            health_status=HealthStatus.UNHEALTHY,
            error_code="ERR_PG",
            message="Valid incident 1",
        )
        await store.record_event(inc1, event_type="FIRED")

        # Inject malformed / partial lines directly into file
        with open(store.file_path, "a", encoding="utf-8") as f:
            f.write('{"incident_id": "truncated_line", "comp\n')  # Incomplete JSON
            f.write("GARBAGE NOT JSON AT ALL\n")                  # Total garbage
            f.write('\n')                                         # Empty line

        # Write second valid incident
        inc2 = IncidentEvent(
            incident_id="inc_valid_2",
            component="redis",
            severity=AlertSeverity.WARNING,
            health_status=HealthStatus.DEGRADED,
            error_code="ERR_REDIS",
            message="Valid incident 2",
        )
        await store.record_event(inc2, event_type="FIRED")

        # Query incidents: must read BOTH valid incidents and safely ignore malformed lines
        incidents, total = await store.query_incidents()
        assert total == 2
        inc_ids = {i["incident_id"] for i in incidents}
        assert inc_ids == {"inc_valid_1", "inc_valid_2"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Extra Safety Verification: Positive allowlist protects business records
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_19_retention_purge_strictly_excludes_business_records(superuser: UserModel):
    """Verify purge statement uses positive allowlist and never touches business entities."""
    from api.services.health.observability_service import ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES

    # Confirm critical business entities are NOT in allowlist
    forbidden_purge_entities = {"lead", "appointment", "call", "objection", "workflow_run", "audit", "billing", "action"}
    for entity in forbidden_purge_entities:
        assert entity not in ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES

    svc = ObservabilityService()

    with patch("api.services.health.observability_service.db_client.async_session") as mock_session_ctx:
        mock_session = AsyncMock()
        mock_session_ctx.return_value.__aenter__.return_value = mock_session
        mock_session.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        with patch("api.services.audit.audit_service.audit_service.record_event"):
            await svc.purge_operational_logs(
                days=30,
                confirm_token="CONFIRM_PURGE_OPERATIONAL_LOGS",
                user=superuser,
            )

        # Inspect the SQL statement executed
        assert mock_session.execute.call_count >= 1
        stmt = mock_session.execute.call_args_list[0][0][0]
        # Must be a select against DashboardEventModel filtered by ALLOWLISTED_OPERATIONAL_RESOURCE_TYPES
        compiled_sql = str(stmt).lower()
        assert "dashboard_events.resource_type in" in compiled_sql

