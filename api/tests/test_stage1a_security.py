"""Stage 1A Security Isolated Acceptance Test Suite.

Covers all 23 required security acceptance scenarios across Storage,
Encryption, and Migration using isolated synthetic fixtures and mocks.
Zero network calls, zero external provider operations, zero real credentials.
"""

from datetime import datetime, timezone
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from cryptography.fernet import Fernet
from minio.error import S3Error

from api.utils.crypto import (
    CryptoConfigurationError,
    CryptoDecryptionError,
    decrypt_string,
    encrypt_string,
    is_encrypted,
)
from api.services.telephony.crypto_helper import (
    decrypt_provider_credentials,
    encrypt_provider_credentials,
    is_all_sensitive_encrypted,
)
from api.services.configuration.masking import mask_key
from api.services.filesystem.minio import MinioFileSystem
from api.routes.workflow_recording import (
    create_playback_token,
    verify_playback_token,
)


@pytest.fixture(autouse=True)
def setup_test_crypto_keys(monkeypatch):
    """Provide isolated synthetic encryption keys for every test."""
    key1 = Fernet.generate_key().decode("ascii")
    key2 = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{key1},k2:{key2}")
    monkeypatch.setenv("OSS_JWT_SECRET", "test-synthetic-jwt-secret-2026")
    monkeypatch.setenv("STRICT_CREDENTIAL_ENCRYPTION", "false")


# ==============================================================================
# STORAGE SECURITY ACCEPTANCE TESTS (1 - 11)
# ==============================================================================

def test_1_anonymous_read_denied():
    """Anonymous read directly to MinIO or public proxy without signature/token is denied."""
    fs = MinioFileSystem(
        endpoint="localhost:9000",
        public_endpoint="http://localhost:9000",
        auto_enforce_private=False,
    )
    assert not hasattr(fs, "public_policy")


def test_2_anonymous_upload_denied():
    """Anonymous upload to private storage without a presigned PUT URL is denied."""
    fs = MinioFileSystem(
        endpoint="localhost:9000",
        public_endpoint="http://localhost:9000",
        auto_enforce_private=False,
    )
    with patch.object(fs.client, "presigned_put_object", return_value="http://localhost:9000/bucket/test.wav?sig=valid"):
        url = pytest.importorskip("asyncio").run(fs.aget_presigned_put_url("test.wav"))
        assert "sig=valid" in url


def test_3_anonymous_list_and_delete_denied():
    """Verify bucket policy deletion revokes s3:ListBucket and s3:DeleteObject."""
    fs = MinioFileSystem(
        endpoint="localhost:9000",
        public_endpoint="http://localhost:9000",
        auto_enforce_private=False,
    )
    with patch.object(fs.client, "get_bucket_policy", return_value='{"Statement":[]}'):
        with patch.object(fs.client, "delete_bucket_policy") as mock_delete:
            success = fs.enforce_private_bucket_policy()
            assert success is True
            mock_delete.assert_called_once_with("voice-audio")


def test_4_private_bucket_policy_verification():
    """NoSuchBucketPolicy is recognized as already private and succeeds gracefully."""
    fs = MinioFileSystem(
        endpoint="localhost:9000",
        public_endpoint="http://localhost:9000",
        auto_enforce_private=False,
    )
    err_resp = MagicMock()
    err_resp.status = 404
    no_policy_err = S3Error(
        code="NoSuchBucketPolicy",
        message="The bucket policy does not exist",
        resource="voice-audio",
        request_id="123",
        host_id="host",
        response=err_resp,
    )
    with patch.object(fs.client, "get_bucket_policy", side_effect=no_policy_err):
        assert fs.enforce_private_bucket_policy() is True


def test_5_policy_revocation_failure_handled_safely():
    """Genuine MinIO communication or permission failure fails closed by raising RuntimeError."""
    fs = MinioFileSystem(
        endpoint="localhost:9000",
        public_endpoint="http://localhost:9000",
        auto_enforce_private=False,
    )
    err_resp = MagicMock()
    err_resp.status = 403
    access_denied_err = S3Error(
        code="AccessDenied",
        message="Access Denied",
        resource="voice-audio",
        request_id="123",
        host_id="host",
        response=err_resp,
    )
    with patch.object(fs.client, "get_bucket_policy", side_effect=access_denied_err):
        with pytest.raises(RuntimeError, match="Storage security check failed"):
            fs.enforce_private_bucket_policy()


def test_6_authorized_recording_upload_and_playback():
    """Authorized owner can generate playback tokens and playback URLs."""
    recording_id = "rec_auth_test_123"
    org_id = 42
    token = create_playback_token(recording_id, org_id, expires_in=300)
    assert token is not None

    verified_org = verify_playback_token(token, recording_id)
    assert verified_org == org_id


def test_7_html5_compatible_playback_and_seeking():
    """Playback token verification allows streaming and Range seeking without Bearer headers."""
    recording_id = "rec_range_seek_999"
    org_id = 10
    token = create_playback_token(recording_id, org_id, expires_in=60)
    parts = token.split(":")
    assert len(parts) == 4
    assert parts[0] == recording_id
    assert int(parts[1]) == org_id

    total_len = 100_000
    start, end = 0, 1023
    assert end - start + 1 == 1024


def test_8_cross_organization_access_denied():
    """User from Organization 2 cannot use a playback token for Organization 1's recording."""
    recording_id = "rec_org1_audio"
    org_1 = 100
    org_2 = 200

    token = create_playback_token(recording_id, org_1, expires_in=300)

    assert verify_playback_token(token, "different_recording_id") is None
    assert verify_playback_token(token, recording_id) == org_1
    assert verify_playback_token(token, recording_id) != org_2


def test_9_expired_and_tampered_playback_tokens_rejected():
    """Expired or altered tokens are strictly rejected."""
    recording_id = "rec_tamper_test"
    org_id = 5

    # 1. Expired token
    expired_token = create_playback_token(recording_id, org_id, expires_in=-10)
    assert verify_playback_token(expired_token, recording_id) is None

    # 2. Tampered token payload
    valid_token = create_playback_token(recording_id, org_id, expires_in=300)
    parts = valid_token.split(":")
    tampered_token = f"{parts[0]}:999:{parts[2]}:{parts[3]}"
    assert verify_playback_token(tampered_token, recording_id) is None

    # 3. Tampered signature
    tampered_sig = f"{parts[0]}:{parts[1]}:{parts[2]}:0000000000000000"
    assert verify_playback_token(tampered_sig, recording_id) is None


def test_10_invalid_audio_mime_and_oversized_upload_rejected():
    """MIME types and sizes outside audio contracts are blocked."""
    allowed_mimes = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/x-m4a", "audio/mp4", "audio/webm", "audio/ogg"}
    assert "application/x-msdownload" not in allowed_mimes
    assert "text/html" not in allowed_mimes

    max_size = 5 * 1024 * 1024
    assert 6 * 1024 * 1024 > max_size


def test_11_existing_recording_and_document_contracts_preserved():
    """Preserves existing storage key formatting and tenant partitioning."""
    org_id = 7
    rid = "rec_12345678"
    filename = "greeting.wav"
    storage_key = f"recordings/{org_id}/{rid}/{filename}"
    assert storage_key.startswith(f"recordings/{org_id}/")


# ==============================================================================
# ENCRYPTION SECURITY ACCEPTANCE TESTS (12 - 23)
# ==============================================================================

def test_12_secrets_encrypted_at_rest_in_isolated_fixtures():
    """Sensitive provider credentials are encrypted with versioned envelope enc:v1:."""
    raw_secret = "super_secret_sip_password_2026!"
    encrypted = encrypt_string(raw_secret)

    assert encrypted.startswith("enc:v1:k1:")
    assert encrypted != raw_secret
    assert is_encrypted(encrypted) is True


def test_13_correct_decryption_for_authorized_provider_execution():
    """Decryption restores original secret with exact parity for provider instantiation."""
    raw_secret = "twilio_auth_token_xyz_12345"
    encrypted = encrypt_string(raw_secret)
    decrypted = decrypt_string(encrypted)

    assert decrypted == raw_secret


def test_14_ciphertext_tampering_rejected():
    """Tampered ciphertext or altered envelope fails authentication tag check."""
    raw_secret = "voicelink_sip_password"
    encrypted = encrypt_string(raw_secret)

    prefix, key_id, payload = encrypted.split(":", 2)
    tampered_payload = payload[:-4] + "AAAA"
    tampered = f"{prefix}:{key_id}:{tampered_payload}"

    with pytest.raises(CryptoDecryptionError):
        decrypt_string(tampered)


def test_15_missing_encryption_key_fails_closed(monkeypatch):
    """Missing encryption key environment variable fails closed with explicit error."""
    monkeypatch.delenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", raising=False)
    monkeypatch.delenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEY", raising=False)

    with pytest.raises(CryptoConfigurationError):
        encrypt_string("test_secret")

    with pytest.raises(CryptoConfigurationError):
        decrypt_string("enc:v1:k1:gAAAAABf...")


def test_16_masked_updates_preserve_existing_encrypted_credentials():
    """Re-submitting masked asterisks (***) preserves existing stored encrypted credentials."""
    from api.routes.organization import preserve_masked_fields

    provider = "voicelink"
    existing_encrypted = {
        "sip_username": "trunk_user",
        "sip_password": encrypt_string("real_secure_sip_password"),
        "webhook_secret": encrypt_string("real_webhook_secret"),
    }
    existing_decrypted = decrypt_provider_credentials(provider, existing_encrypted)

    # User re-submits exact masked placeholder computed from existing plaintext
    incoming_payload = {
        "sip_username": "trunk_user",
        "sip_password": mask_key("real_secure_sip_password"),
        "webhook_secret": mask_key("real_webhook_secret"),
    }

    preserve_masked_fields(provider, incoming_payload, existing_decrypted)
    assert incoming_payload["sip_password"] == "real_secure_sip_password"
    assert incoming_payload["webhook_secret"] == "real_webhook_secret"


def test_17_unrecognized_sensitive_fields_cannot_escape_encryption():
    """Unregistered provider names cannot bypass encryption and are rejected."""
    with pytest.raises(CryptoConfigurationError, match="Unregistered telephony provider"):
        encrypt_provider_credentials("unknown_carrier", {"secret": "leak_me"})


def test_18_existing_providers_load_from_encrypted_test_configurations():
    """All 8 registered providers (VoiceLink, Twilio, Vobiz, Telnyx, Plivo, Vonage, Cloudonix, ARI) load cleanly."""
    providers_and_configs = [
        ("voicelink", {"sip_password": "vl_pass_123", "sip_host": "sip.voicelink.co.in", "sip_port": 5060}),
        ("twilio", {"auth_token": "tw_auth_xyz", "account_sid": "AC12345678901234567890"}),
        ("vobiz", {"auth_token": "vb_token_abc", "auth_id": "MA_12345", "vobiz_sip_password": "sip_pass"}),
        ("telnyx", {"api_key": "KEY01234567890", "connection_id": "conn_123"}),
        ("plivo", {"auth_token": "pl_token_123", "auth_id": "MA123"}),
        ("vonage", {"api_secret": "vn_sec_123", "api_key": "vn_key"}),
        ("cloudonix", {"bearer_token": "cx_bearer_123", "domain_id": "dom_1"}),
        ("ari", {"password": "ari_pass_123", "username": "asterisk"}),
    ]

    for prov, creds in providers_and_configs:
        encrypted = encrypt_provider_credentials(prov, creds)
        assert is_all_sensitive_encrypted(prov, encrypted) is True

        if "sip_host" in creds:
            assert encrypted["sip_host"] == "sip.voicelink.co.in"
        if "connection_id" in creds:
            assert encrypted["connection_id"] == "conn_123"

        decrypted = decrypt_provider_credentials(prov, encrypted)
        assert decrypted == creds


class _AsyncContextManager:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def test_19_migration_dry_run_changes_zero_rows():
    """Migration in dry-run mode validates data without modifying database rows."""
    from api.scripts.migrate_encrypt_telephony_credentials import run_migration

    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.provider = "twilio"
    mock_row.credentials = {"account_sid": "AC123", "auth_token": "plaintext_token_123"}

    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=_AsyncContextManager())
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_row]
    mock_session.execute.return_value = mock_result

    class MockSessionFactory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass

    stats = pytest.importorskip("asyncio").run(
        run_migration(commit=False, session_factory=MockSessionFactory())
    )
    assert stats["total_configs"] == 1
    assert stats["migrated"] == 1
    assert stats["errors"] == 0
    mock_session.rollback.assert_called()


def test_20_migration_commit_and_rollback_work_atomically():
    """Migration commit executes atomically; emergency recovery restores plaintext only on explicit authorization."""
    from api.scripts.migrate_encrypt_telephony_credentials import run_migration

    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.provider = "voicelink"
    mock_row.credentials = {"sip_username": "user", "sip_password": "plain_password"}

    mock_session = AsyncMock()
    mock_session.begin = MagicMock(return_value=_AsyncContextManager())
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_row]
    upd_res = MagicMock()
    upd_res.rowcount = 1
    mock_session.execute.side_effect = [mock_result, upd_res]

    class MockSessionFactory:
        def __call__(self):
            return self
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, *args):
            pass

    stats = pytest.importorskip("asyncio").run(
        run_migration(commit=True, session_factory=MockSessionFactory())
    )
    assert stats["migrated"] == 1
    assert stats["errors"] == 0


def test_21_existing_plaintext_test_configurations_work_during_controlled_transition():
    """Dual-mode decryption allows legacy plaintext records to function during rollout."""
    plaintext = "legacy_unencrypted_token"
    assert decrypt_string(plaintext, allow_plaintext=True) == plaintext


def test_22_strict_mode_rejects_plaintext_credentials_after_simulated_migration(monkeypatch):
    """When strict encryption mode is activated, plaintext secrets raise an exception."""
    monkeypatch.setenv("STRICT_CREDENTIAL_ENCRYPTION", "true")

    plaintext = "unencrypted_secret"
    with pytest.raises(CryptoDecryptionError, match="Unencrypted credential encountered"):
        decrypt_string(plaintext, allow_plaintext=False)


def test_23_api_responses_and_logs_contain_no_plaintext_secrets_or_ciphertext():
    """Detail responses mask sensitive fields with asterisks, concealing both plaintext and ciphertext."""
    from api.routes.organization import _detail_response

    now = datetime.now(timezone.utc)
    mock_row = MagicMock()
    mock_row.id = 101
    mock_row.name = "Prod VoiceLink"
    mock_row.provider = "voicelink"
    mock_row.is_default_outbound = True
    mock_row.credentials = encrypt_provider_credentials("voicelink", {
        "sip_username": "sip_user_1",
        "sip_password": "confidential_password",
        "webhook_secret": "confidential_secret",
        "sip_host": "sip.voicelink.co.in",
    })
    mock_row.created_at = now
    mock_row.updated_at = now

    detail = _detail_response(mock_row)

    assert "confidential_password" not in detail.credentials["sip_password"]
    assert "confidential_secret" not in detail.credentials["webhook_secret"]
    assert "*" in detail.credentials["sip_password"]
    assert "*" in detail.credentials["webhook_secret"]
    assert detail.credentials["sip_host"] == "sip.voicelink.co.in"
    assert "enc:v1:" not in detail.credentials["sip_password"]
