"""Template -> agent creation: routing regression + no partial records.

Regression: POST /workflow/templates/duplicate used to be captured by
POST /workflow/{workflow_id}/duplicate and rejected with a 422 before any code
ran. Mocked DB and template client only: no live data, no network.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Match

from api.routes import workflow as wf
from api.services.auth.depends import get_user
from api.services.workflow.defaults import build_default_workflow_definition


def _app():
    app = FastAPI()
    app.include_router(wf.router, prefix="/api/v1")
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1, selected_organization_id=7, provider_id="p", is_superuser=False
    )
    return app


def _patch(monkeypatch, template=True, create_side_effect=None, conflict=False):
    template_obj = (
        SimpleNamespace(template_json=build_default_workflow_definition())
        if template
        else None
    )
    client = SimpleNamespace(get_workflow_template=AsyncMock(return_value=template_obj))
    monkeypatch.setattr(wf, "WorkflowTemplateClient", lambda: client)

    created = SimpleNamespace(
        id=501,
        name="Customer Support Assistant",
        status="active",
        created_at=datetime.now(UTC),
        current_definition_id=900,
        template_context_variables={},
        call_disposition_codes=None,
        workflow_configurations={},
    )
    db = SimpleNamespace(
        create_workflow=AsyncMock(return_value=created, side_effect=create_side_effect),
        assert_trigger_paths_available=AsyncMock(),
        sync_triggers_for_workflow=AsyncMock(),
    )
    if conflict:
        db.assert_trigger_paths_available.side_effect = wf.TriggerPathConflictError(
            "trigger path in use"
        )
        monkeypatch.setattr(wf, "extract_trigger_paths", lambda definition: ["/hook"])
    monkeypatch.setattr(wf, "db_client", db)
    return db


def _matched_endpoint(app, method, path):
    scope = {"type": "http", "method": method, "path": path, "root_path": ""}
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return route.endpoint.__name__
    return None


def test_templates_duplicate_is_not_captured_by_workflow_id_route():
    app = _app()
    assert (
        _matched_endpoint(app, "POST", "/api/v1/workflow/templates/duplicate")
        == "duplicate_workflow_template"
    )


def test_numeric_workflow_duplicate_route_is_unchanged():
    app = _app()
    assert (
        _matched_endpoint(app, "POST", "/api/v1/workflow/42/duplicate")
        == "duplicate_workflow_endpoint"
    )


def test_template_duplicate_creates_exactly_one_workflow_in_callers_org(monkeypatch):
    db = _patch(monkeypatch)
    resp = TestClient(_app()).post(
        "/api/v1/workflow/templates/duplicate",
        json={"template_id": 1, "workflow_name": "Customer Support Assistant"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == 501
    db.create_workflow.assert_awaited_once()
    args = db.create_workflow.await_args.args
    assert args[0] == "Customer Support Assistant"
    assert args[2] == 1 and args[3] == 7  # caller's user id and organization


def test_unknown_template_returns_404_and_creates_nothing(monkeypatch):
    db = _patch(monkeypatch, template=False)
    resp = TestClient(_app()).post(
        "/api/v1/workflow/templates/duplicate",
        json={"template_id": 999, "workflow_name": "x"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 404
    db.create_workflow.assert_not_called()
    db.sync_triggers_for_workflow.assert_not_called()


def test_trigger_conflict_returns_409_and_creates_nothing(monkeypatch):
    db = _patch(monkeypatch, conflict=True)
    resp = TestClient(_app()).post(
        "/api/v1/workflow/templates/duplicate",
        json={"template_id": 1, "workflow_name": "x"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 409
    db.create_workflow.assert_not_called()


def test_invalid_body_returns_422_and_creates_nothing(monkeypatch):
    db = _patch(monkeypatch)
    resp = TestClient(_app()).post(
        "/api/v1/workflow/templates/duplicate",
        json={"workflow_name": "missing template id"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 422
    db.create_workflow.assert_not_called()


def test_creation_failure_does_not_sync_triggers_or_report_success(monkeypatch):
    db = _patch(monkeypatch, create_side_effect=RuntimeError("db down"))
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/workflow/templates/duplicate",
        json={"template_id": 1, "workflow_name": "x"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 500
    db.sync_triggers_for_workflow.assert_not_called()
