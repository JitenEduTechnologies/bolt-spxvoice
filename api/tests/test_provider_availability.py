"""Unsupported providers are listed with a reason, never silently dropped, never selectable."""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.schemas.user_configuration import UserConfiguration
from api.services.auth.depends import get_user
from api.services.configuration.provider_availability import (
    REASON_HOSTED_DISABLED,
    REASON_UNSUPPORTED_RUNTIME,
    STATE_UNAVAILABLE,
    build_unavailable,
)
from api.services.configuration.registry import REGISTRY, ServiceType

SERVICES = (("llm", ServiceType.LLM), ("tts", ServiceType.TTS), ("stt", ServiceType.STT))
KEEP = {"llm": {"openai", "google", "deepseek"}, "tts": {"cartesia", "openai"}, "stt": {"deepgram", "openai"}}


@pytest.fixture
def client():
    from api.routes.user import router

    app = FastAPI()
    app.include_router(router)
    user = MagicMock(id=1, is_superuser=False, selected_organization_id=None, provider_id="t")
    app.dependency_overrides[get_user] = lambda: user
    with (
        patch("api.routes.user.is_livekit_runtime", return_value=True),
        patch("api.routes.user.dograh_cloud_enabled", return_value=False),
    ):
        yield TestClient(app)


def _defaults(client):
    r = client.get("/user/configurations/defaults")
    assert r.status_code == 200
    return r.json()


def test_pure_builder_is_stable_sorted_and_attributes_reasons():
    out = build_unavailable(["b", "a", "dograh", "c"], ["c"], hosted_enabled=False)
    assert [p["id"] for p in out] == ["a", "b", "dograh"]
    assert {p["id"]: p["reason_code"] for p in out} == {
        "a": REASON_UNSUPPORTED_RUNTIME,
        "b": REASON_UNSUPPORTED_RUNTIME,
        "dograh": REASON_HOSTED_DISABLED,
    }
    assert all(p["state"] == STATE_UNAVAILABLE for p in out)
    # hosted enabled but runtime-unsupported -> the runtime reason, not "disabled"
    assert build_unavailable(["dograh"], [], hosted_enabled=True)[0]["reason_code"] == REASON_UNSUPPORTED_RUNTIME


def test_api_exposes_unavailable_for_llm_tts_stt_with_all_fields(client):
    data = _defaults(client)
    for service, st in SERVICES:
        items = data["unavailable"][service]
        assert items, f"{service}: nothing listed as unavailable"
        for item in items:
            assert set(item) == {"id", "state", "reason_code", "message"}
            assert item["state"] == STATE_UNAVAILABLE and item["reason_code"] and item["message"]
        ids = {i["id"] for i in items}
        assert ids, service
        # the explanation covers exactly what the registry has beyond what is offered
        assert ids == {str(getattr(p, "value", p)) for p in REGISTRY[st]} - set(data[service])


def test_nothing_disappears_silently(client):
    data = _defaults(client)
    for service, st in SERVICES + (("realtime", ServiceType.REALTIME),):
        registered = {str(getattr(p, "value", p)) for p in REGISTRY[st]}
        accounted = set(data[service]) | {i["id"] for i in data["unavailable"][service]}
        assert accounted == registered, f"{service}: silently dropped {registered - accounted}"


def test_unavailable_providers_are_never_selectable(client):
    data = _defaults(client)
    for service, _ in SERVICES:
        assert not (set(data[service]) & {i["id"] for i in data["unavailable"][service]})


def test_supported_providers_unchanged(client):
    data = _defaults(client)
    for service, keep in KEEP.items():
        assert set(data[service]) == keep, service
        assert keep.isdisjoint({i["id"] for i in data["unavailable"][service]})
    assert {"google_realtime"} <= set(data["realtime"])
    assert data["default_providers"]["realtime"] == "google_realtime"


def test_hosted_provider_reason_when_hosted_services_are_disabled(client):
    items = [i for i in _defaults(client)["unavailable"]["llm"] if i["id"] == "dograh"]
    assert items and items[0]["reason_code"] == REASON_HOSTED_DISABLED


def test_reasons_are_static_and_sanitized(client):
    blob = json.dumps(_defaults(client)["unavailable"])
    assert not re.search(r"[A-Za-z]:\\|/home/|/usr/|Traceback|Exception|Error\b|https?://|api[_-]?key|secret|token|password", blob, re.I)
    messages = {i["message"] for v in _defaults(client)["unavailable"].values() for i in v}
    assert len(messages) <= 2  # fixed text per reason code, not per provider/exception


def test_selecting_an_unavailable_provider_is_rejected_server_side(client):
    from unittest.mock import patch as p

    with (
        p("api.routes.user.db_client") as db,
        p("api.routes.user.UserConfigurationValidator") as v,
    ):
        db.get_user_configurations = AsyncMock(return_value=UserConfiguration())
        db.update_user_configuration = AsyncMock()
        v.return_value.validate = AsyncMock()
        for service, body in (
            ("llm", {"provider": "groq", "api_key": "SYNTHETIC_KEY_0123456789", "model": "m"}),
            ("tts", {"provider": "elevenlabs", "api_key": "SYNTHETIC_KEY_0123456789", "model": "m", "voice": "v"}),
            ("stt", {"provider": "assemblyai", "api_key": "SYNTHETIC_KEY_0123456789", "model": "m"}),
        ):
            r = client.put("/user/configurations/user", json={service: body})
            assert r.status_code == 400, (service, r.text)
            assert "SYNTHETIC_KEY" not in r.text
        db.update_user_configuration.assert_not_called()


def test_non_livekit_runtime_lists_nothing_unavailable():
    from api.routes.user import router

    app = FastAPI()
    app.include_router(router)
    with (
        patch("api.routes.user.is_livekit_runtime", return_value=False),
        patch("api.routes.user.dograh_cloud_enabled", return_value=True),
    ):
        data = TestClient(app).get("/user/configurations/defaults").json()
    assert all(v == [] for v in data["unavailable"].values())
