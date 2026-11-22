"""P1-A (workflow node secrets) and P1-B (provider endpoint / key exfiltration).

Synthetic keys only; no provider or network calls (DNS is mocked).
"""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.db.workflow_client import WorkflowClient
from api.schemas.user_configuration import UserConfiguration
from api.services.auth.depends import get_user
from api.services.configuration import at_rest
from api.services.configuration.at_rest import ConfigDecryptionError
from api.services.configuration.endpoint_policy import (
    EndpointPolicyError,
    check_endpoint,
    enforce_config_endpoints,
    enforce_override_endpoints,
)
from api.services.configuration.masking import mask_workflow_definition, merge_workflow_api_keys
from api.services.configuration.node_secrets import (
    NodeSecretError,
    find_plaintext_node_secrets,
    open_node_secret,
    reencrypt_node_secrets,
    seal_workflow_node_secrets,
)
from api.services.configuration.resolve import resolve_effective_config

QA_KEY = "SYNTHETIC_QA_KEY_0123456789abcdef"
TUNER_KEY = "SYNTHETIC_TUNER_KEY_fedcba9876543210"
OWNER_KEY = "SYNTHETIC_OWNER_DEEPSEEK_KEY_0011223344"
ENC_NAMES = (
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS",
    "TELEPHONY_ENCRYPTION_KEYS",
    "TELEPHONY_CREDENTIALS_ENCRYPTION_KEY",
    "TELEPHONY_ENCRYPTION_KEY",
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for n in ENC_NAMES + (
        "REQUIRE_CONFIG_ENCRYPTION",
        "SPX_ADMIN_PROVIDER_ENDPOINTS",
        "SPX_TRUSTED_LOCAL_ENDPOINTS",
    ):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(at_rest, "_warned_plaintext", False)


@pytest.fixture
def fernet(monkeypatch):
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")


def _wf(qa=QA_KEY, tuner=TUNER_KEY):
    return {
        "nodes": [
            {"id": "start", "type": "startCall", "data": {"prompt": "hi"}},
            {"id": "qa-1", "type": "qa", "data": {"name": "QA", "qa_api_key": qa}},
            {"id": "tn-1", "type": "tuner", "data": {"name": "Tuner", "tuner_api_key": tuner}},
        ],
        "edges": [],
    }


def _node(wf, nid):
    return next(n for n in wf["nodes"] if n["id"] == nid)["data"]


# =========================== P1-A: node secrets ===============================


def test_seal_encrypts_every_node_secret_and_leaves_no_plaintext(fernet):
    sealed = seal_workflow_node_secrets(_wf(), organization_id=7)
    blob = json.dumps(sealed)
    assert QA_KEY not in blob and TUNER_KEY not in blob
    assert _node(sealed, "qa-1")["qa_api_key"].startswith("enc:v1:")
    assert _node(sealed, "tn-1")["tuner_api_key"].startswith("enc:v1:")
    assert find_plaintext_node_secrets(sealed) == []
    assert _node(sealed, "start") == {"prompt": "hi"}  # non-secret data untouched


def test_api_response_view_never_shows_plaintext_or_full_ciphertext(fernet):
    sealed = seal_workflow_node_secrets(_wf(), organization_id=7)
    shown = mask_workflow_definition(sealed)
    blob = json.dumps(shown)
    assert QA_KEY not in blob and TUNER_KEY not in blob
    assert _node(sealed, "qa-1")["qa_api_key"] not in blob


def test_masked_roundtrip_preserves_stored_ciphertext(fernet):
    stored = seal_workflow_node_secrets(_wf(), organization_id=7)
    incoming = mask_workflow_definition(stored)  # what the UI sends back unchanged
    merged = merge_workflow_api_keys(incoming, stored)
    resealed = seal_workflow_node_secrets(merged, organization_id=7)
    assert _node(resealed, "qa-1")["qa_api_key"] == _node(stored, "qa-1")["qa_api_key"]


def test_unresolved_masked_placeholder_can_never_become_a_key(fernet):
    with pytest.raises(NodeSecretError) as exc:
        seal_workflow_node_secrets(_wf(qa="****************cdef"), organization_id=7)
    assert "qa_api_key" in str(exc.value) and "cdef" not in str(exc.value)


def test_new_key_typed_by_user_replaces_old_one_encrypted(fernet):
    stored = seal_workflow_node_secrets(_wf(), organization_id=7)
    edited = mask_workflow_definition(stored)
    _node(edited, "qa-1")["qa_api_key"] = "SYNTHETIC_NEW_QA_KEY_99999999"
    merged = merge_workflow_api_keys(edited, stored)
    resealed = seal_workflow_node_secrets(merged, organization_id=7)
    assert open_node_secret(_node(resealed, "qa-1")["qa_api_key"], 7) == "SYNTHETIC_NEW_QA_KEY_99999999"


def test_cross_organization_envelope_is_rejected_on_save_and_runtime(fernet):
    foreign = seal_workflow_node_secrets(_wf(), organization_id=1)
    with pytest.raises(NodeSecretError):
        seal_workflow_node_secrets(foreign, organization_id=2)
    with pytest.raises(ConfigDecryptionError) as exc:
        open_node_secret(_node(foreign, "qa-1")["qa_api_key"], 2)
    assert exc.value.reason == "ciphertext belongs to a different owner"


def test_tampered_or_unreadable_node_secret_fails_closed(fernet, monkeypatch):
    sealed = seal_workflow_node_secrets(_wf(), organization_id=7)
    env = _node(sealed, "qa-1")["qa_api_key"]
    tampered = env[:-6] + ("A" if env[-6] != "A" else "B") + env[-5:]
    with pytest.raises(ConfigDecryptionError):
        open_node_secret(tampered, 7)
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    with pytest.raises(ConfigDecryptionError) as exc:
        open_node_secret(env, 7)
    assert QA_KEY not in str(exc.value) and "enc:v1" not in str(exc.value)


def test_encryption_required_refuses_plaintext_node_secrets(monkeypatch):
    monkeypatch.setenv("REQUIRE_CONFIG_ENCRYPTION", "true")
    with pytest.raises(at_rest.ConfigEncryptionRequiredError):
        seal_workflow_node_secrets(_wf(), organization_id=7)


def test_legacy_plaintext_is_migrated_and_rotated(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k1:{old}")
    migrated, n = reencrypt_node_secrets(_wf(), 7)
    assert n == 2 and find_plaintext_node_secrets(migrated) == []
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k2:{new},k1:{old}")
    rotated, n2 = reencrypt_node_secrets(migrated, 7)
    assert n2 == 2 and ":k2:" in _node(rotated, "qa-1")["qa_api_key"]
    monkeypatch.setenv("TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS", f"k2:{new}")
    assert open_node_secret(_node(rotated, "tn-1")["tuner_api_key"], 7) == TUNER_KEY


# --- persistence: the DB layer seals on create and on draft save --------------


class _Res:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(first=lambda: self.value)


class _Sess:
    def __init__(self, results):
        self.results, self.added = list(results), []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def execute(self, _q):
        return _Res(self.results.pop(0) if self.results else None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def refresh(self, _o):
        pass


def test_create_workflow_persists_only_ciphertext(fernet):
    sess = _Sess([])
    client = WorkflowClient.__new__(WorkflowClient)
    client.async_session = lambda: sess
    asyncio.run(client.create_workflow("wf", _wf(), user_id=1, organization_id=7))
    stored = json.dumps([getattr(o, "workflow_definition", None) or getattr(o, "workflow_json", None) for o in sess.added])
    assert QA_KEY not in stored and TUNER_KEY not in stored and "enc:v1:" in stored


def test_draft_save_and_version_copy_keep_ciphertext(fernet):
    draft = SimpleNamespace(workflow_json={}, workflow_configurations={}, template_context_variables={})
    workflow = SimpleNamespace(workflow_definition=None, workflow_configurations=None, template_context_variables=None)
    sess = _Sess([7, draft, workflow])  # org id, existing draft, workflow row
    client = WorkflowClient.__new__(WorkflowClient)
    client.async_session = lambda: sess
    asyncio.run(client.save_workflow_draft(5, workflow_definition=_wf()))
    for row_json in (draft.workflow_json, workflow.workflow_definition):
        assert QA_KEY not in json.dumps(row_json)
        assert open_node_secret(_node(row_json, "qa-1")["qa_api_key"], 7) == QA_KEY


# --- runtime: decryption only in the consumer ---------------------------------


def test_qa_runtime_gets_plaintext_only_inside_the_consumer(fernet):
    from api.services.workflow.dto import QANodeData
    from api.services.workflow.qa import llm_config

    sealed = seal_workflow_node_secrets(_wf(), organization_id=7)
    qa = QANodeData.model_validate({"name": "QA", "qa_use_workflow_llm": False, "qa_provider": "openai",
                                    "qa_model": "gpt-4.1", "qa_api_key": _node(sealed, "qa-1")["qa_api_key"]})
    run = SimpleNamespace(workflow=SimpleNamespace(organization_id=7))
    _, _, key, _ = asyncio.run(llm_config.resolve_llm_config(qa, run))
    assert key == QA_KEY
    other = SimpleNamespace(workflow=SimpleNamespace(organization_id=8))
    _, _, key2, _ = asyncio.run(llm_config.resolve_llm_config(qa, other))
    assert key2 is None  # fails closed: caller reports no_api_key


def test_tuner_runtime_fails_closed_on_foreign_ciphertext(fernet):
    tuner = pytest.importorskip("api.services.integrations.tuner.completion")
    sealed = seal_workflow_node_secrets(_wf(), organization_id=1)
    ctx = SimpleNamespace(
        workflow_run=SimpleNamespace(logs={"tuner_payload": {"x": 1}}, recording_url=None,
                                     workflow=SimpleNamespace(organization_id=2)),
        public_token=None,
    )
    node = next(n for n in sealed["nodes"] if n["id"] == "tn-1")
    node["data"].update({"tuner_enabled": True, "tuner_workspace_id": 1, "tuner_agent_id": "a"})
    with patch.object(tuner, "post_call", AsyncMock()) as sent:
        out = asyncio.run(tuner.run_completion([node], ctx))
    sent.assert_not_called()
    assert out["tuner_tn-1"] == {"error": "credential_unreadable"}


# ===================== P1-B: endpoint / key exfiltration ======================


@pytest.mark.parametrize(
    "provider,url",
    [
        ("openai", "https://api.openai.com/v1"),
        ("deepseek", "https://api.deepseek.com"),
        ("openrouter", "https://openrouter.ai/api/v1"),
        ("elevenlabs", "https://api.elevenlabs.io"),
        ("elevenlabs", "https://api.eu.residency.elevenlabs.io"),
        ("azure", "https://myres.openai.azure.com"),
        ("deepseek", "https://api.deepseek.com./v1"),  # trailing-dot FQDN
    ],
)
def test_builtin_official_endpoints_are_allowed(provider, url):
    check_endpoint(provider, url, has_credential=True)


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example.com/v1",
        "http://api.deepseek.com",  # downgrade
        "https://api.deepseek.com:8443",  # non-default port
        "https://api.deepseek.com.attacker.example",  # suffix trick
        "https://user:pass@api.deepseek.com",  # embedded credentials
        "https://api.deepseek.com@attacker.example",  # userinfo confusion
        "https://api%2edeepseek.com",  # percent-encoded host
        "https://api.deepseek.cοm",  # homoglyph / non-ASCII
        "https://api.deepseek.com\\@attacker.example",  # backslash parser confusion
        "ftp://api.deepseek.com",
        "file:///etc/passwd",
        "https://127.0.0.1",
        "https://2130706433",  # decimal 127.0.0.1
        "https://0x7f000001",  # hex
        "https://0177.0.0.1",  # octal
        "https://127.1",  # short form
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",  # IPv4-mapped IPv6
        "https://169.254.169.254/latest/meta-data",
        "https://10.0.0.5",
        "https://metadata.google.internal",
        " https://api.deepseek.com",
    ],
)
def test_owner_key_cannot_be_sent_to_unapproved_or_unsafe_hosts(url):
    with pytest.raises(EndpointPolicyError) as exc:
        check_endpoint("deepseek", url, has_credential=True)
    assert "pass" not in str(exc.value)  # never echo URL credentials


def test_operator_approved_custom_endpoint_is_allowed_after_ssrf_check(monkeypatch):
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://llm-gw.example.org/v1")
    public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    with patch.object(socket, "getaddrinfo", return_value=public):
        check_endpoint("deepseek", "https://llm-gw.example.org/v1", has_credential=True)


@pytest.mark.parametrize("ip", ["10.1.2.3", "127.0.0.1", "169.254.169.254", "::1", "fd00::1"])
def test_approved_host_resolving_to_internal_address_is_rejected(monkeypatch, ip):
    """DNS (incl. rebinding to an internal address) cannot bypass the SSRF check."""
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://llm-gw.example.org")
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    with patch.object(socket, "getaddrinfo", return_value=[(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]):
        with pytest.raises(EndpointPolicyError):
            check_endpoint("deepseek", "https://llm-gw.example.org", has_credential=True)


def test_approval_is_per_provider_and_exact_origin(monkeypatch):
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "openrouter=https://gw.example.org")
    with pytest.raises(EndpointPolicyError):
        check_endpoint("deepseek", "https://gw.example.org", has_credential=True)
    with pytest.raises(EndpointPolicyError):
        check_endpoint("openrouter", "https://gw.example.org:8443", has_credential=True)


def test_keyless_local_server_is_not_restricted():
    check_endpoint("speaches", "http://localhost:9100/v1", has_credential=False)


def test_trusted_local_policy_is_explicit_and_scoped_to_approved_endpoints(monkeypatch):
    with pytest.raises(EndpointPolicyError):
        check_endpoint("openai", "http://127.0.0.1:11434/v1", has_credential=True)
    monkeypatch.setenv("SPX_TRUSTED_LOCAL_ENDPOINTS", "true")
    with pytest.raises(EndpointPolicyError):  # still needs operator approval
        check_endpoint("openai", "http://127.0.0.1:11434/v1", has_credential=True)
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "openai=http://127.0.0.1:11434/v1")
    check_endpoint("openai", "http://127.0.0.1:11434/v1", has_credential=True)


# --- workflow overrides run with the OWNER's key ------------------------------


def _owner_config():
    return UserConfiguration.model_validate(
        {
            "is_realtime": False,
            "llm": {"provider": "deepseek", "api_key": OWNER_KEY, "model": "deepseek-chat"},
            "stt": {"provider": "deepgram", "api_key": "SYNTH_DG_0123456789", "model": "nova-3-general"},
            "tts": {"provider": "cartesia", "api_key": "SYNTH_CT_0123456789", "model": "sonic-3",
                    "voice": "248be419-c632-4f23-adf1-5324ed7dbf10"},
        }
    )


def test_member_override_cannot_redirect_owner_key(monkeypatch):
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://gw.example.org")
    bad = {"llm": {"provider": "deepseek", "base_url": "https://attacker.example/v1"}}
    with pytest.raises(EndpointPolicyError):
        resolve_effective_config(_owner_config(), bad)
    approved_custom = {"llm": {"provider": "deepseek", "base_url": "https://gw.example.org"}}
    with pytest.raises(EndpointPolicyError):  # even operator-approved hosts: not via overrides
        resolve_effective_config(_owner_config(), approved_custom)
    with pytest.raises(EndpointPolicyError):  # provider omitted -> cannot be proven built-in
        enforce_override_endpoints({"llm": {"base_url": "https://api.deepseek.com"}})


def test_premium_override_with_official_deepseek_url_still_works():
    from api.services.business.agent_training import resolve_product_mode_overrides

    _, overrides = resolve_product_mode_overrides("premium")
    eff = resolve_effective_config(_owner_config(), overrides)
    assert eff.llm.base_url == "https://api.deepseek.com" and eff.llm.api_key == OWNER_KEY


def test_db_layer_refuses_unsafe_override_endpoint_before_touching_the_database():
    client = WorkflowClient.__new__(WorkflowClient)
    cfg = {"model_overrides": {"llm": {"provider": "deepseek", "base_url": "https://evil.example"}}}
    with pytest.raises(EndpointPolicyError):
        asyncio.run(client.save_workflow_draft(1, None, cfg))


def test_user_config_put_rejects_custom_endpoint_with_key():
    from api.routes.user import router

    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    db = SimpleNamespace(get_user_configurations=AsyncMock(return_value=UserConfiguration()),
                         update_user_configuration=AsyncMock())
    with (
        patch("api.routes.user.db_client", db),
        patch("api.routes.user.UserConfigurationValidator") as v,
        patch("api.routes.user.is_livekit_runtime", return_value=True),
    ):
        v.return_value.validate = AsyncMock()
        r = TestClient(app).put("/user/configurations/user", json={
            "llm": {"provider": "deepseek", "model": "deepseek-chat", "api_key": OWNER_KEY,
                    "base_url": "https://attacker.example/v1"}})
    assert r.status_code == 400
    assert OWNER_KEY not in r.text
    db.update_user_configuration.assert_not_called()


def test_enforce_config_endpoints_checks_every_credentialed_section():
    cfg = UserConfiguration.model_validate({
        "is_realtime": False,
        "llm": {"provider": "deepseek", "api_key": OWNER_KEY, "model": "deepseek-chat", "base_url": "https://10.0.0.9"},
    })
    with pytest.raises(EndpointPolicyError):
        enforce_config_endpoints(cfg)


# --- immediately before sending -----------------------------------------------


def test_pipecat_factory_rechecks_before_building_client():
    from api.services.pipecat import service_factory

    with pytest.raises(HTTPException) as exc:
        service_factory.create_llm_service_from_provider(
            "deepseek", "deepseek-chat", OWNER_KEY, base_url="https://attacker.example/v1"
        )
    assert exc.value.status_code == 400 and OWNER_KEY not in str(exc.value.detail)


def test_livekit_worker_rechecks_before_building_client():
    from api.services.livekit import worker

    cfg = SimpleNamespace(
        is_realtime=False, realtime=None,
        llm=SimpleNamespace(provider="deepseek", model="deepseek-chat", api_key=OWNER_KEY, base_url="https://attacker.example"),
        stt=SimpleNamespace(provider="deepgram", model="nova-3-general", api_key="SYNTH_DG_0123456789", language="en"),
        tts=SimpleNamespace(provider="cartesia", model="sonic-3", api_key="SYNTH_CT_0123456789", voice="v"),
    )
    with patch.object(worker.openai, "LLM") as llm_ctor:
        with pytest.raises(EndpointPolicyError):
            worker._create_session(cfg, vad=object())
    llm_ctor.assert_not_called()


def test_cross_origin_redirect_never_carries_the_key():
    """httpx (used by the OpenAI-compatible SDK clients) drops Authorization on
    a redirect to another origin, so a redirect cannot bypass host validation."""
    seen = {}

    def handler(request: httpx.Request):
        if request.url.host == "api.deepseek.com":
            return httpx.Response(302, headers={"Location": "https://attacker.example/steal"})
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as c:
        c.get("https://api.deepseek.com/v1/models", headers={"Authorization": f"Bearer {OWNER_KEY}"})
    assert "auth" in seen and seen["auth"] is None


def test_static_node_secret_list_covers_every_integration_sensitive_field():
    """Every sensitive_fields=(...) declared in integration source must be in the
    static list, so an unloaded integration never leaves its key in plaintext."""
    import re
    from pathlib import Path

    from api.services.configuration.masking import _NODE_SECRET_FIELDS

    root = Path(__file__).resolve().parents[1] / "services" / "integrations"
    declared = set()
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"sensitive_fields\s*=\s*\(([^)]*)\)", text):
            declared.update(re.findall(r"['\"]([A-Za-z0-9_]+)['\"]", block))
    covered = {f for fields in _NODE_SECRET_FIELDS.values() for f in fields}
    assert declared and declared <= covered, declared - covered


# ============== Re-review P2 follow-ups (CGNAT/NAT64, embeddings, warning) ==============


@pytest.mark.parametrize(
    "ip",
    ["100.64.0.1", "100.127.255.254", "64:ff9b::7f00:1", "64:ff9b::a00:1", "2002:7f00:1::1", "2002:a9fe:a9fe::1"],
)
def test_approved_host_resolving_to_cgnat_nat64_or_6to4_internal_is_rejected(monkeypatch, ip):
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://llm-gw.example.org")
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    with patch.object(socket, "getaddrinfo", return_value=[(fam, socket.SOCK_STREAM, 6, "", (ip, 0))]):
        with pytest.raises(EndpointPolicyError):
            check_endpoint("deepseek", "https://llm-gw.example.org", has_credential=True)


def test_nat64_and_6to4_of_a_public_address_is_still_allowed(monkeypatch):
    monkeypatch.setenv("SPX_ADMIN_PROVIDER_ENDPOINTS", "deepseek=https://llm-gw.example.org")
    for ip in ("64:ff9b::5db8:d822", "2002:5db8:d822::1"):  # 93.184.216.34
        with patch.object(socket, "getaddrinfo", return_value=[(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0))]):
            check_endpoint("deepseek", "https://llm-gw.example.org", has_credential=True)


def test_embedding_client_is_not_built_for_an_unapproved_host_and_never_echoes_the_key():
    from api.services.gen_ai.embedding.factory import create_embedding_service

    with pytest.raises(EndpointPolicyError) as exc:
        create_embedding_service(
            db_client=MagicMock(), provider="openai", api_key="SYNTHETIC_KEY_0123456789",
            model="m", base_url="https://evil.example.net/v1",
        )
    assert "SYNTHETIC_KEY" not in str(exc.value)


def test_embedding_client_allows_official_endpoint_and_default():
    from api.services.gen_ai.embedding.factory import create_embedding_service

    for url in (None, "https://api.openai.com/v1"):
        svc = create_embedding_service(
            db_client=MagicMock(), provider="openai", api_key="SYNTHETIC_KEY_0123456789", model="m", base_url=url,
        )
        assert svc is not None


def test_plaintext_node_secrets_without_a_key_log_a_warning_without_the_secret():
    from loguru import logger

    seen = []
    sink = logger.add(lambda m: seen.append(str(m)), level="WARNING")
    try:
        out = seal_workflow_node_secrets(_wf(), organization_id=1)
    finally:
        logger.remove(sink)
    assert _node(out, "qa-1")["qa_api_key"] == QA_KEY  # unchanged: no key available, not required
    text = "".join(seen)
    assert "plaintext" in text and QA_KEY not in text and TUNER_KEY not in text
