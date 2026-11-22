"""CP-02 hardening: Trash lifecycle, audit-first bulk actions, exports, masking and tenancy.

Reuses the SQLite-backed ``env`` fixture of ``test_cp02_conversations_routes`` so the real
query statements and route handlers are exercised (no PostgreSQL, network or paid call).
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.db.models import WorkflowRunModel
from api.routes import business
from api.services.business import conversation_query as cq
from api.tests.test_cp02_conversations_routes import (  # noqa: F401  (fixture + helpers)
    ORG_A,
    _read,
    env,
    page,
)


def _ids(res):
    return {i["id"] for i in res["items"]}


@pytest.mark.asyncio
async def test_trash_hides_from_active_and_archived_views_and_is_restorable(env):
    cost_before = json.dumps(env.session.get(WorkflowRunModel, 337).cost_info, sort_keys=True)
    res = await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337, 338, 900]), user=env.a)
    assert res == {"status": "trashed", "selected": 2, "changed": 2, "skipped": 1}
    assert not ({337, 338} & _ids(await page(env, limit=100)))
    assert not ({337, 338} & _ids(await page(env, limit=100, archived="archived")))
    trashed = await page(env, limit=100, archived="trashed")
    assert _ids(trashed) == {337, 338} and all(i["trashed"] and i["lifecycle"] == "trashed" for i in trashed["items"])
    assert "trash" not in (env.session.get(WorkflowRunModel, 900).annotations or {})  # other tenant untouched
    assert json.dumps(env.session.get(WorkflowRunModel, 337).cost_info, sort_keys=True) == cost_before
    assert trashed["items"][0]["cost"]["total_inr"] == 8.27  # financial history retained in Trash
    detail = await business.get_conversation_detail(337, user=env.a)  # still openable, e.g. from Cost Analytics
    assert detail["trashed"] is True and detail["cost"]["status"] == "estimated"
    again = await business.bulk_archive_conversations(business.BulkSelectionRequest(ids=[337]), user=env.a)
    assert again["changed"] == 0  # archive cannot silently swap a trashed record
    back = await business.bulk_restore_conversations(business.BulkSelectionRequest(ids=[337, 338]), user=env.a)
    assert back["changed"] == 2 and {337, 338} <= _ids(await page(env, limit=100))
    assert all(c.kwargs["organization_id"] == ORG_A for c in env.bus.await_args_list)


@pytest.mark.asyncio
async def test_bulk_action_is_refused_when_the_audit_record_cannot_be_written(env):
    env.audit.side_effect = RuntimeError("audit store down")
    with pytest.raises(HTTPException) as exc:
        await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337, 338]), user=env.a)
    assert exc.value.status_code == 503 and "could not be audited" in exc.value.detail
    assert env.audit.await_args.kwargs["fail_closed"] is True
    assert "trash" not in (env.session.get(WorkflowRunModel, 337).annotations or {})  # nothing changed
    assert 337 in _ids(await page(env, limit=100))


@pytest.mark.asyncio
async def test_partial_failure_is_reported_audited_and_safe_to_repeat(env):
    real = env.db.mutate_workflow_run_annotations
    calls = {"n": 0}

    async def flaky(run_id, mutator, *, organization_id=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db hiccup")
        return await real(run_id, mutator, organization_id=organization_id)

    env.db.mutate_workflow_run_annotations = flaky
    with pytest.raises(HTTPException) as exc:
        await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337, 338, 336]), user=env.a)
    assert exc.value.status_code == 500 and "safe to repeat" in exc.value.detail and "1 changed" in exc.value.detail
    assert "FAILED" in [c.kwargs.get("outcome") for c in env.audit.await_args_list]
    assert 337 not in _ids(await page(env, limit=100)) and 338 in _ids(await page(env, limit=100))
    env.db.mutate_workflow_run_annotations = real
    done = await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337, 338, 336]), user=env.a)
    assert done["selected"] == 3 and done["changed"] == 2  # 337 already trashed: idempotent
    assert _ids(await page(env, limit=100, archived="trashed")) == {337, 338, 336}


@pytest.mark.asyncio
async def test_select_all_matching_trash_verifies_scope_and_respects_cap(env):
    f = business.ConversationFilterRequest(source="pstn")
    current = (await page(env, limit=1, source="pstn"))["total"]
    with pytest.raises(HTTPException) as exc:
        await business.bulk_trash_conversations(
            business.BulkSelectionRequest(filters=f, expected_count=current + 1), user=env.a
        )
    assert exc.value.status_code == 409
    with patch.object(cq, "MAX_BULK_SELECTION", current - 1):
        with pytest.raises(HTTPException) as exc:
            await business.bulk_trash_conversations(
                business.BulkSelectionRequest(filters=f, expected_count=current), user=env.a
            )
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await business.bulk_trash_conversations(
                business.BulkSelectionRequest(ids=list(range(1, current + 2))), user=env.a
            )
        assert exc.value.status_code == 400
    done = await business.bulk_trash_conversations(
        business.BulkSelectionRequest(filters=f, expected_count=current), user=env.a
    )
    assert done["changed"] == current
    assert (await page(env, limit=100, archived="trashed", source="pstn"))["total"] == current
    restored = await business.bulk_restore_conversations(
        business.BulkSelectionRequest(
            filters=business.ConversationFilterRequest(source="pstn", archived="trashed"), expected_count=current
        ),
        user=env.a,
    )
    assert restored["changed"] == current


@pytest.mark.asyncio
async def test_contacts_are_masked_in_lists_but_full_in_detail_and_exports(env):
    listed = {i["id"]: i for i in (await page(env, limit=100))["items"]}
    assert listed[337]["contact"] == "+91" + "•" * 8 + "11" and listed[337]["contact_masked"] is True
    assert "9811111111" not in json.dumps(listed[337])
    assert listed[337]["customer_name"] == "" and listed[337]["has_customer_name"] is False  # no fabricated identity
    detail = await business.get_conversation_detail(337, user=env.a)
    assert detail["contact"] == "+919811111111" and detail["contact_masked"] is False
    csv_resp = await business.export_conversations(format="csv", ids="337", user=env.a)
    assert "+919811111111" in (await _read(csv_resp)).decode("utf-8-sig")
    assert "key" not in detail["recording"] and "recording_url" not in detail  # no storage key / URL leak


@pytest.mark.asyncio
async def test_detail_exposes_recorded_timestamps_only(env):
    run = env.session.get(WorkflowRunModel, 337)
    run.logs = {
        "livekit_history": {
            "items": [
                {"type": "message", "role": "assistant", "content": ["Hello"], "created_at": 1_795_000_000.0},
                {"type": "message", "role": "user", "content": ["I need a 2BHK"]},
            ]
        }
    }
    env.session.commit()
    d = await business.get_conversation_detail(337, user=env.a)
    assert d["timestamps_available"] is True
    assert d["transcript_turns"][0]["timestamp"].startswith("2026-") and d["transcript_turns"][1]["timestamp"] is None


def _make_ready_recording(env, run_id, key):
    run = env.session.get(WorkflowRunModel, run_id)
    run.recording_url = f"http://minio/{key}"
    run.gathered_context = {
        "livekit_recording": {"status": "complete", "recording_key": key, "file_results": [{"size": 9}]}
    }
    env.session.commit()


@pytest.mark.asyncio
async def test_zip_includes_audio_only_when_requested_verified_and_within_limits(env):
    from api.services.livekit import post_call

    _make_ready_recording(env, 337, "SPX-VOICE-INBOUND/337-room.mp3")
    _make_ready_recording(env, 338, "SPX-VOICE-INBOUND/338-room.mp3")
    fetch = AsyncMock(return_value=b"AUDIO")
    with patch.object(post_call, "fetch_recording_bytes", fetch), patch.object(
        business, "_recording_storage_configured", lambda: True
    ):
        plain = await business.export_conversations(format="zip", ids="337,338", user=env.a)
        with zipfile.ZipFile(io.BytesIO(await _read(plain))) as z:
            assert not [n for n in z.namelist() if n.startswith("recordings/")]
        fetch.assert_not_awaited()  # audio is never fetched unless explicitly requested

        asked = await business.export_conversations(format="zip", ids="337,338,900", include_recordings=True, user=env.a)
        with zipfile.ZipFile(io.BytesIO(await _read(asked))) as z:
            names = z.namelist()
            assert {"recordings/337.mp3", "recordings/338.mp3"} <= set(names) and "recordings/900.mp3" not in names
            assert all(".." not in n and not n.startswith("/") for n in names)
        keys = {c.args[0] for c in fetch.await_args_list}
        assert keys == {"SPX-VOICE-INBOUND/337-room.mp3", "SPX-VOICE-INBOUND/338-room.mp3"}  # tenant B never fetched

        with patch.object(business, "MAX_ZIP_RECORDINGS", 1):
            capped = await business.export_conversations(format="zip", ids="337,338", include_recordings=True, user=env.a)
        with zipfile.ZipFile(io.BytesIO(await _read(capped))) as z:
            manifest = {e["run_id"]: e for e in json.loads(z.read("manifest.json"))["entries"]}
        assert sum(1 for e in manifest.values() if e["recording_included"]) == 1
        assert any("at most 1 recordings" in (e["recording_note"] or "") for e in manifest.values())

    with patch.object(post_call, "fetch_recording_bytes", AsyncMock(return_value=None)), patch.object(
        business, "_recording_storage_configured", lambda: True
    ):
        missing = await business.export_conversations(format="zip", ids="337", include_recordings=True, user=env.a)
        with zipfile.ZipFile(io.BytesIO(await _read(missing))) as z:
            entry = json.loads(z.read("manifest.json"))["entries"][0]
        assert entry["recording_included"] is False and "could not be verified" in entry["recording_note"]


@pytest.mark.asyncio
async def test_unscoped_and_foreign_users_cannot_export_archive_or_list(env):
    nobody = SimpleNamespace(id=9, selected_organization_id=None, is_superuser=False)
    for call in (
        lambda: business.export_conversations(format="csv", user=nobody),
        lambda: business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337]), user=nobody),
        lambda: business.bulk_restore_conversations(business.BulkSelectionRequest(ids=[337]), user=nobody),
        lambda: business.list_conversations(user=nobody),
        lambda: business.stream_dashboard_events(last_event_id=None, user=nobody),
        lambda: business.create_sse_connection_ticket(user=nobody),
        lambda: business.publish_dashboard_event(
            business.PublishEventRequest(event_type="x", resource_type="y", resource_id="1"), user=nobody
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403
    resp = await business.export_conversations(format="csv", ids="337,338", user=env.b)
    assert resp.headers["X-Export-Count"] == "0"  # tenant B asking for tenant A's ids gets nothing
    res = await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337, 338]), user=env.b)
    assert res["selected"] == 0 and res["skipped"] == 2
    assert "trash" not in (env.session.get(WorkflowRunModel, 337).annotations or {})
    with pytest.raises(HTTPException) as exc:
        await business.export_conversations(
            format="csv", ids=",".join(str(i) for i in range(1, cq.MAX_BULK_SELECTION + 5)), user=env.a
        )
    assert exc.value.status_code == 400  # id-list cap cannot be bypassed via export


@pytest.mark.asyncio
async def test_client_cannot_forge_platform_events_and_custom_events_stay_in_their_org(env):
    for reserved in ("conversation_finalized", "conversation_analysis_failed", "audit.fake", "lead_updated"):
        with pytest.raises(HTTPException) as exc:
            await business.publish_dashboard_event(
                business.PublishEventRequest(event_type=reserved, resource_type="conversation", resource_id="1"),
                user=env.a,
            )
        assert exc.value.status_code == 400
    env.bus.reset_mock()
    env.bus.return_value = {"id": 1}
    await business.publish_dashboard_event(
        business.PublishEventRequest(event_type="owner_note_added", resource_type="note", resource_id="9"), user=env.a
    )
    assert env.bus.await_args.kwargs["organization_id"] == ORG_A


@pytest.mark.asyncio
async def test_invalid_inputs_are_rejected_cleanly(env):
    for kwargs in (
        {"archived": "deleted"},
        {"source": "satellite"},
        {"status": "weird"},
        {"sort": "password"},
        {"call_type": "sideways"},
    ):
        with pytest.raises(HTTPException) as exc:
            await page(env, **kwargs)
        assert exc.value.status_code == 400
    for bad_id in (-5, 0, 10**12):
        with pytest.raises(HTTPException) as exc:
            await business.get_conversation_detail(bad_id, user=env.a)
        assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[]), user=env.a)
    assert exc.value.status_code == 400
