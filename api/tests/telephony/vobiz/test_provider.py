import hashlib
import hmac
import time
import uuid

import pytest

from api.services.telephony.providers.vobiz.provider import VobizProvider


def _provider() -> VobizProvider:
    return VobizProvider(
        {
            "auth_id": "MA_TEST",
            "auth_token": "test-auth-token",
            "application_id": "APP_TEST",
            "from_numbers": ["910000000000"],
        }
    )


def _body() -> str:
    return (
        "CallUUID=call-test&From=910000000001&To=910000000000&"
        "Direction=inbound&CallStatus=ringing&ParentAuthID=MA_TEST"
    )


def _payload() -> dict[str, str]:
    return {
        "CallUUID": "call-test",
        "From": "910000000001",
        "To": "910000000000",
        "Direction": "inbound",
        "CallStatus": "ringing",
        "ParentAuthID": "MA_TEST",
    }


def _signed_headers(body: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-auth-token",
        f"{timestamp}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-vobiz-signature": signature,
        "x-vobiz-timestamp": timestamp,
    }


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_unsigned_vobiz_webhook():
    result = await _provider().verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        _payload(),
        {},
        _body(),
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_incomplete_vobiz_headers():
    result = await _provider().verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        _payload(),
        {"x-vobiz-signature": "present-without-timestamp"},
        _body(),
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_accepts_valid_complete_vobiz_headers():
    body = _body()

    result = await _provider().verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        _payload(),
        _signed_headers(body),
        body,
    )

    assert result is True


@pytest.mark.asyncio
async def test_verify_inbound_signature_rejects_invalid_complete_vobiz_headers():
    body = _body()
    headers = _signed_headers(body)
    headers["x-vobiz-signature"] = "invalid"

    result = await _provider().verify_inbound_signature(
        "https://example.test/api/v1/telephony/inbound/run",
        _payload(),
        headers,
        body,
    )

    assert result is False


def test_normalize_call_cost_uses_current_inr_cdr_fields():
    result = VobizProvider._normalize_call_cost(
        {
            "data": {
                "billsec": 42,
                "cost": 0.45,
                "currency": "INR",
                "duration": 47,
                "status": "completed",
                "total_cost": 0.45,
            },
            "success": True,
        },
        source="cdr",
    )

    assert result["currency"] == "INR"
    assert result["cost_inr"] == 0.45
    assert result["cost_usd"] == 0.0
    assert result["duration"] == 42
    assert result["source"] == "cdr"


def test_normalize_call_cost_handles_legacy_minor_unit_cdr_fields():
    result = VobizProvider._normalize_call_cost(
        {
            "cost": 156,
            "currency": "USD",
            "ratePerMinute": 30,
            "billableSeconds": 312,
            "status": "answered",
        },
        source="legacy_call_endpoint",
    )

    assert result["currency"] == "USD"
    assert result["cost_usd"] == 1.56
    assert result["rate_usd_per_minute"] == 0.3
    assert result["duration"] == 312


# =============================================================================
# Vobiz V3 Webhook Authentication & Replay Protection Tests
# =============================================================================

@pytest.mark.asyncio
async def test_verify_v3_signature_valid():
    """Valid V3 HMAC-SHA256 signature with nonce is accepted."""
    provider = _provider()
    url = "https://example.test/api/v1/telephony/vobiz/hangup-callback/101?query=ignored"
    nonce = f"nonce-test-{uuid.uuid4().hex}"

    expected_sig = provider.compute_v3_signature("https://example.test/api/v1/telephony/vobiz/hangup-callback/101", nonce)

    result = await provider.verify_webhook_signature(
        url=url,
        params={"call_uuid": "call-1"},
        signature=expected_sig,
        nonce=nonce,
    )
    assert result is True


@pytest.mark.asyncio
async def test_verify_v3_signature_tampered_rejected():
    """Tampered V3 signature is rejected."""
    provider = _provider()
    url = "https://example.test/api/v1/telephony/vobiz/hangup-callback/101"
    nonce = "nonce-test-12345-tampered"

    result = await provider.verify_webhook_signature(
        url=url,
        params={},
        signature="invalid-base64-signature",
        nonce=nonce,
    )
    assert result is False


@pytest.mark.asyncio
async def test_verify_v3_signature_nonce_replay_rejected():
    """Replaying the exact same nonce is rejected by replay protection."""
    provider = _provider()
    url = "https://example.test/api/v1/telephony/vobiz/hangup-callback/101"
    nonce = f"nonce-replay-{int(time.time())}"

    sig = provider.compute_v3_signature(url, nonce)

    # First attempt: fresh nonce -> PASS
    first = await provider.verify_webhook_signature(
        url=url,
        params={},
        signature=sig,
        nonce=nonce,
    )
    assert first is True

    # Second attempt: same nonce -> REPLAY REJECTED
    second = await provider.verify_webhook_signature(
        url=url,
        params={},
        signature=sig,
        nonce=nonce,
    )
    assert second is False


@pytest.mark.asyncio
async def test_verify_v3_signature_missing_auth_token_fails_closed():
    """Provider without auth_token fails closed."""
    unconfigured = VobizProvider({"auth_id": "MA_TEST", "from_numbers": ["910000000000"]})
    result = await unconfigured.verify_webhook_signature(
        url="https://example.test/callback",
        params={},
        signature="any",
        nonce="any-nonce",
    )
    assert result is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_v3_valid_and_tampered():
    """Inbound webhook route verifies V3 headers and rejects tampered V3 headers."""
    provider = _provider()
    url = "https://example.test/api/v1/telephony/inbound/run"
    nonce = f"nonce-inbound-{uuid.uuid4().hex}"
    sig = provider.compute_v3_signature(url, nonce)

    # Valid V3
    headers_valid = {
        "x-vobiz-signature-v3": sig,
        "x-vobiz-signature-v3-nonce": nonce,
    }
    res_valid = await provider.verify_inbound_signature(url, _payload(), headers_valid)
    assert res_valid is True

    # Tampered V3
    headers_tampered = {
        "x-vobiz-signature-v3": "tampered",
        "x-vobiz-signature-v3-nonce": "nonce-inbound-tampered",
    }
    res_tampered = await provider.verify_inbound_signature(url, _payload(), headers_tampered)
    assert res_tampered is False

    # Incomplete V3 (missing nonce)
    headers_incomplete = {
        "x-vobiz-signature-v3": sig,
    }
    res_incomplete = await provider.verify_inbound_signature(url, _payload(), headers_incomplete)
    assert res_incomplete is False


# =============================================================================
# Vobiz XML Verbs & IVR Compatibility Tests
# =============================================================================

def test_vobiz_xml_speak():
    """Vobiz XML helper generates valid <Speak voice="WOMAN"> XML."""
    xml = VobizProvider.generate_speak_xml("Hello caller, welcome to SPX Voice.", voice="WOMAN")
    assert "<Speak voice=\"WOMAN\"" in xml
    assert "Hello caller, welcome to SPX Voice." in xml
    assert "<Say" not in xml


def test_vobiz_xml_play():
    """Vobiz XML helper generates valid <Play> for audio URLs."""
    xml = VobizProvider.generate_play_xml("https://cdn.example.com/audio/menu.wav")
    assert "<Play>https://cdn.example.com/audio/menu.wav</Play>" in xml
    assert "<Speak" not in xml


def test_vobiz_xml_gather_with_speak_and_play():
    """Vobiz XML Gather generates proper action, method, numDigits, and nested Speak/Play."""
    # Text greeting -> nested Speak
    xml_speak = VobizProvider.generate_gather_xml(
        action_url="https://example.test/api/v1/telephony/vobiz/gather",
        prompt_or_url="Press 1 for sales or 2 for support",
        timeout=6,
        num_digits=1,
        method="POST",
    )
    assert '<Gather action="https://example.test/api/v1/telephony/vobiz/gather" method="POST" numDigits="1" timeout="6"' in xml_speak
    assert '<Speak voice="WOMAN">Press 1 for sales or 2 for support</Speak>' in xml_speak

    # Audio URL greeting -> nested Play
    xml_play = VobizProvider.generate_gather_xml(
        action_url="https://example.test/api/v1/telephony/vobiz/gather",
        prompt_or_url="https://cdn.example.com/ivr/intro.mp3",
        timeout=5,
        num_digits=2,
    )
    assert '<Gather action="https://example.test/api/v1/telephony/vobiz/gather" method="POST" numDigits="2" timeout="5"' in xml_play
    assert "<Play>https://cdn.example.com/ivr/intro.mp3</Play>" in xml_play
