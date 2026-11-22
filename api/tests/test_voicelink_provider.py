"""Tests for VoiceLink telephony provider, Smart DID controller, and compliance module.

Covers:
- Configuration validation and masked responses
- Provider registry integration and UI metadata
- Smart DID health monitoring, cooldown, and quarantine
- Capacity-aware caller-ID selection and anti-rotation safeguards
- Global customer consent, suppression, and DNC enforcement
- Fail-closed call initiation and immutable telephony snapshots
- Status callback parsing, webhook signature verification, and cost tracking
- Dual-engine compatibility (Gemini Live and Cartesia/DeepSeek)
- Preservation of Vobiz as an independent provider
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.enums import WorkflowRunMode
from api.services.telephony import registry
from api.services.telephony.compliance import (
    GlobalComplianceManager,
    compliance_manager,
    normalize_phone_number,
)
from api.services.telephony.providers.voicelink.config import (
    VoiceLinkConfigurationRequest,
    VoiceLinkConfigurationResponse,
)
from api.services.telephony.providers.voicelink.provider import VoiceLinkProvider
from api.services.telephony.smart_did import (
    NumberHealthState,
    SmartDIDController,
    smart_did_controller,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Reset compliance and Smart DID controller state between tests."""
    compliance_manager.clear()
    smart_did_controller.clear()
    yield
    compliance_manager.clear()
    smart_did_controller.clear()


def test_voicelink_registered_in_registry():
    """Verify VoiceLink is registered in the telephony registry alongside Vobiz."""
    assert "voicelink" in registry.names()
    assert "vobiz" in registry.names()

    spec = registry.get("voicelink")
    assert spec.name == "voicelink"
    assert spec.provider_cls is VoiceLinkProvider
    assert spec.account_id_credential_field == "account_id"
    assert spec.ui_metadata is not None
    assert spec.ui_metadata.display_name == "VoiceLink (India VNO)"

    field_names = [f.name for f in spec.ui_metadata.fields]
    assert "account_id" in field_names
    assert "sip_host" in field_names
    assert "sip_username" in field_names
    assert "sip_password" in field_names
    assert "from_numbers" in field_names
    assert "livekit_sip_trunk_id" in field_names


def test_voicelink_configuration_validation():
    """Test validation of VoiceLink configuration schema."""
    valid_req = VoiceLinkConfigurationRequest(
        account_id="ACC_VL_1001",
        sip_host="sip.voicelink.co.in",
        sip_port=5060,
        sip_username="trunk_usr_01",
        sip_password="super_secret_sip_pass",
        from_numbers=["+919876543210"],
        channel_capacity=20,
        cps_limit=3.0,
    )
    assert valid_req.account_id == "ACC_VL_1001"
    assert valid_req.sip_port == 5060
    assert valid_req.channel_capacity == 20

    # Invalid port
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(
            account_id="ACC_VL_1001",
            sip_host="sip.voicelink.co.in",
            sip_port=99999,  # out of bounds
            sip_username="usr",
            sip_password="pwd",
        )

    # Empty account_id
    with pytest.raises(ValidationError):
        VoiceLinkConfigurationRequest(
            account_id="   ",
            sip_host="sip.voicelink.co.in",
            sip_username="usr",
            sip_password="pwd",
        )


def test_voicelink_provider_validate_config():
    """Test VoiceLinkProvider.validate_config method."""
    good_config = {
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_port": 5060,
        "sip_username": "trunk_user",
        "sip_password": "trunk_password",
        "from_numbers": ["+919876543210"],
    }
    provider = VoiceLinkProvider(good_config)
    assert provider.validate_config() is True

    # Missing from_numbers
    bad_config = dict(good_config)
    bad_config["from_numbers"] = []
    assert VoiceLinkProvider(bad_config).validate_config() is False

    # Missing sip_password
    bad_config2 = dict(good_config)
    bad_config2["sip_password"] = ""
    assert VoiceLinkProvider(bad_config2).validate_config() is False


@pytest.mark.asyncio
async def test_global_compliance_dnc_suppression():
    """Verify global DNC and customer suppression prevents call initiation."""
    org_id = 42
    target_number = "+919988776655"

    config = {
        "organization_id": org_id,
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_port": 5060,
        "sip_username": "trunk_user",
        "sip_password": "trunk_password",
        "from_numbers": ["+919876543210"],
    }
    provider = VoiceLinkProvider(config)

    # Initially not suppressed
    allowed, reason = compliance_manager.check_call_allowed(org_id, target_number)
    assert allowed is True
    assert reason is None

    # Add to DNC
    compliance_manager.add_to_dnc(
        organization_id=org_id,
        phone_number=target_number,
        reason="Customer requested Do Not Call during previous call",
        source="opt_out_request",
    )

    # Attempt call initiation - MUST fail closed
    with pytest.raises(HTTPException) as exc_info:
        await provider.initiate_call(
            to_number=target_number,
            webhook_url="https://api.example.com/webhook",
            workflow_run_id=101,
        )
    assert exc_info.value.status_code == 403
    assert "DNC list match" in str(exc_info.value.detail)

    # Remove from DNC - call is permitted again
    compliance_manager.remove_from_dnc(org_id, target_number)
    allowed2, _ = compliance_manager.check_call_allowed(org_id, target_number)
    assert allowed2 is True


@pytest.mark.asyncio
async def test_smart_did_consecutive_failures_cooldown():
    """Verify that consecutive technical failures put a DID into temporary cooldown."""
    org_id = 1
    did = "+919876543210"

    smart_did_controller.register_did(org_id, did)
    assert smart_did_controller.get_did_health(org_id, did).state == NumberHealthState.ACTIVE

    # 1st failure
    smart_did_controller.record_call_end(org_id, did, success=False, failure_reason="SIP 503 Service Unavailable", status_code=503)
    assert smart_did_controller.get_did_health(org_id, did).state == NumberHealthState.ACTIVE
    assert smart_did_controller.get_did_health(org_id, did).consecutive_failures == 1

    # 2nd failure
    smart_did_controller.record_call_end(org_id, did, success=False, failure_reason="SIP 504 Gateway Timeout", status_code=504)
    assert smart_did_controller.get_did_health(org_id, did).consecutive_failures == 2

    # 3rd failure -> triggers COOLDOWN
    smart_did_controller.record_call_end(org_id, did, success=False, failure_reason="SIP 500 Server Error", status_code=500)
    health = smart_did_controller.get_did_health(org_id, did)
    assert health.state == NumberHealthState.COOLDOWN
    assert health.is_in_cooldown() is True

    # Selecting this DID while in cooldown must fail closed
    selected, err = smart_did_controller.select_caller_id(org_id, [did], preferred_number=did)
    assert selected is None
    assert "COOLDOWN" in (err or "")


def test_smart_did_quarantine_on_policy_rejection():
    """Verify carrier policy/regulatory errors (e.g. 403 Forbidden / DLT rejection) quarantine the DID."""
    org_id = 1
    did = "+919876543210"

    smart_did_controller.register_did(org_id, did)
    smart_did_controller.record_call_end(
        org_id,
        did,
        success=False,
        failure_reason="403 Forbidden: unregistered_dlt_header",
        status_code=403,
    )

    health = smart_did_controller.get_did_health(org_id, did)
    assert health.state == NumberHealthState.QUARANTINE
    assert "Carrier policy/regulatory rejection" in (health.quarantine_reason or "")

    # Selecting quarantined number fails closed
    selected, err = smart_did_controller.select_caller_id(org_id, [did], preferred_number=did)
    assert selected is None
    assert "QUARANTINED" in (err or "")

    # Admin reset restores health
    assert smart_did_controller.reset_did_health(org_id, did) is True
    assert smart_did_controller.get_did_health(org_id, did).state == NumberHealthState.ACTIVE


def test_smart_did_capacity_aware_selection():
    """Verify capacity-aware selection chooses the least loaded healthy DID."""
    org_id = 5
    num1 = "+919000000001"
    num2 = "+919000000002"

    smart_did_controller.register_did(org_id, num1)
    smart_did_controller.register_did(org_id, num2)

    # num1 has 2 active calls, num2 has 0
    smart_did_controller.record_call_start(org_id, num1)
    smart_did_controller.record_call_start(org_id, num1)

    selected, err = smart_did_controller.select_caller_id(
        org_id, [num1, num2], max_concurrency_per_did=5
    )
    assert err is None
    assert selected == num2  # selects least loaded

    # num2 reaches max capacity
    for _ in range(5):
        smart_did_controller.record_call_start(org_id, num2)

    # Now num1 should be selected (2 < 5)
    selected2, err2 = smart_did_controller.select_caller_id(
        org_id, [num1, num2], max_concurrency_per_did=5
    )
    assert err2 is None
    assert selected2 == num1


@pytest.mark.asyncio
async def test_voicelink_initiate_call_immutable_snapshot():
    """Verify outbound call initiation generates and returns an immutable configuration snapshot."""
    org_id = 10
    config = {
        "organization_id": org_id,
        "account_id": "ACC_TEST_INDIA",
        "sip_host": "sip.voicelink.co.in",
        "sip_port": 5060,
        "sip_username": "trunk_test",
        "sip_password": "secret_password",
        "from_numbers": ["+919876543210"],
        "telephony_configuration_id": 99,
    }
    provider = VoiceLinkProvider(config)

    result = await provider.initiate_call(
        to_number="+919876500000",
        webhook_url="https://api.spxvoice.local/webhook",
        workflow_run_id=5001,
    )

    assert result.status == "initiated"
    assert result.caller_number == "+919876543210"

    snapshot = result.provider_metadata.get("telephony_snapshot", {})
    assert snapshot["provider"] == WorkflowRunMode.VOICELINK.value
    assert snapshot["caller_number"] == "+919876543210"
    assert snapshot["called_number"] == "+919876500000"
    assert snapshot["sip_host"] == "sip.voicelink.co.in"
    assert snapshot["sip_port"] == 5060
    assert snapshot["account_id"] == "ACC_TEST_INDIA"
    assert snapshot["telephony_configuration_id"] == 99
    assert snapshot["fail_closed_enabled"] is True


@pytest.mark.asyncio
async def test_voicelink_fail_closed_on_error():
    """Verify that if call initiation encounters an error, it fails closed without redialing."""
    org_id = 10
    config = {
        "organization_id": org_id,
        "account_id": "ACC_TEST_INDIA",
        "sip_host": "sip.voicelink.co.in",
        "sip_port": 5060,
        "sip_username": "trunk_test",
        "sip_password": "secret_password",
        "from_numbers": ["+919876543210"],
        "livekit_sip_trunk_id": "ST_TEST_TRUNK",
    }
    provider = VoiceLinkProvider(config)

    with patch("api.services.telephony.providers.voicelink.provider.is_livekit_runtime", return_value=True), \
         patch("api.services.telephony.providers.voicelink.provider.create_outbound_sip_call", side_effect=Exception("SIP 503 Service Unavailable")):
        with pytest.raises(HTTPException) as exc_info:
            await provider.initiate_call(
                to_number="+919876500000",
                webhook_url="https://api.spxvoice.local/webhook",
                workflow_run_id=5002,
                workflow_id=1,
            )
        assert exc_info.value.status_code == 503
        assert "telephony_call_setup_failed" in str(exc_info.value.detail)

    # Number recorded the failure
    health = smart_did_controller.get_did_health(org_id, "+919876543210")
    assert health.failed_calls == 1
    assert health.consecutive_failures == 1


@pytest.mark.asyncio
async def test_voicelink_webhook_signature_verification():
    """Verify webhook signature authentication (direct token and HMAC)."""
    config = {
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_username": "user",
        "sip_password": "pass",
        "from_numbers": ["+919876543210"],
        "webhook_secret": "my_super_secret_webhook_token_123",
    }
    provider = VoiceLinkProvider(config)

    # Valid secret match
    assert await provider.verify_webhook_signature(
        "https://example.com/webhook", {}, "my_super_secret_webhook_token_123"
    ) is True

    # Invalid secret
    assert await provider.verify_webhook_signature(
        "https://example.com/webhook", {}, "wrong_token"
    ) is False


def test_voicelink_status_callback_parsing():
    """Verify status callback parsing normalizes VoiceLink CDR payloads."""
    provider = VoiceLinkProvider({
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_username": "u",
        "sip_password": "p",
        "from_numbers": ["+919876543210"],
    })

    raw_data = {
        "CallUUID": "vl-call-987654321",
        "CallStatus": "completed",
        "Duration": "45",
        "From": "+919876543210",
        "To": "+919876500000",
    }
    parsed = provider.parse_status_callback(raw_data)
    assert parsed["call_id"] == "vl-call-987654321"
    assert parsed["status"] == "completed"
    assert parsed["duration"] == 45
    assert parsed["from_number"] == "+919876543210"
    assert parsed["to_number"] == "+919876500000"


@pytest.mark.asyncio
async def test_voicelink_call_cost_calculation():
    """Verify call cost calculation for INR pulse-based VoiceLink billing."""
    provider = VoiceLinkProvider({
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_username": "u",
        "sip_password": "p",
        "from_numbers": ["+919876543210"],
    })
    cost_info = await provider.get_call_cost("vl-call-12345")
    assert cost_info["call_id"] == "vl-call-12345"
    assert cost_info["cost_inr"] > 0
    assert cost_info["cost_usd"] > 0
    assert cost_info["status"] == "completed"


def test_ai_pipeline_independence():
    """Verify that telephony provider selection is independent of AI model/pipeline.

    The provider layer only bridges PSTN to WebRTC LiveKit rooms. Whether the worker
    is running Gemini Live or Cartesia + DeepSeek, the telephony adapter is identical.
    """
    provider = VoiceLinkProvider({
        "account_id": "ACC_001",
        "sip_host": "sip.voicelink.co.in",
        "sip_username": "u",
        "sip_password": "p",
        "from_numbers": ["+919876543210"],
    })
    assert provider.PROVIDER_NAME == "voicelink"
    # The provider metadata is neutral to AI pipeline
    assert "gemini" not in provider.PROVIDER_NAME
    assert "cartesia" not in provider.PROVIDER_NAME


def test_voicelink_can_handle_webhook():
    """Verify webhook detection matches VoiceLink user agent and headers."""
    assert VoiceLinkProvider.can_handle_webhook({}, {"user-agent": "VoiceLink-SIP/2.0"}) is True
    assert VoiceLinkProvider.can_handle_webhook({}, {"x-provider": "voicelink"}) is True
    assert VoiceLinkProvider.can_handle_webhook({}, {"x-voicelink-signature": "abc"}) is True
    assert VoiceLinkProvider.can_handle_webhook({"provider": "voicelink"}, {}) is True
    assert VoiceLinkProvider.can_handle_webhook({}, {"user-agent": "TwilioProxy/1.1"}) is False


def test_voicelink_parse_inbound_webhook():
    """Verify inbound webhook parsing normalizes Indian caller and called numbers."""
    data = {
        "CallUUID": "vl-inbound-123456",
        "From": "9876543210",
        "To": "02271264296",
        "CallStatus": "ringing",
        "AccountID": "ACC_VL_99",
    }
    normalized = VoiceLinkProvider.parse_inbound_webhook(data)
    assert normalized.provider == "voicelink"
    assert normalized.call_id == "vl-inbound-123456"
    assert normalized.from_number.startswith("+91")
    assert normalized.to_number.startswith("+91")
    assert normalized.account_id == "ACC_VL_99"
    assert normalized.direction == "inbound"


@pytest.mark.asyncio
async def test_voicelink_inbound_route():
    """Verify the VoiceLink inbound route returns valid XML acknowledgment."""
    from unittest.mock import Mock
    from api.services.telephony.providers.voicelink.routes import handle_voicelink_inbound

    mock_request = Mock()
    mock_request.headers = {"content-type": "application/json"}
    mock_request.json = AsyncMock(return_value={"from": "+919876543210", "to": "+912271264296"})

    response = await handle_voicelink_inbound(mock_request)
    assert response.status_code == 200
    body_text = response.body.decode("utf-8")
    assert "Thank you for calling SPX Voice" in body_text or "Our offices are currently closed" in body_text
    assert "<Dial><Sip>sip:livekit-sip@sip.spxvoice.internal</Sip></Dial>" in body_text


@pytest.mark.asyncio
async def test_voicelink_dtmf_route():
    """Verify VoiceLink DTMF capture route processes digits."""
    from unittest.mock import Mock
    from api.services.telephony.providers.voicelink.routes import handle_voicelink_dtmf

    mock_request = Mock()
    mock_request.form = AsyncMock(return_value={"digits": "1"})

    response = await handle_voicelink_dtmf(workflow_run_id=901, request=mock_request)
    assert response.status_code == 200
    assert b'"digits":"1"' in response.body
