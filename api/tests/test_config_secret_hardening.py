"""Security hardening of stored provider secrets. Synthetic keys, no provider calls."""

import asyncio
import importlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from api.db.models import UserConfigurationModel
from api.db.user_client import UserClient
from api.db.workflow_client import WorkflowClient
from api.schemas.user_configuration import UserConfiguration
from api.services.auth.depends import get_user
from api.services.configuration import at_rest
from api.services.configuration.at_rest import (
    ConfigDecryptionError,
    ConfigEncryptionRequiredError,
    decrypt_configuration_secrets,
    encrypt_configuration_secrets,
    reencrypt_configuration_secrets,
)
from api.services.configuration.key_format import check_key_format
from api.services.configuration.masking import (
    merge_workflow_configuration_api_keys,
)
from api.services.configuration.override_secrets import (
    WorkflowSecretError,
    adopt_unchanged_legacy_secrets,
    assert_no_plaintext_override_secrets,
    encrypt_override_secrets,
    find_plaintext_override_secrets,
    reencrypt_override_secrets,
    validate_incoming_override_secrets,
)
from api.services.configuration.resolve import resolve_effective_config

KEY = "SYNTHETIC_GEMINI_KEY_0123456789abcdef"
KEY2 = "SYNTHETIC_OTHER_KEY_ffffeeeeddddcccc"
ENC_NAMES = (
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS",
    "TELEPHONY_ENCRYPTION_KEYS",
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEY",
    "TELEPHONY_ENCRYPTION_KEY",
)
REALTIME = {
    "provider": "google_realtime",
    "api_key": KEY,
    "model": "gemini-3.8-live",
    "voice": "Kore",
    "language": "en",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ENC_NAMES + ("REQUIRE_CONFIG_ENCRYPTION",):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(at_rest, "_warned_plaintext", False)


def _set_keys(monkeypatch, value):
    for n in ENC_NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", value)


@pytest.fixture
def fernet(monkeypatch):
    k = Fernet.generate_key().decode()
    _set_keys(monkeypatch, k)
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")
    return k


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

    async def execute(self, _q):
        await asyncio.sleep(0)  # yield so concurrent saves interleave
        return _Result(self._store)

    def add(self, obj):
        self._store.append(obj)

    async def commit(self):
        await asyncio.sleep(0)

    async def rollback(self):
        pass

    async def refresh(self, _o):
        pass


def _client():
    store: list = []
    c = UserClient.__new__(UserClient)
    c.async_session = lambda: _Session(store)
    return c, store


def _cfg(key=KEY):
    return UserConfiguration.model_validate(
        {"is_realtime": True, "realtime": {**REALTIME, "api_key": key}}
    )


def _tamper(envelope: str) -> str:
    head, payload = envelope.rsplit(":", 1)
    flipped = ("A" if payload[20] != "A" else "B")
    return head + ":" + payload[:20] + flipped + payload[21:]


# --- 1. encryption boundaries, tamper, malformed, missing key ----------------


def test_stored_json_is_ciphertext_only_and_scanned_whole(fernet):
    client, store = _client()
    asyncio.run(client.update_user_configuration(7, _cfg()))
    blob = json.dumps(store[0].configuration)
    assert KEY not in blob
    assert "enc:v1:" in blob
    assert asyncio.run(client.get_user_configurations(7)).realtime.api_key == KEY


def test_tampered_ciphertext_is_rejected_without_leaking(fernet):
    sealed = encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    sealed["realtime"]["api_key"] = _tamper(sealed["realtime"]["api_key"])
    with pytest.raises(ConfigDecryptionError) as exc:
        decrypt_configuration_secrets(sealed, context="user:1")
    assert exc.value.reason == "integrity check failed"
    assert KEY not in str(exc.value)


@pytest.mark.parametrize(
    "bad", ["enc:v1:k1", "enc:v1:", "enc:v1:k1:not-base64!!", "enc:v1:k1:éé"]
)
def test_malformed_envelopes_raise_decryption_error(fernet, bad):
    with pytest.raises(ConfigDecryptionError):
        decrypt_configuration_secrets({"realtime": {"api_key": bad}}, context="user:1")


def test_wrong_key_and_missing_key_fail_closed(monkeypatch, fernet):
    sealed = encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    _set_keys(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(ConfigDecryptionError):
        decrypt_configuration_secrets(sealed, context="user:1")
    for n in ENC_NAMES:
        monkeypatch.delenv(n, raising=False)
    with pytest.raises(ConfigDecryptionError) as exc:
        decrypt_configuration_secrets(sealed, context="user:1")
    assert exc.value.reason == "no encryption key configured"


def test_missing_key_on_save_is_fail_closed_when_required(monkeypatch):
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")
    with pytest.raises(ConfigEncryptionRequiredError):
        encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")


def test_missing_key_without_requirement_warns_once(monkeypatch):
    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
        encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    finally:
        logger.remove(sink)
    assert sum("plaintext" in m for m in seen) == 1
    assert KEY not in "".join(seen)


def test_conflicting_key_variables_do_not_echo_key_material(monkeypatch):
    from api.utils.crypto import CryptoConfigurationError, _load_keys

    a, b = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", a)
    monkeypatch.setenv("TELEPHONY_ENCRYPTION_KEYS", b)
    with pytest.raises(CryptoConfigurationError) as exc:
        _load_keys()
    assert a[:8] not in str(exc.value) and b[:8] not in str(exc.value)


# --- 2. tenant isolation ------------------------------------------------------


def test_ciphertext_cannot_be_replayed_under_another_owner(fernet):
    sealed = encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    with pytest.raises(ConfigDecryptionError) as exc:
        decrypt_configuration_secrets(sealed, context="user:2")
    assert exc.value.reason == "ciphertext belongs to a different owner"


def test_row_swap_between_users_is_detected(fernet):
    client, store = _client()
    asyncio.run(client.update_user_configuration(1, _cfg()))
    store[0].user_id = 2  # attacker/ops error moves the row to another owner
    with pytest.raises(ConfigDecryptionError):
        asyncio.run(client.get_user_configurations(2))


def test_user_supplied_envelope_is_rejected_by_the_route(fernet):
    from api.routes.user import router

    client, _ = _client()
    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    stolen = encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:99")
    with (
        patch("api.routes.user.db_client", client),
        patch("api.routes.user.UserConfigurationValidator") as v,
        patch("api.routes.user.is_livekit_runtime", return_value=True),
    ):
        v.return_value.validate = AsyncMock()
        r = TestClient(app).put(
            "/user/configurations/user",
            json={"is_realtime": True, "realtime": {**REALTIME, "api_key": stolen["realtime"]["api_key"]}},
        )
    assert r.status_code == 400
    assert "ciphertext" in r.text


def test_encrypting_an_envelope_shaped_value_is_refused(fernet):
    with pytest.raises(ValueError):
        at_rest.encrypt_value("enc:v1:k1:abc", "user:1")


# --- 3. rotation / versioning -------------------------------------------------


def test_rotation_reencrypts_onto_new_primary_and_old_key_can_be_dropped(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _set_keys(monkeypatch, f"k1:{old}")
    sealed = encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    assert sealed["realtime"]["api_key"].startswith("enc:v1:k1:")

    _set_keys(monkeypatch, f"k2:{new},k1:{old}")
    # still readable during the overlap window
    assert decrypt_configuration_secrets(sealed, context="user:1")["realtime"]["api_key"] == KEY
    rotated, changed = reencrypt_configuration_secrets(sealed, context="user:1")
    assert changed == 1 and rotated["realtime"]["api_key"].startswith("enc:v1:k2:")
    _, again = reencrypt_configuration_secrets(rotated, context="user:1")
    assert again == 0  # idempotent

    _set_keys(monkeypatch, f"k2:{new}")  # old key removed
    assert decrypt_configuration_secrets(rotated, context="user:1")["realtime"]["api_key"] == KEY
    with pytest.raises(ConfigDecryptionError):
        decrypt_configuration_secrets(sealed, context="user:1")


def test_reencrypt_encrypts_legacy_plaintext_and_reports_corruption(fernet):
    out, changed = reencrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1")
    assert changed == 1 and KEY not in json.dumps(out)
    out["realtime"]["api_key"] = _tamper(out["realtime"]["api_key"])
    with pytest.raises(ConfigDecryptionError):
        reencrypt_configuration_secrets(out, context="user:1")


# --- 4. concurrency -----------------------------------------------------------


def test_concurrent_saves_never_store_mixed_or_plaintext_values(fernet):
    client, store = _client()

    async def go():
        await asyncio.gather(
            *[
                client.update_user_configuration(5, _cfg(k))
                for k in (KEY, KEY2, KEY, KEY2, KEY)
            ]
        )

    asyncio.run(go())
    assert len(store) in (1, 5)  # fake store has no uniqueness; real table is per-user
    for row in store:
        assert KEY not in json.dumps(row.configuration)
        assert KEY2 not in json.dumps(row.configuration)
        got = decrypt_configuration_secrets(row.configuration, context="user:5")
        assert got["realtime"]["api_key"] in (KEY, KEY2)


# --- 5. logs / errors ----------------------------------------------------------


def test_invalid_stored_config_does_not_log_the_decrypted_key(fernet):
    client, store = _client()
    bad = {"llm": {"provider": "no_such_provider", "api_key": KEY, "model": "x"}}
    store.append(
        UserConfigurationModel(
            user_id=3, configuration=encrypt_configuration_secrets(bad, context="user:3")
        )
    )
    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="TRACE")
    try:
        cfg = asyncio.run(client.get_user_configurations(3))
    finally:
        logger.remove(sink)
    assert cfg.llm is None  # fell back to defaults
    assert KEY not in "".join(seen)


def test_validation_errors_returned_by_the_route_never_echo_keys(fernet):
    from api.routes.user import router

    client, store = _client()
    store.append(
        UserConfigurationModel(
            user_id=1,
            configuration=encrypt_configuration_secrets({"realtime": dict(REALTIME)}, context="user:1"),
        )
    )
    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    with (
        patch("api.routes.user.db_client", client),
        patch("api.routes.user.is_livekit_runtime", return_value=True),
    ):
        r = TestClient(app).put(
            "/user/configurations/user",
            json={"realtime": {"provider": "google_realtime", "api_key": KEY2, "model": 123}},
        )
    assert r.status_code == 422
    assert KEY not in r.text and KEY2 not in r.text
    # The dashboard renders each item as "model: message" (UserConfigContext.tsx).
    items = r.json()["detail"]
    assert items and all(set(i) == {"model", "message"} for i in items)
    assert items[0]["model"] == "realtime"


def test_decryption_failure_surfaces_as_503_not_a_traceback(fernet, monkeypatch):
    from api.routes.user import router

    client, store = _client()
    asyncio.run(client.update_user_configuration(1, _cfg()))
    _set_keys(monkeypatch, Fernet.generate_key().decode())  # wrong key now
    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    with patch("api.routes.user.db_client", client):
        r = TestClient(app).get("/user/configurations/user")
    assert r.status_code == 503
    assert KEY not in r.text and "enc:v1" not in r.text


# --- 6. workflow overrides never hold secrets ---------------------------------


def _wf(api_key):
    return {
        "model_overrides": {
            "is_realtime": True,
            "realtime": {"provider": "google_realtime", "model": "m", "api_key": api_key},
        }
    }


def test_new_plaintext_override_secret_is_rejected():
    with pytest.raises(WorkflowSecretError) as exc:
        validate_incoming_override_secrets(_wf(KEY), None)
    assert "model_overrides.realtime.api_key" in str(exc.value)
    assert KEY not in str(exc.value)


def test_db_layer_refuses_plaintext_override_secret_without_touching_the_database():
    client = WorkflowClient.__new__(WorkflowClient)  # no session: guard must fire first
    with pytest.raises(WorkflowSecretError):
        asyncio.run(client.save_workflow_draft(1, None, _wf(KEY)))


def test_overrides_without_secrets_are_accepted():
    cfg = {"model_overrides": {"is_realtime": True, "realtime": {"provider": "google_realtime", "model": "m"}}}
    assert find_plaintext_override_secrets(cfg) == []
    assert_no_plaintext_override_secrets(cfg)


def test_injected_envelope_that_differs_from_stored_is_rejected(fernet):
    legacy, _ = encrypt_override_secrets(_wf(KEY))
    stolen, _ = encrypt_override_secrets(_wf(KEY2))
    with pytest.raises(WorkflowSecretError):
        validate_incoming_override_secrets(stolen, legacy)
    validate_incoming_override_secrets(legacy, legacy)  # unchanged legacy value passes


def test_unchanged_legacy_plaintext_is_adopted_encrypted_on_save(fernet):
    existing = _wf(KEY)  # legacy row still holding plaintext
    incoming = _wf(KEY)  # client round-trip after the merge step restored it
    adopted = adopt_unchanged_legacy_secrets(incoming, existing)
    assert find_plaintext_override_secrets(adopted) == []
    assert KEY not in json.dumps(adopted)
    validate_incoming_override_secrets(adopted, existing)
    # a key the user typed (different from stored) is not adopted, and is rejected
    typed = adopt_unchanged_legacy_secrets(_wf(KEY2), existing)
    with pytest.raises(WorkflowSecretError):
        validate_incoming_override_secrets(typed, existing)


def test_masked_placeholder_roundtrip_for_encrypted_overrides(fernet):
    from api.services.configuration.masking import mask_workflow_configurations

    stored, _ = encrypt_override_secrets(_wf(KEY))
    shown = mask_workflow_configurations(stored)
    assert KEY not in json.dumps(shown)
    merged = merge_workflow_configuration_api_keys(shown, stored)
    validate_incoming_override_secrets(merged, stored)
    assert merged["model_overrides"]["realtime"]["api_key"] == stored["model_overrides"]["realtime"]["api_key"]


def test_legacy_encrypted_override_secret_is_decrypted_at_runtime(fernet):
    stored, n = encrypt_override_secrets(_wf(KEY))
    assert n == 1 and KEY not in json.dumps(stored)
    effective = resolve_effective_config(UserConfiguration(is_realtime=True), stored["model_overrides"])
    assert effective.realtime.api_key == KEY


def test_override_rotation_and_no_plaintext_after_maintenance(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _set_keys(monkeypatch, f"k1:{old}")
    stored, _ = encrypt_override_secrets(_wf(KEY))
    _set_keys(monkeypatch, f"k2:{new},k1:{old}")
    rotated, changed = reencrypt_override_secrets(stored)
    assert changed == 1
    assert ":k2:" in rotated["model_overrides"]["realtime"]["api_key"]
    plain_legacy, changed = reencrypt_override_secrets(_wf(KEY2))
    assert changed == 1 and KEY2 not in json.dumps(plain_legacy)


def test_dashboard_save_and_override_flow_leave_no_plaintext_anywhere(fernet):
    client, store = _client()
    asyncio.run(client.update_user_configuration(1, _cfg()))
    wf_row, _ = encrypt_override_secrets(_wf(KEY2))
    everything = json.dumps([r.configuration for r in store]) + json.dumps(wf_row)
    assert KEY not in everything and KEY2 not in everything


# --- 7. format-tolerant key validation ----------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "AIza" + "x" * 35,  # classic shape
        "AQ.Ab8RN6Kxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",  # newer shape with dots
        "key_with-dashes_and.dots_1234567890",
        "z" * 128,
    ],
)
def test_plausible_key_shapes_are_accepted_locally(good):
    check_key_format("Google/Gemini", good)


@pytest.mark.parametrize(
    "bad",
    ["", " AIza" + "x" * 35, "AIza" + "x" * 35 + "\n", '"' + "x" * 30 + '"', "a b" + "x" * 30,
     "short", "x" * 600, "dgr_" + "x" * 30, "mps-" + "x" * 30, "kéy" + "x" * 30],
)
def test_implausible_values_are_rejected_without_echoing_them(bad):
    with pytest.raises(ValueError) as exc:
        check_key_format("Google/Gemini", bad)
    if len(bad) > 8:
        assert bad not in str(exc.value)


def test_validator_accepts_deepseek_and_list_keys_locally():
    from api.services.configuration.check_validity import UserConfigurationValidator

    v = UserConfigurationValidator()
    assert v._check_api_key("deepseek", "sk-" + "a" * 32)
    assert v._check_api_key("google_realtime", ["AIza" + "x" * 35])


def test_route_trims_pasted_whitespace(fernet):
    from api.routes.user import router

    client, _ = _client()
    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    with (
        patch("api.routes.user.db_client", client),
        patch("api.routes.user.UserConfigurationValidator") as v,
        patch("api.routes.user.is_livekit_runtime", return_value=True),
    ):
        v.return_value.validate = AsyncMock()
        r = TestClient(app).put(
            "/user/configurations/user",
            json={"is_realtime": True, "realtime": {**REALTIME, "api_key": "  " + KEY + "\r\n"}},
        )
    assert r.status_code == 200
    assert asyncio.run(client.get_user_configurations(1)).realtime.api_key == KEY


# --- 8. URLs come from configuration, never literals --------------------------


def _monitor(monkeypatch, ui_url, container=False):
    sm = importlib.import_module("api.services.health.system_monitor")
    probed = []

    class _C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def get(self, url, *a, **k):
            probed.append(url)
            raise RuntimeError("unreachable")

    monkeypatch.setattr(sm, "UI_APP_URL", ui_url)
    monkeypatch.setattr(sm.httpx, "AsyncClient", _C)
    monkeypatch.delenv("HEALTH_UI_PROBE_URLS", raising=False)
    if container:
        monkeypatch.setenv("DEPLOYMENT_MODE", "docker")
    asyncio.run(sm.SystemMonitor().check_frontend())
    return probed


def test_health_probes_only_configured_ui_urls(monkeypatch):
    assert _monitor(monkeypatch, "http://127.0.0.1:4777") == ["http://127.0.0.1:4777"]


def test_container_fallbacks_use_the_configured_ui_port(monkeypatch):
    probed = _monitor(monkeypatch, "http://127.0.0.1:4777", container=True)
    assert probed == [
        "http://127.0.0.1:4777",
        "http://host.docker.internal:4777",
        "http://ui:4777",
    ]


def test_no_hardcoded_ui_ports_in_health_monitor_or_acceptance_scripts():
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"localhost:300[01]|127\.0\.0\.1:300[01]|:3001\b|:3000\b")
    targets = [root / "api/services/health/system_monitor.py"]
    targets += [
        p
        for p in (root / "accept").glob("*.ps1")
        if not p.name.startswith("test-")
    ]
    offenders = []
    for path in targets:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}:{n}")
    assert offenders == []


# --- 9. regressions for independent-audit findings ---------------------------


def test_stale_masked_placeholder_in_inactive_section_never_counts_as_a_key():
    # Saving is allowed by design (test_user_configuration_realtime), but if that
    # section becomes active it must read as unconfigured, so Test Audio is
    # refused instead of sending "****" to a provider.
    from api.services.configuration import provider_readiness as pr
    from api.services.configuration.masking import check_for_masked_keys, mask_key

    cfg = UserConfiguration.model_validate(
        {
            "is_realtime": True,
            "realtime": dict(REALTIME),
            "llm": {"provider": "deepseek", "api_key": "sk-" + "a" * 32, "model": "deepseek-chat"},
            "stt": {"provider": "deepgram", "api_key": mask_key(KEY2), "model": "nova-3-general"},
            "tts": {"provider": "cartesia", "api_key": mask_key(KEY2), "model": "sonic-3"},
        }
    )
    check_for_masked_keys(cfg)  # existing contract: inactive placeholders don't block saves
    premium = resolve_effective_config(cfg, {"is_realtime": False})
    result = pr.evaluate_user_config(premium)
    assert result.status == pr.PROVIDER_UNCONFIGURED
    assert set(result.missing) == {"stt", "tts"}
    assert KEY2 not in result.message

def test_undecryptable_override_envelope_is_rejected_even_when_slot_empty(fernet):
    with pytest.raises(WorkflowSecretError):
        validate_incoming_override_secrets(_wf("enc:v1:k1:garbage"), None)
    with pytest.raises(WorkflowSecretError):
        validate_incoming_override_secrets(_wf("enc:v1:k1:garbage"), {"model_overrides": {}})


def test_login_setup_survives_undecryptable_credentials_without_seeding(monkeypatch):
    from api.services.auth import depends

    fake_db = SimpleNamespace(
        get_user_configurations=AsyncMock(
            side_effect=ConfigDecryptionError("integrity check failed")
        ),
        update_user_configuration=AsyncMock(),
    )
    monkeypatch.setattr(depends, "db_client", fake_db)
    seeded = AsyncMock()
    monkeypatch.setattr(depends, "ensure_default_workflow_for_organization", seeded)
    monkeypatch.setenv("GOOGLE_API_KEY", KEY)  # would normally be seeded
    user = SimpleNamespace(id=1, selected_organization_id=9, provider_id="p")
    asyncio.run(depends.ensure_default_user_setup(user))  # must not raise
    fake_db.update_user_configuration.assert_not_called()  # never overwrite the row


def test_workflow_override_validation_errors_never_echo_input():
    """Source guard: the workflow-settings 422 paths must not return input values."""
    src = (Path(__file__).resolve().parents[1] / "routes" / "workflow.py").read_text(
        encoding="utf-8"
    )
    assert "detail=e.errors())" not in src
    block = src[src.index("Unexpected workflow model override validation error"):][:400]
    assert "str(e)" not in block
    assert "include_input=False" in src
