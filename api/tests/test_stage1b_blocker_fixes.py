"""Targeted tests for Stage 1B Blocker Fixes.

Covers:
1. crypto.py environment variable resolution, single/plural aliases, and conflict rejection.
2. Vobiz credential encryption on all write paths and in-memory decryption on read paths.
3. workflow_recording.py: correct db_client.get_recording_by_recording_id method usage.
4. Authenticated playback streaming with HTTP 200, HTTP 206 Partial Content (Range seeking).
5. Tenant denial, expired/tampered tokens, and missing authentication.
6. Browser playback URL: prevention of Docker-internal http://api:8000 exposure.
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.s3_signed_url import router as s3_router
from api.routes.workflow_recording import (
    create_playback_token,
    router as recording_router,
)
from api.routes.livekit import _upsert_vobiz_setup_config, VobizLiveKitSetupRequest
from api.routes.organization import _detail_response
from api.services.auth.depends import get_user
from api.services.livekit.vobiz import (
    list_vobiz_account_phone_numbers,
    verify_vobiz_credentials,
    _import_vobiz_phone_numbers,
)
from api.services.telephony.crypto_helper import (
    decrypt_provider_credentials,
    encrypt_provider_credentials,
    is_all_sensitive_encrypted,
)
from api.utils.crypto import (
    CryptoConfigurationError,
    _load_keys,
    decrypt_string,
    encrypt_string,
    is_encrypted,
)


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """Ensure clean test keys and environment."""
    key1 = Fernet.generate_key().decode("ascii")
    key2 = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{key1},k2:{key2}")
    monkeypatch.setenv("OSS_JWT_SECRET", "stage-1b-test-secret-key-2026")
    monkeypatch.setenv("ENVIRONMENT", "test")


# ==============================================================================
# 1. CRYPTO ENVIRONMENT-VARIABLE HANDLING TESTS
# ==============================================================================

def test_crypto_env_single_and_alias_support(monkeypatch):
    """crypto._load_keys supports all documented environment variable names."""
    k_raw = Fernet.generate_key().decode("ascii")

    # 1. TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{k_raw}")
    monkeypatch.delenv("TELEPHONY_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TELEPHONY_ENCRYPTION_KEY", raising=False)
    keys, primary = _load_keys()
    assert len(keys) == 1
    assert primary == "k1"

    # 2. TELEPHONY_ENCRYPTION_KEYS alias
    monkeypatch.delenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("TELEPHONY_ENCRYPTION_KEYS", f"k2:{k_raw}")
    keys, primary = _load_keys()
    assert len(keys) == 1
    assert primary == "k2"

    # 3. Single-key TELEPHONY_CREDENTIALS_ENCRYPTION_KEY
    monkeypatch.delenv("TELEPHONY_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEY", k_raw)
    keys, primary = _load_keys()
    assert len(keys) == 1
    assert primary == "k1"

    # 4. Single-key TELEPHONY_ENCRYPTION_KEY
    monkeypatch.delenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("TELEPHONY_ENCRYPTION_KEY", k_raw)
    keys, primary = _load_keys()
    assert len(keys) == 1
    assert primary == "k1"


def test_crypto_env_identical_values_allowed(monkeypatch):
    """Setting both variables to identical keys does not raise a conflict."""
    k_raw = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{k_raw}")
    monkeypatch.setenv("TELEPHONY_ENCRYPTION_KEYS", f"k1:{k_raw}")
    keys, primary = _load_keys()
    assert len(keys) == 1
    assert primary == "k1"


def test_crypto_env_conflicting_keys_rejected(monkeypatch):
    """Setting different keys across environment variables fails closed with CryptoConfigurationError."""
    k1 = Fernet.generate_key().decode("ascii")
    k2 = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{k1}")
    monkeypatch.setenv("TELEPHONY_ENCRYPTION_KEYS", f"k2:{k2}")

    with pytest.raises(CryptoConfigurationError, match="Conflicting encryption key configurations detected"):
        _load_keys()


# ==============================================================================
# 2. VOBIZ CREDENTIAL WRITE & READ INTEGRATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_vobiz_upsert_writes_encrypted_credentials():
    """_upsert_vobiz_setup_config encrypts Vobiz credentials before writing to DB."""
    from api.db import db_client

    org_id = 42
    request = VobizLiveKitSetupRequest(
        config_name="Production Vobiz",
        vobiz_auth_id="MA_TEST_AUTH_ID_123",
        vobiz_auth_token="TEST_SECRET_TOKEN_XYZ",
        vobiz_application_id="app_12345",
        set_default_outbound=True,
    )

    created_capture = {}

    async def mock_create_tc(**kwargs):
        created_capture.update(kwargs)
        mock_row = MagicMock()
        mock_row.id = 99
        mock_row.name = kwargs.get("name")
        mock_row.provider = kwargs.get("provider")
        mock_row.credentials = kwargs.get("credentials")
        return mock_row

    with patch.object(db_client, "list_telephony_configurations_by_provider", return_value=[]), \
         patch.object(db_client, "create_telephony_configuration", side_effect=mock_create_tc):

        row, created = await _upsert_vobiz_setup_config(organization_id=org_id, request=request)
        assert created is True
        assert created_capture["provider"] == "vobiz"
        creds = created_capture["credentials"]

        # Sensitive credentials MUST be encrypted at rest in DB
        assert is_encrypted(creds["auth_token"]) is True
        assert is_encrypted(creds["auth_id"]) is True
        assert creds["auth_token"] != "TEST_SECRET_TOKEN_XYZ"
        assert is_all_sensitive_encrypted("vobiz", creds) is True

        # In-memory decryption restores original values
        decrypted = decrypt_provider_credentials("vobiz", creds)
        assert decrypted["auth_id"] == "MA_TEST_AUTH_ID_123"
        assert decrypted["auth_token"] == "TEST_SECRET_TOKEN_XYZ"


@pytest.mark.asyncio
async def test_vobiz_candidate_matching_decrypts_stored_auth_id():
    """_upsert_vobiz_setup_config matches existing configs even when stored auth_id is encrypted."""
    from api.db import db_client

    org_id = 42
    stored_auth_id = "MA_MATCH_AUTH_ID"
    stored_encrypted_creds = encrypt_provider_credentials(
        "vobiz",
        {"auth_id": stored_auth_id, "auth_token": "secret_token_1"}
    )

    existing_row = MagicMock()
    existing_row.id = 101
    existing_row.provider = "vobiz"
    existing_row.name = "Existing Vobiz"
    existing_row.credentials = stored_encrypted_creds

    request = VobizLiveKitSetupRequest(
        config_name="Updated Vobiz",
        vobiz_auth_id=stored_auth_id,
        vobiz_auth_token="new_secret_token_2",
    )

    updated_capture = {}

    async def mock_update_tc(**kwargs):
        updated_capture.update(kwargs)
        existing_row.credentials = kwargs.get("credentials")
        return existing_row

    with patch.object(db_client, "list_telephony_configurations_by_provider", return_value=[existing_row]), \
         patch.object(db_client, "update_telephony_configuration", side_effect=mock_update_tc):

        row, created = await _upsert_vobiz_setup_config(organization_id=org_id, request=request)
        assert row.id == 101
        assert "credentials" in updated_capture
        # Newly updated credentials are encrypted
        assert is_encrypted(updated_capture["credentials"]["auth_token"]) is True


@pytest.mark.asyncio
async def test_vobiz_read_helpers_decrypt_in_memory():
    """Vobiz provider read paths decrypt credentials in memory without modifying DB."""
    encrypted_creds = encrypt_provider_credentials(
        "vobiz",
        {"auth_id": "MA_READ_AUTH", "auth_token": "READ_TOKEN"}
    )

    # list_vobiz_account_phone_numbers
    with patch("api.services.livekit.vobiz._vobiz_request_json", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"items": ["+12025550199"]}
        numbers = await list_vobiz_account_phone_numbers(encrypted_creds)
        assert numbers == ["+12025550199"]
        # Verify decrypted auth was sent in headers
        headers_sent = mock_req.call_args[1]["headers"]
        assert headers_sent["X-Auth-ID"] == "MA_READ_AUTH"
        assert headers_sent["X-Auth-Token"] == "READ_TOKEN"

    # verify_vobiz_credentials
    enc_id = encrypt_string("MA_VERIFY_AUTH")
    enc_tok = encrypt_string("VERIFY_TOKEN")
    with patch("api.services.livekit.vobiz._vobiz_request_json", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"items": []}
        result = await verify_vobiz_credentials(enc_id, enc_tok)
        assert result["ok"] is True
        headers_sent = mock_req.call_args[1]["headers"]
        assert headers_sent["X-Auth-ID"] == "MA_VERIFY_AUTH"
        assert headers_sent["X-Auth-Token"] == "VERIFY_TOKEN"


def test_vobiz_detail_response_masks_credentials():
    """API detail response masks credentials, never exposing ciphertexts or raw secrets."""
    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.name = "Prod Vobiz"
    mock_row.provider = "vobiz"
    mock_row.is_default_outbound = True
    mock_row.credentials = encrypt_provider_credentials(
        "vobiz",
        {"auth_id": "MA_ACCOUNT_SECRET", "auth_token": "SUPER_SECRET_TOKEN", "vobiz_sip_password": "sip_pass"}
    )
    mock_row.created_at = datetime.now(timezone.utc)
    mock_row.updated_at = datetime.now(timezone.utc)

    detail = _detail_response(mock_row)
    assert detail.provider == "vobiz"
    creds = detail.credentials
    # Secrets should be masked (e.g. asterisks)
    assert "SUPER_SECRET_TOKEN" not in str(creds)
    assert "enc:v1:" not in str(creds)
    assert "******" in creds.get("auth_token", "") or "*" in creds.get("auth_token", "")


# ==============================================================================
# 3. WORKFLOW RECORDING LOOKUP & FASTAPI PLAYBACK (200 & 206) TESTS
# ==============================================================================

def _create_test_app():
    app = FastAPI()
    app.include_router(recording_router, prefix="/api/v1")
    app.include_router(s3_router, prefix="/api/v1")
    return app


def test_playback_uses_get_recording_by_recording_id_and_streams_200_and_206(tmp_path):
    """FastAPI playback route uses get_recording_by_recording_id and handles HTTP 200 & HTTP 206 Range requests."""
    from api.db import db_client
    from api.services.storage import storage_fs

    app = _create_test_app()
    client = TestClient(app)

    org_id = 10
    recording_id = "rec_test_206"
    test_audio = bytes(range(200))  # 200 bytes of audio data

    # Create temporary file to simulate storage download
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(test_audio)

    mock_rec = MagicMock()
    mock_rec.recording_id = recording_id
    mock_rec.organization_id = org_id
    mock_rec.storage_key = f"recordings/{org_id}/{recording_id}/greeting.wav"
    mock_rec.is_active = True

    async def mock_download(key, target_path):
        with open(target_path, "wb") as f:
            f.write(test_audio)
        return True

    token = create_playback_token(recording_id, org_id, expires_in=300)

    with patch.object(db_client, "get_recording_by_recording_id", new_callable=AsyncMock) as mock_get_rec, \
         patch.object(storage_fs, "adownload_file", side_effect=mock_download):

        mock_get_rec.return_value = mock_rec

        # 1. Full playback GET (HTTP 200)
        resp = client.get(f"/api/v1/workflow-recordings/{recording_id}/playback?token={token}")
        assert resp.status_code == 200
        assert resp.headers["content-length"] == "200"
        assert resp.headers["accept-ranges"] == "bytes"
        assert resp.content == test_audio
        mock_get_rec.assert_called_with(recording_id, organization_id=org_id)

        # 2. HTTP 206 Partial Content: bytes=0-49 (first 50 bytes)
        resp_range1 = client.get(
            f"/api/v1/workflow-recordings/{recording_id}/playback?token={token}",
            headers={"Range": "bytes=0-49"}
        )
        assert resp_range1.status_code == 206
        assert resp_range1.headers["content-range"] == "bytes 0-49/200"
        assert resp_range1.headers["content-length"] == "50"
        assert resp_range1.content == test_audio[0:50]

        # 3. HTTP 206 Partial Content: bytes=50-99 (middle slice)
        resp_range2 = client.get(
            f"/api/v1/workflow-recordings/{recording_id}/playback?token={token}",
            headers={"Range": "bytes=50-99"}
        )
        assert resp_range2.status_code == 206
        assert resp_range2.headers["content-range"] == "bytes 50-99/200"
        assert resp_range2.headers["content-length"] == "50"
        assert resp_range2.content == test_audio[50:100]

        # 4. HTTP 206 Partial Content: bytes=100- (offset to end)
        resp_range3 = client.get(
            f"/api/v1/workflow-recordings/{recording_id}/playback?token={token}",
            headers={"Range": "bytes=100-"}
        )
        assert resp_range3.status_code == 206
        assert resp_range3.headers["content-range"] == "bytes 100-199/200"
        assert resp_range3.headers["content-length"] == "100"
        assert resp_range3.content == test_audio[100:200]

        # 5. HTTP 206 Partial Content: bytes=-40 (suffix: last 40 bytes)
        resp_range4 = client.get(
            f"/api/v1/workflow-recordings/{recording_id}/playback?token={token}",
            headers={"Range": "bytes=-40"}
        )
        assert resp_range4.status_code == 206
        assert resp_range4.headers["content-range"] == "bytes 160-199/200"
        assert resp_range4.headers["content-length"] == "40"
        assert resp_range4.content == test_audio[160:200]


def test_playback_tenant_denial_and_security_checks():
    """Playback enforces strict tenant isolation, token integrity, and authentication."""
    from api.db import db_client

    app = _create_test_app()
    client = TestClient(app)

    recording_id = "rec_isolated_1"
    org_1 = 10
    org_2 = 20

    # 1. Missing authentication returns 401
    resp_unauth = client.get(f"/api/v1/workflow-recordings/{recording_id}/playback")
    assert resp_unauth.status_code == 401

    # 2. Expired token returns 403
    expired_token = create_playback_token(recording_id, org_1, expires_in=-10)
    resp_expired = client.get(f"/api/v1/workflow-recordings/{recording_id}/playback?token={expired_token}")
    assert resp_expired.status_code == 403

    # 3. Tampered token returns 403
    valid_token = create_playback_token(recording_id, org_1, expires_in=300)
    tampered_token = valid_token[:-4] + "dead"
    resp_tampered = client.get(f"/api/v1/workflow-recordings/{recording_id}/playback?token={tampered_token}")
    assert resp_tampered.status_code == 403

    # 4. Token for org_2 attempting to query recording belonging to org_1 returns 404 (DB isolation)
    token_org2 = create_playback_token(recording_id, org_2, expires_in=300)
    with patch.object(db_client, "get_recording_by_recording_id", new_callable=AsyncMock) as mock_get_rec:
        # DB returns None when queried with org_2
        mock_get_rec.return_value = None

        resp_cross_tenant = client.get(f"/api/v1/workflow-recordings/{recording_id}/playback?token={token_org2}")
        assert resp_cross_tenant.status_code == 404
        mock_get_rec.assert_called_with(recording_id, organization_id=org_2)


# ==============================================================================
# 4. BROWSER PLAYBACK URL INTEGRATION TEST (DOCKER-INTERNAL AVOIDANCE)
# ==============================================================================

def test_signed_url_avoids_docker_internal_endpoint():
    """Signed URL generation never exposes http://api:8000 to browser clients."""
    app = _create_test_app()

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.is_superuser = False
    mock_user.selected_organization_id = 10
    app.dependency_overrides[get_user] = lambda: mock_user

    client = TestClient(app)
    key = "recordings/10/rec_docker_test/greeting.wav"

    # Case A: When backend endpoint is Docker-internal 'http://api:8000'
    with patch("api.utils.common.get_backend_endpoints", new_callable=AsyncMock) as mock_be, \
         patch("api.routes.s3_signed_url.resolve_public_url", return_value=None):
        mock_be.return_value = ("http://api:8000", "ws://api:8000")

        resp = client.get(f"/api/v1/s3/signed-url?key={key}")
        assert resp.status_code == 200
        url = resp.json()["url"]
        # Must NOT start with http://api:8000
        assert not url.startswith("http://api:8000")
        assert not url.startswith("http://api:")
        # Relative path fallback is valid for browsers
        assert url.startswith("/api/v1/workflow-recordings/rec_docker_test/playback?token=")

    # Case B: When external endpoint is 'http://localhost:8000'
    with patch("api.utils.common.get_backend_endpoints", new_callable=AsyncMock) as mock_be:
        mock_be.return_value = ("http://localhost:8000", "ws://localhost:8000")

        resp = client.get(f"/api/v1/s3/signed-url?key={key}")
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert url.startswith("http://localhost:8000/api/v1/workflow-recordings/rec_docker_test/playback?token=")

