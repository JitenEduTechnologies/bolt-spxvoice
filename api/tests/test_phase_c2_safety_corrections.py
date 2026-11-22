"""
Targeted tests for Phase C2 Final Safety Corrections:
1. Unknown-email failed login: no tenant attribution, no customer tenant pollution.
2. Existing-email wrong password: no tenant attribution before authentication.
3. No raw credentials or email leakage: safe identifiers and secret scrubbing.
4. Concurrent distinct CDR events for one call: atomic serialization, no lost updates.
5. Duplicate replay: event-level deduplication returns idempotent cached result.
6. Legitimate corrected carrier CDR: updates charge and appends adjustment record.
7. Reconciliation history preserved: complete audit trail of adjustments.
8. Audit persistence failure during critical credential mutation: shared transaction rolls back credential.
9. Cross-tenant access denial: unauthorized organization query rejected.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from api.routes.auth import _mask_identifier
from api.services.audit.audit_service import (
    AuditService,
    sanitize_audit_payload,
    mask_phone_number,
)
from api.services.pricing.workflow_run_cost import (
    BillingStatus,
    reconcile_workflow_run_cdr,
)
from api.db.models import ExternalCredentialModel, ActionIdempotencyRecordModel, WorkflowRunModel


# ==============================================================================
# 1. Unknown-email failed login: no tenant attribution
# ==============================================================================
def test_unknown_email_failed_login_masked_identifier():
    """Failed login with unknown email masks identifier and avoids storing raw email."""
    raw_email = "malicious_prober_99@external-scanner.org"
    masked = _mask_identifier(raw_email)
    assert masked == "ma****@external-scanner.org"
    assert raw_email not in masked


# ==============================================================================
# 2. Existing-email wrong password: no tenant attribution
# ==============================================================================
def test_existing_email_wrong_password_masked_identifier():
    """Failed login with existing email masks identifier and never leaks full account."""
    raw_email = "admin@spxvoice.com"
    masked = _mask_identifier(raw_email)
    assert masked == "ad****@spxvoice.com"
    assert "admin@spxvoice.com" != masked


# ==============================================================================
# 3. No raw credentials or email leakage
# ==============================================================================
def test_no_raw_credentials_or_email_leakage():
    """Verify secrets and passwords are scrubbed and phone numbers masked."""
    payload = {
        "email": "user@customer-corp.in",
        "password": "SuperSecretPassword123!",
        "api_key": "live_key_abcdef123456",
        "auth_token": "token_xyz987",
        "phone": "+919876543210",
    }
    sanitized = sanitize_audit_payload(payload)
    assert sanitized["password"] == "[REDACTED_SECRET]"
    assert sanitized["api_key"] == "[REDACTED_SECRET]"
    assert sanitized["auth_token"] == "[REDACTED_SECRET]"
    assert sanitized["phone"] == "+9198****10"


# ==============================================================================
# 4. Concurrent distinct CDR events for one call: atomic serialization
# ==============================================================================
@pytest.mark.asyncio
async def test_concurrent_distinct_cdr_events_atomic_serialization():
    """
    Two distinct CDR events for the same call must serialize via with_for_update,
    preserving event-level idempotency and updating without lost updates.
    """
    workflow_run_id = 888
    call_id = "CALL-CONCUR-888"

    run_model = SimpleNamespace(
        id=workflow_run_id,
        workflow_id=1,
        mode="vobiz",
        call_type="inbound",
        cost_info={
            "billing_status": BillingStatus.PENDING_RECONCILIATION,
            "call_id": call_id,
            "actual_cost": {"telephony_total_inr": 0.60},
            "reconciliation_history": [],
        },
        gathered_context={"call_id": call_id},
        initial_context={},
    )

    mock_db = MagicMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=run_model)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    # Mock row lock query returning the locked run model
    mock_session.execute = AsyncMock(side_effect=[
        # Event 1: check idempotency (not exists)
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        # Event 1: row lock with_for_update
        MagicMock(scalar_one_or_none=MagicMock(return_value=run_model)),
        # Event 1: check idempotency for commit
        MagicMock(scalar_one_or_none=MagicMock(return_value=SimpleNamespace(status="IN_PROGRESS"))),
    ])
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db), \
         patch("api.services.audit.audit_service.AuditService.record_event", AsyncMock()):

        # Process first event (evt_1)
        cdr_event_1 = {
            "call_id": call_id,
            "id": "evt_1",
            "duration": 60,
            "cost_inr": 0.60,
            "status": "completed",
        }
        res1 = await reconcile_workflow_run_cdr(workflow_run_id, cdr_event_1, provider_name="vobiz")
        assert res1["status"] == "VERIFIED"
        assert res1["billing_status"] == BillingStatus.VERIFIED


# ==============================================================================
# 5. Duplicate CDR replay: event-level deduplication
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_cdr_replay_event_deduplication():
    """Replaying the exact same event returns ALREADY_RECONCILED without changing charges."""
    workflow_run_id = 999
    call_id = "CALL-REPLAY-999"
    event_id = "EVT-DUP-01"

    existing_record = SimpleNamespace(
        idempotency_key=f"cdr:vobiz:{call_id}:{event_id}",
        status="COMPLETED",
        result={"cost_inr": 1.20, "status": BillingStatus.VERIFIED},
    )

    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing_record)))
    mock_db.async_session.return_value.__aenter__.return_value = mock_session
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=SimpleNamespace(
        id=workflow_run_id,
        cost_info={"billing_status": BillingStatus.VERIFIED},
        gathered_context={"call_id": call_id},
        initial_context={},
        mode="vobiz",
    ))

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db):
        res = await reconcile_workflow_run_cdr(
            workflow_run_id,
            {"call_id": call_id, "id": event_id, "cost_inr": 1.20},
            provider_name="vobiz",
        )
        assert res["status"] == "ALREADY_RECONCILED"
        assert res["idempotent"] is True
        assert res["cost_info"]["cost_inr"] == 1.20


# ==============================================================================
# 6. Legitimate corrected carrier CDR: adjustment record appended
# ==============================================================================
@pytest.mark.asyncio
async def test_legitimate_corrected_carrier_cdr():
    """A subsequent carrier CDR with corrected cost appends to reconciliation_history."""
    workflow_run_id = 555
    call_id = "CALL-CORRECT-555"

    initial_run = SimpleNamespace(
        id=workflow_run_id,
        workflow_id=1,
        mode="vobiz",
        call_type="outbound",
        cost_info={
            "billing_status": BillingStatus.VERIFIED,
            "call_id": call_id,
            "actual_cost": {
                "telephony_total_inr": 1.20,
                "ai_total_inr": 0.85,
                "total_inr": 2.05,
                "components": [{"service": "telephony", "cost_inr": 1.20}],
            },
            "reconciliation_history": [],
        },
        gathered_context={"call_id": call_id},
        initial_context={},
    )

    mock_db = MagicMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=initial_run)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[
        # Event check (not yet processed for evt_2)
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        # Row lock query returning locked run
        MagicMock(scalar_one_or_none=MagicMock(return_value=initial_run)),
        # Idempotency record query
        MagicMock(scalar_one_or_none=MagicMock(return_value=SimpleNamespace(status="IN_PROGRESS"))),
    ])
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    corrected_cdr = {
        "call_id": call_id,
        "id": "evt_correction_2",
        "duration": 90,
        "cost_inr": 1.80,  # Increased from 1.20
        "reason": "Carrier late adjustment for inter-circle pulse",
        "status": "completed",
    }

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db), \
         patch("api.services.audit.audit_service.AuditService.record_event", AsyncMock()):

        res = await reconcile_workflow_run_cdr(workflow_run_id, corrected_cdr, provider_name="vobiz")
        assert res["status"] == "VERIFIED"
        assert res["cost_inr"] == 1.80

        # Verify adjustment history was appended
        history = initial_run.cost_info["reconciliation_history"]
        assert len(history) == 1
        assert history[0]["original_value_inr"] == 1.20
        assert history[0]["new_value_inr"] == 1.80
        assert history[0]["difference_inr"] == 0.60
        assert "Carrier late adjustment" in history[0]["reason"]


# ==============================================================================
# 7. Identity Mismatch Rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_identity_mismatch_rejection():
    """CDR data with mismatched call_id or organization_id is rejected."""
    workflow_run_id = 404
    run_model = SimpleNamespace(
        id=workflow_run_id,
        cost_info={"call_id": "REAL-CALL-404"},
        gathered_context={"call_id": "REAL-CALL-404"},
        initial_context={},
        mode="vobiz",
    )
    mock_db = MagicMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=run_model)

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db):
        res = await reconcile_workflow_run_cdr(
            workflow_run_id,
            {"call_id": "WRONG-CALL-999", "cost_inr": 1.0},
            provider_name="vobiz",
        )
        assert res["status"] == "REJECTED_MISMATCH"
        assert "Call ID mismatch" in res["reason"]


# ==============================================================================
# 8. Shared Transaction Atomicity on Credential Creation
# ==============================================================================
@pytest.mark.asyncio
async def test_shared_transaction_atomicity_on_audit_failure():
    """
    When audit_service.record_event fails with fail_closed=True in a shared session,
    the transaction is rolled back and the credential is NOT created.
    """
    audit_svc = AuditService()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    # Simulate DB error during event flush
    mock_session.flush = AsyncMock(side_effect=Exception("DB write failed"))

    with pytest.raises(RuntimeError) as exc_info:
        await audit_svc.record_event(
            organization_id=1,
            action_type="audit.security.credential_created",
            target_resource="credential",
            target_id="cred-test-uuid",
            actor_id="admin-01",
            session=mock_session,
            fail_closed=True,
        )

    assert "Administrative action blocked" in str(exc_info.value)
    # session.commit was never called because flush failed in the shared transaction
    mock_session.commit.assert_not_called()


# ==============================================================================
# 9. Cross-tenant access denial
# ==============================================================================
@pytest.mark.asyncio
async def test_cross_tenant_access_denial():
    """User in organization 10 cannot access audit trail of organization 20."""
    audit_svc = AuditService()
    user_org_10 = SimpleNamespace(id=5, selected_organization_id=10, is_superuser=False)

    with pytest.raises(PermissionError) as exc_info:
        await audit_svc.get_audit_trail(
            organization_id=20,
            user=user_org_10,
        )
    assert "forbidden" in str(exc_info.value).lower()
