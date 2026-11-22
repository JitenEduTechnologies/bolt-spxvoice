"""Dashboard-based Gemini key readiness: no provider calls, synthetic keys only.

Exercises the real UserClient persistence code against an in-memory fake session
so the encrypted-at-rest behaviour, masking, merge and worker key resolution are
tested together.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from loguru import logger

from api.db.models import UserConfigurationModel
from api.db.user_client import UserClient
from api.schemas.user_configuration import UserConfiguration
from api.services.auth.depends import get_user
from api.services.configuration import provider_readiness as pr
from api.services.configuration.at_rest import (
    ConfigEncryptionRequiredError,
    decrypt_configuration_secrets,
    encrypt_configuration_secrets,
)
from api.services.configuration.defaults import build_env_default_user_configuration
from api.services.configuration.masking import mask_key
from api.services.configuration.resolve import resolve_effective_config

SYNTHETIC_KEY = "SYNTHETIC_GEMINI_KEY_0123456789abcdef"
GEMINI_ENV_NAMES = (
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
    "GOOGLE_AI_API_KEY",
)
ENC_ENV_NAMES = (
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS",
    "TELEPHONY_ENCRYPTION_KEYS",
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEY",
    "TELEPHONY_ENCRYPTION_KEY",
)

REALTIME_BODY = {
    "is_realtime": True,
    "realtime": {
        "provider": "google_realtime",
        "api_key": SYNTHETIC_KEY,
        "model": "gemini-3.8-live",
        "voice": "Kore",
        "language": "en",
    },
}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(first=lambda: self._rows[0] if self._rows else None)

    def all(self):
        return [(r.configuration,) for r in self._rows]


class _Session:
    def __init__(self, store):
        self._store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _query):
        return _Result(self._store)

    def add(self, obj):
        self._store.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, _obj):
        pass


def _make_client():
    store: list = []
    client = UserClient.__new__(UserClient)
    client.async_session = lambda: _Session(store)
    return client, store


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in GEMINI_ENV_NAMES + ("OPENAI_API_KEY",) + ENC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("REQUIRE_CONFIG_ENCRYPTION", raising=False)
    pr.reset_cache()
    yield
    pr.reset_cache()


@pytest.fixture
def encryption(monkeypatch):
    monkeypatch.setenv(
        "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", Fernet.generate_key().decode()
    )
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")


@pytest.fixture
def dashboard(encryption):
    """Real UserClient on a fake session, mounted behind the real /user routes."""
    from api.routes.user import router

    client, store = _make_client()
    app = FastAPI()
    app.include_router(router)
    user = MagicMock()
    user.id = 1
    user.is_superuser = False
    user.selected_organization_id = None
    user.provider_id = "test"
    app.dependency_overrides[get_user] = lambda: user
    with (
        patch("api.routes.user.db_client", client),
        patch("api.routes.user.UserConfigurationValidator") as validator,
        patch("api.routes.user.is_livekit_runtime", return_value=True),
    ):
        validator.return_value.validate = AsyncMock()
        yield SimpleNamespace(http=TestClient(app), client=client, store=store)


def _save(dash, body=REALTIME_BODY):
    return dash.http.put("/user/configurations/user", json=body)


# --- 1. startup without a key ------------------------------------------------


def test_no_env_key_means_no_seeded_config():
    assert build_env_default_user_configuration() is None


def test_import_and_unconfigured_state_without_any_key():
    import api.routes.main  # noqa: F401  (must import with no provider key)

    result = pr.evaluate_user_config(UserConfiguration(is_realtime=True))
    assert result.status == pr.PROVIDER_UNCONFIGURED
    assert result.missing == ["realtime"]
    assert "dashboard" in result.message
    assert "Test Audio" in result.message


def test_health_reports_provider_unconfigured_not_failure():
    from api.routes import health

    client, _ = _make_client()
    with patch.object(health, "db_client", client):
        response = asyncio.run(health.legacy_health())
    assert response.status == "ok"
    assert response.provider_status == pr.PROVIDER_UNCONFIGURED


def test_health_provider_status_unknown_when_db_unreachable():
    broken = SimpleNamespace(
        any_realtime_provider_configured=AsyncMock(side_effect=RuntimeError("down"))
    )
    assert asyncio.run(pr.deployment_provider_status(broken)) == pr.PROVIDER_UNKNOWN


# --- 2. dashboard save / reload, masked responses ---------------------------


def test_dashboard_save_reload_masked_and_encrypted(dashboard):
    saved = _save(dashboard)
    assert saved.status_code == 200
    masked = mask_key(SYNTHETIC_KEY)
    assert saved.json()["realtime"]["api_key"] == masked
    assert SYNTHETIC_KEY not in saved.text

    reloaded = dashboard.http.get("/user/configurations/user")
    assert reloaded.status_code == 200
    assert reloaded.json()["realtime"]["api_key"] == masked
    assert SYNTHETIC_KEY not in reloaded.text

    # Persisted form is an encrypted envelope, not plaintext.
    stored = dashboard.store[0].configuration
    assert str(stored["realtime"]["api_key"]).startswith("enc:v1:")
    assert SYNTHETIC_KEY not in json.dumps(stored)

    # Masking is not encryption: only the stored envelope protects the value.
    assert masked != stored["realtime"]["api_key"]

    # The worker-side read path returns the real key.
    config = asyncio.run(dashboard.client.get_user_configurations(1))
    assert config.realtime.api_key == SYNTHETIC_KEY


def test_masked_placeholder_merge_keeps_real_key(dashboard):
    _save(dashboard)
    masked = mask_key(SYNTHETIC_KEY)
    body = {
        "is_realtime": True,
        "realtime": {**REALTIME_BODY["realtime"], "api_key": masked, "voice": "Puck"},
    }
    again = _save(dashboard, body)
    assert again.status_code == 200
    config = asyncio.run(dashboard.client.get_user_configurations(1))
    assert config.realtime.api_key == SYNTHETIC_KEY
    assert config.realtime.voice == "Puck"
    assert str(dashboard.store[0].configuration["realtime"]["api_key"]).startswith(
        "enc:v1:"
    )


def test_masked_placeholder_rejected_when_provider_changes(dashboard):
    _save(dashboard)
    body = {
        "is_realtime": True,
        "realtime": {
            "provider": "openai_realtime",
            "api_key": mask_key(SYNTHETIC_KEY),
            "model": "gpt-4o-realtime-preview",
            "voice": "alloy",
        },
    }
    response = _save(dashboard, body)
    assert response.status_code in (400, 422)
    assert SYNTHETIC_KEY not in response.text


# --- 3. encryption contract --------------------------------------------------


def test_encryption_required_fails_closed_without_key(monkeypatch):
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")
    client, store = _make_client()
    config = UserConfiguration.model_validate(
        {"is_realtime": True, "realtime": REALTIME_BODY["realtime"]}
    )
    with pytest.raises(ConfigEncryptionRequiredError) as exc:
        asyncio.run(client.update_user_configuration(1, config))
    assert SYNTHETIC_KEY not in str(exc.value)
    assert store == []


def test_legacy_plaintext_row_still_loads_and_reencrypts(encryption):
    client, store = _make_client()
    store.append(
        UserConfigurationModel(
            user_id=1,
            configuration={"is_realtime": True, "realtime": REALTIME_BODY["realtime"]},
        )
    )
    config = asyncio.run(client.get_user_configurations(1))
    assert config.realtime.api_key == SYNTHETIC_KEY
    asyncio.run(client.update_user_configuration(1, config))
    assert str(store[0].configuration["realtime"]["api_key"]).startswith("enc:v1:")


def test_encrypt_decrypt_roundtrip_covers_secret_fields_and_lists(encryption):
    original = {
        "realtime": {"provider": "google_realtime", "api_key": [SYNTHETIC_KEY, "k2-x"]},
        "llm": {"provider": "openai", "api_key": SYNTHETIC_KEY, "model": "m"},
    }
    sealed = encrypt_configuration_secrets(original, context="user:1")
    assert SYNTHETIC_KEY not in json.dumps(sealed)
    assert decrypt_configuration_secrets(sealed, context="user:1") == original


# --- 4. no key in logs / workflow JSON --------------------------------------


def test_key_never_in_logs_or_workflow_json(dashboard):
    from api.services.workflow.defaults import build_default_workflow_definition

    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(str(message)), level="TRACE")
    try:
        _save(dashboard)
        dashboard.http.get("/user/configurations/user")
        asyncio.run(dashboard.client.get_user_configurations(1))
        asyncio.run(pr.deployment_provider_status(dashboard.client))
    finally:
        logger.remove(sink_id)

    assert SYNTHETIC_KEY not in "\n".join(captured)
    assert SYNTHETIC_KEY not in json.dumps(build_default_workflow_definition())


# --- 5. worker key resolution -----------------------------------------------


def test_worker_session_uses_dashboard_saved_key(dashboard, monkeypatch):
    from api.services.livekit import worker

    _save(dashboard)
    user_config = asyncio.run(dashboard.client.get_user_configurations(1))
    effective = resolve_effective_config(user_config, None)

    captured: dict = {}

    def fake_realtime_model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(worker.google.beta.realtime, "RealtimeModel", fake_realtime_model)
    monkeypatch.setattr(worker, "AgentSession", lambda **kw: SimpleNamespace(**kw))

    worker._create_session(effective, vad=object())
    assert captured["api_key"] == SYNTHETIC_KEY
    assert captured["model"] == "gemini-3.8-live"


# --- 6. Test Audio is blocked until a key is saved ---------------------------


def _workflow_row():
    return SimpleNamespace(workflow_configurations={})


def test_test_audio_rejected_without_key():
    from api.routes import workflow as wf

    user = SimpleNamespace(id=1, selected_organization_id=7)
    fake_db = SimpleNamespace(
        get_user_configurations=AsyncMock(
            return_value=UserConfiguration(is_realtime=True)
        ),
        get_workflow=AsyncMock(return_value=_workflow_row()),
        create_workflow_run=AsyncMock(),
    )
    request = wf.CreateWorkflowRunRequest(mode="smallwebrtc", name="WR-test")
    with (
        patch.object(wf, "db_client", fake_db),
        patch.object(wf, "is_livekit_runtime", return_value=True),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wf.create_workflow_run(1, request, user))

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "provider_unconfigured"
    assert "dashboard" in exc.value.detail["message"]
    fake_db.create_workflow_run.assert_not_called()


def test_test_audio_allowed_after_key_saved(dashboard):
    from api.routes import workflow as wf

    _save(dashboard)
    user = SimpleNamespace(id=1, selected_organization_id=7)
    created = SimpleNamespace(
        id=5,
        workflow_id=1,
        name="WR-test",
        mode="livekit",
        created_at=None,
        definition_id=1,
        initial_context=None,
        gathered_context=None,
    )
    fake_db = SimpleNamespace(
        get_user_configurations=dashboard.client.get_user_configurations,
        get_workflow=AsyncMock(return_value=_workflow_row()),
        create_workflow_run=AsyncMock(return_value=created),
    )
    request = wf.CreateWorkflowRunRequest(mode="smallwebrtc", name="WR-test")
    with (
        patch.object(wf, "db_client", fake_db),
        patch.object(wf, "is_livekit_runtime", return_value=True),
    ):
        result = asyncio.run(wf.create_workflow_run(1, request, user))
    assert result["id"] == 5
    fake_db.create_workflow_run.assert_awaited_once()
