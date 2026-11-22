"""CP-02 hardening regressions (offline, no database/network).

Covers defects found in the first candidate during adversarial self-review plus the
production-hardening requirements: signed-URL authorisation/expiry, unsupported-script
speech, transcript timestamps, Trash lifecycle, masking, bounded retries and cost labels.
"""

from __future__ import annotations

import io
import json
import sys
import types
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routes import s3_signed_url
from api.services.business import conversation_export as export
from api.services.business import conversation_view as view
from api.services.business.lead_intelligence import (
    analyze_lead_from_transcript,
    extract_timed_turns,
    extract_transcript_from_logs,
)
from api.services.livekit import post_call
from api.services.pricing import cost_analytics as ca

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def run(**kw):
    base = dict(
        id=1, name="WR-1", mode="livekit", state="completed", is_completed=True, is_simulation=False,
        call_disposition="UNKNOWN", usage_info={}, cost_info={}, initial_context={}, gathered_context={},
        annotations={}, logs={}, recording_url=None, created_at=NOW - timedelta(minutes=5),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------- signed-URL authorisation and expiry -------------------- #

RECORDING_KEY = "SPX-VOICE-INBOUND/42-room-20261117T090000Z.mp3"


@pytest.mark.asyncio
async def test_user_without_organization_cannot_reach_any_run_through_signed_url_route():
    nobody = SimpleNamespace(id=9, selected_organization_id=None, is_superuser=False)
    get_run = AsyncMock(return_value=SimpleNamespace(id=42))  # would return ANY run if asked unscoped
    with patch.object(s3_signed_url.db_client, "get_workflow_run", get_run):
        with pytest.raises(HTTPException) as exc:
            await s3_signed_url._authorize_and_get_workflow_run(42, nobody)
    assert exc.value.status_code == 403
    get_run.assert_not_awaited()  # fail closed BEFORE any unscoped lookup


@pytest.mark.asyncio
async def test_run_must_belong_to_the_callers_organization():
    owner = SimpleNamespace(id=1, selected_organization_id=11, is_superuser=False)
    get_run = AsyncMock(return_value=None)  # DB helper found nothing in org 11
    with patch.object(s3_signed_url.db_client, "get_workflow_run", get_run):
        with pytest.raises(HTTPException) as exc:
            await s3_signed_url._authorize_and_get_workflow_run(42, owner)
    assert exc.value.status_code == 403
    assert get_run.await_args.kwargs == {"organization_id": 11}
    root = SimpleNamespace(id=2, selected_organization_id=None, is_superuser=True)
    with patch.object(s3_signed_url.db_client, "get_workflow_run_by_id", AsyncMock(return_value=SimpleNamespace(id=42))):
        assert (await s3_signed_url._authorize_and_get_workflow_run(42, root)).id == 42


@pytest.mark.asyncio
async def test_recording_signed_url_is_clamped_to_fifteen_minutes_whatever_is_requested():
    owner = SimpleNamespace(id=1, selected_organization_id=11, is_superuser=False)
    signer = AsyncMock(return_value="https://signed.example/a")
    with patch.object(s3_signed_url.db_client, "get_workflow_run", AsyncMock(return_value=SimpleNamespace(id=42))), \
         patch.object(s3_signed_url.post_call, "generate_recording_signed_url", signer):
        out = await s3_signed_url.get_signed_url(
            key=RECORDING_KEY, expires_in=604_800, inline=True, storage_backend=None, user=owner
        )
    assert signer.await_args.kwargs["expiration"] == s3_signed_url.MAX_RECORDING_URL_SECONDS == 900
    assert out["expires_in"] == 900


@pytest.mark.asyncio
async def test_generic_signed_url_expiry_is_bounded_and_traversal_is_rejected():
    owner = SimpleNamespace(id=1, selected_organization_id=11, is_superuser=False)
    storage = SimpleNamespace(aget_signed_url=AsyncMock(return_value="https://signed.example/b"))
    with patch.object(s3_signed_url, "storage_fs", storage), \
         patch.object(s3_signed_url.db_client, "get_workflow_run", AsyncMock(return_value=SimpleNamespace(id=7, storage_backend=None))):
        out = await s3_signed_url.get_signed_url(
            key="recordings/7.wav", expires_in=10**9, inline=False, storage_backend=None, user=owner
        )
        assert out["expires_in"] == s3_signed_url.MAX_SIGNED_URL_SECONDS == 3600
        tiny = await s3_signed_url.get_signed_url(
            key="recordings/7.wav", expires_in=1, inline=False, storage_backend=None, user=owner
        )
        assert tiny["expires_in"] == s3_signed_url.MIN_SIGNED_URL_SECONDS
        for bad in ("../etc/passwd", "/abs/path", "a\\b"):
            with pytest.raises(HTTPException) as exc:
                await s3_signed_url.get_signed_url(key=bad, expires_in=60, inline=False, storage_backend=None, user=owner)
            assert exc.value.status_code == 400


class _FakeS3:
    def __init__(self, head=None, error=None):
        self.params = []
        self._head, self._error = head, error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def generate_presigned_url(self, op, Params, ExpiresIn):
        self.params.append((op, Params, ExpiresIn))
        return "https://signed.example/object"

    async def head_object(self, **kw):
        if self._error:
            raise self._error
        return self._head

    async def get_object(self, **kw):
        body = MagicMock()
        body.read = AsyncMock(return_value=b"x" * int(self._head["ContentLength"]))
        return {"Body": body}


def _fake_boto(client):
    mod = types.ModuleType("aioboto3")
    mod.Session = lambda: SimpleNamespace(client=lambda *a, **k: client)
    return mod


CONFIG = post_call._RecordingConfig(
    endpoint_url="http://storage.test", bucket="b", access_key_id="k", secret_access_key="s",
    region="r", force_path_style=True, public_base_url="http://storage.test", key_prefix="SPX-VOICE-INBOUND",
)


@pytest.mark.asyncio
async def test_signed_url_carries_the_requested_expiry_and_disposition():
    client = _FakeS3()
    with patch.object(post_call, "_recording_config_from_env", return_value=CONFIG), \
         patch.dict(sys.modules, {"aioboto3": _fake_boto(client)}):
        await post_call.generate_recording_signed_url(RECORDING_KEY, expiration=900, force_inline=True)
        await post_call.generate_recording_signed_url(RECORDING_KEY, expiration=900, download_filename="conv 42/../x.mp3")
    inline, download = client.params
    assert inline[2] == 900 and inline[1]["ResponseContentDisposition"] == "inline"
    assert inline[1]["ResponseContentType"] == "audio/mpeg"
    disposition = download[1]["ResponseContentDisposition"]
    assert disposition.startswith("attachment; filename=") and ".." not in disposition and "/" not in disposition.split("=", 1)[1]


@pytest.mark.asyncio
async def test_signed_url_refused_without_configuration_or_for_foreign_keys():
    with patch.object(post_call, "_recording_config_from_env", return_value=None):
        assert await post_call.generate_recording_signed_url(RECORDING_KEY) is None
    with patch.object(post_call, "_recording_config_from_env", return_value=CONFIG):
        assert await post_call.generate_recording_signed_url("other-prefix/1.mp3") is None  # not our key space


@pytest.mark.asyncio
async def test_object_existence_is_verified_not_assumed():
    from botocore.exceptions import ClientError

    missing = _FakeS3(error=ClientError({"Error": {"Code": "404"}}, "HeadObject"))
    broken = _FakeS3(error=ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject"))
    empty = _FakeS3(head={"ContentLength": 0})
    ok = _FakeS3(head={"ContentLength": 1234})
    with patch.object(post_call, "_recording_config_from_env", return_value=CONFIG):
        results = []
        for client in (ok, missing, broken, empty):
            with patch.dict(sys.modules, {"aioboto3": _fake_boto(client)}):
                results.append(await post_call.recording_object_exists(RECORDING_KEY))
    assert results == [True, False, None, False]  # None = could not verify, never "ready"
    with patch.object(post_call, "_recording_config_from_env", return_value=None):
        assert await post_call.recording_object_exists(RECORDING_KEY) is None


@pytest.mark.asyncio
async def test_recording_download_for_export_is_size_bounded_before_reading():
    big = _FakeS3(head={"ContentLength": 50})
    small = _FakeS3(head={"ContentLength": 10})
    with patch.object(post_call, "_recording_config_from_env", return_value=CONFIG):
        with patch.dict(sys.modules, {"aioboto3": _fake_boto(big)}):
            assert await post_call.fetch_recording_bytes(RECORDING_KEY, max_bytes=20) is None
        with patch.dict(sys.modules, {"aioboto3": _fake_boto(small)}):
            assert await post_call.fetch_recording_bytes(RECORDING_KEY, max_bytes=20) == b"x" * 10
        assert await post_call.fetch_recording_bytes("not-our-prefix/1.mp3", max_bytes=20) is None


# ------------------------- recording state transitions --------------------------- #

def test_recording_lifecycle_transitions_never_skip_verification():
    def lk(**k):
        return run(gathered_context={"livekit_recording": {"recording_key": RECORDING_KEY, **k}})

    s = lambda r: view.recording_status(r, storage_configured=True)["status"]
    live = dict(state="running", is_completed=False)
    assert s(run(**live)) == "starting"
    assert s(lk(status="started", **{})) == "failed"  # call ended, egress never finished
    assert s(run(**live, gathered_context={"livekit_recording": {"status": "started"}})) == "starting"
    for terminal in ("failed", "aborted", "stop_failed", "not_started"):
        assert s(lk(status=terminal, error="x")) == "failed"
    assert s(lk(status="complete")) == "failed"  # finished but no stored file/url
    ready = run(recording_url="http://m/x.mp3", gathered_context={"livekit_recording": {"status": "stopped", "recording_key": RECORDING_KEY, "file_results": [{"size": 9}]}})
    assert s(ready) == "ready"
    assert view.recording_status(run(), storage_configured=False)["status"] == "not_configured"
    assert view.recording_status(run(), storage_configured=True)["status"] == "disabled"


# ------------------------- unsupported-script speech ------------------------------ #

@pytest.mark.parametrize(
    "caller",
    [
        "నమస్కారం నాకు రెండు బెడ్ రూమ్ ఫ్లాట్ కావాలి గచ్చిబౌలి లో",
        "नमस्ते मुझे दो कमरे का फ्लैट चाहिए गचीबौली में बजट अस्सी लाख",
    ],
)
def test_native_script_speech_is_needs_review_never_a_scored_lead(caller):
    intel = analyze_lead_from_transcript(f"Agent: Namaste, how can I help?\nCaller: {caller}")
    assert intel["language_support"] == "unsupported_script"
    assert intel["lead_classification"] == "Needs Review" and intel["analysis_status"] == "needs_review"
    assert intel["lead_score"] == 0 and intel["score_basis"] == "insufficient_evidence"
    assert intel["customer_intent"] == "General Inquiry"  # nothing guessed
    assert "Needs Review" in intel["title"] and "cannot read reliably" in intel["recommended_next_action"]
    assert intel["outcome"] == "needs_review"


def test_mixed_script_is_capped_to_needs_review_but_latin_evidence_is_kept_for_the_owner():
    intel = analyze_lead_from_transcript(
        "Agent: Hi\nCaller: నమస్కారం నాకు ఇల్లు కావాలి అండి నేను చెప్పాను ఫ్లాట్ గురించి 2BHK flat"
    )
    assert intel["lead_classification"] == "Needs Review" and intel["language_support"] == "unsupported_script"
    assert intel["customer_requirements"].get("bhk") == "2BHK"  # evidence retained, not scored


def test_english_and_transliterated_speech_still_analyse_normally():
    english = analyze_lead_from_transcript("Agent: Hi\nCaller: I want a 3BHK flat in Gachibowli, budget 1.5 crore.")
    assert english["language_support"] == "supported" and english["lead_classification"] in ("Warm", "Hot")
    hinglish = analyze_lead_from_transcript("Agent: Hi\nCaller: mujhe 2BHK flat chahiye Miyapur mein, budget 70 lakh")
    assert hinglish["language_support"] == "supported" and hinglish["customer_requirements"]["bhk"] == "2BHK"


# ------------------------- transcript persistence / timestamps -------------------- #

EPOCH = 1_795_000_000.5

SHAPES = {
    "livekit_history": {"livekit_history": {"items": [
        {"type": "agent_config_update", "instructions": "SECRET"},
        {"type": "message", "role": "assistant", "content": ["Hello"], "created_at": EPOCH},
        {"type": "message", "role": "user", "content": ["నమస్కారం"], "created_at": EPOCH + 3},
        {"type": "message", "role": "developer", "content": ["HIDDEN"]},
    ]}},
    "simulator_messages": {"messages": [{"role": "agent", "content": "Hi"}, {"role": "caller", "content": "Need a flat"}]},
    "text_chat_turns": {"turns": [{"user_message": "Hi", "agent_message": "Hello"}]},
    "list_of_dicts": [{"role": "user", "text": "Hi"}, {"role": "assistant", "text": "Yo"}],
    "flattened_lines": {"messages": ["Caller: Hi there", "Agent: Hello"]},
}


@pytest.mark.parametrize("name", list(SHAPES))
def test_every_supported_log_shape_round_trips_into_speaker_turns(name):
    logs = SHAPES[name]
    text = extract_transcript_from_logs(logs)
    turns = view.timed_transcript_turns(logs)
    assert text and turns
    assert "SECRET" not in text and "HIDDEN" not in text
    assert [t["speaker"] for t in turns] and all(t["speaker"] in ("caller", "agent", "unknown") for t in turns)
    assert [t["index"] for t in turns] == list(range(len(turns)))
    # The plain-text transcript and the timed turns describe the same conversation.
    assert "\n".join(f"{'Caller' if t['speaker']=='caller' else 'Agent'}: {t['text']}" for t in turns) == text


def test_timestamps_are_surfaced_only_when_recorded_never_invented():
    timed = view.timed_transcript_turns(SHAPES["livekit_history"])
    assert timed[0]["timestamp"].startswith("2026-") and timed[1]["timestamp"] > timed[0]["timestamp"]
    assert all(t["timestamp"] is None for t in view.timed_transcript_turns(SHAPES["simulator_messages"]))
    assert extract_timed_turns({"messages": [{"role": "user", "content": "x", "created_at": "not-a-date"}]})[0]["timestamp"] is None
    iso = extract_timed_turns({"messages": [{"role": "user", "content": "x", "timestamp": "2026-11-17T09:00:00Z"}]})[0]["timestamp"]
    assert iso == "2026-11-17T09:00:00+00:00"


def test_empty_incomplete_and_failed_calls_have_no_fabricated_transcript():
    for logs in ({}, None, {"livekit_events": [{"event": "startup_failed"}]}, {"messages": []}):
        assert extract_transcript_from_logs(logs) == "" and view.timed_transcript_turns(logs) == []


# ------------------------- Trash lifecycle ---------------------------------------- #

ACTOR = {"user_id": 1, "role": "owner"}


def test_trash_archive_restore_state_machine_is_reversible_and_never_swaps_silently():
    ann = {"owner_notes": "keep", "analysis": {"status": "complete"}}
    t, changed = view.apply_trash(ann, actor=ACTOR, reason="duplicate", now=NOW)
    assert changed and view.lifecycle_state(t) == "trashed" and t["owner_notes"] == "keep"
    assert view.apply_trash(t, actor=ACTOR)[1] is False  # idempotent
    assert view.apply_archive(t, actor=ACTOR)[1] is False  # must restore first
    r, ch = view.apply_restore(t, actor=ACTOR, now=NOW)
    assert ch and view.lifecycle_state(r) == "active" and r["trash"]["history"][-1]["from"] == "trashed"
    a, _ = view.apply_archive(r, actor=ACTOR, now=NOW)
    assert view.lifecycle_state(a) == "archived" and view.apply_trash(a, actor=ACTOR)[1] is False
    r2, ch2 = view.apply_restore(a, actor=ACTOR)
    assert ch2 and view.lifecycle_state(r2) == "active" and view.apply_restore(r2, actor=ACTOR)[1] is False
    assert [h["action"] for h in r["trash"]["history"]] == ["trashed", "restored"]


def test_trash_never_touches_financial_or_analysis_evidence():
    r = run(cost_info={"billing_status": "ESTIMATED", "actual_cost": {"estimated": True, "total_inr": 8.27}},
            usage_info={"call_duration_seconds": 225}, annotations={"analysis": {"status": "complete"}})
    before = (json.dumps(r.cost_info, sort_keys=True), json.dumps(r.usage_info, sort_keys=True))
    r.annotations, _ = view.apply_trash(r.annotations, actor=ACTOR)
    assert (json.dumps(r.cost_info, sort_keys=True), json.dumps(r.usage_info, sort_keys=True)) == before
    assert view.run_cost_summary(r)["total_inr"] == 8.27 and view.resolve_duration_seconds(r) == 225.0
    assert ca.cost_row(r, workflow_name="W", organization_id=1)["conversation_state"] == "trashed"


# ------------------------- bounded retries, masking, cost labels ------------------ #

def test_analysis_retries_are_bounded_and_visible():
    ann = {}
    for attempt in range(1, view.MAX_ANALYSIS_ATTEMPTS + 1):
        ann = view.apply_analysis_failure(ann, "post_call_failed:X", now=NOW)
        assert ann["analysis"]["attempts"] == attempt
        assert ann["analysis"]["retryable"] is (attempt < view.MAX_ANALYSIS_ATTEMPTS)
    ok, _ = view.apply_ai_analysis(ann, analyze_lead_from_transcript("Agent: Hi\nCaller: I want a 2BHK flat"), transcript_hash_value="h", now=NOW)
    assert ok["analysis"]["status"] == "complete" and ok["analysis"]["attempts"] == 0 and ok["analysis"]["retryable"] is None


@pytest.mark.parametrize(
    "raw, masked",
    [("+919811111111", "+91•••••••11".replace("+91•••••••11", "+91" + "•" * 8 + "11")), ("9811111111", "98" + "•" * 6 + "11")],
)
def test_phone_numbers_are_masked_in_lists(raw, masked):
    assert view.mask_contact(raw) == masked


def test_non_phone_identifiers_are_not_mangled():
    for value in ("WR-4254", "WR-LK-IN-123456789", "Embed Run", "", None):
        assert view.mask_contact(value) == str(value or "")


def test_cost_rows_never_show_unknown_as_zero():
    pstn_pending = run(mode="vobiz", name="CALL-1")
    row = ca.cost_row(pstn_pending, workflow_name="W", organization_id=1)
    assert row["telephony_inr"] == "n/a (carrier cost pending)" and row["total_inr"] == "n/a (not costed)"
    assert row["ai_inr"] == "n/a (not costed)" and row["cost_status"] == "not_costed"
    browser = ca.cost_row(run(initial_context={"participant_kind": 0}), workflow_name="W", organization_id=1)
    assert browser["telephony_inr"] == "n/a (no PSTN)"


def test_cost_json_and_zip_exports_are_complete_and_labelled():
    rows = [ca.cost_row(run(id=i, cost_info={"billing_status": "ESTIMATED", "actual_cost": {"estimated": True, "total_inr": 1.5, "ai_total_inr": 1.5, "components": []}}),
                        workflow_name="=evil", organization_id=2) for i in (1, 2)]
    payload = json.loads(ca.build_cost_json(rows, filters={"filtered": True}))
    assert payload["count"] == 2 and "n/a" in payload["note"] and payload["records"][0]["cost_status"] == "estimated"
    with zipfile.ZipFile(io.BytesIO(ca.build_cost_zip(rows))) as z:
        assert set(z.namelist()) == {"cost-records.csv", "cost-records.json", "manifest.json"}
        assert z.read("cost-records.csv").decode("utf-8-sig").count("\n") >= 3
        assert "'=evil" in z.read("cost-records.csv").decode("utf-8-sig")  # formula injection neutralised
        assert json.loads(z.read("manifest.json"))["count"] == 2


# ------------------------- export safety ------------------------------------------ #

ITEM = {"id": 7, "title": "t", "recording": {"status": "ready"}, "call_type": "outbound", "origin": "browser", "run_status": {}}


def test_zip_with_recordings_uses_only_integer_names_and_reports_every_omission():
    blob = export.build_zip(
        [ITEM, {**ITEM, "id": 8, "recording": {"status": "not_configured"}}],
        {7: [], 8: []},
        recordings={7: b"AUDIO"},
        recording_notes={8: "no recording to include (status: not_configured)"},
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        assert "recordings/7.mp3" in names and "recordings/8.mp3" not in names
        assert all(".." not in n and not n.startswith("/") and "\\" not in n for n in names)
        manifest = {e["run_id"]: e for e in json.loads(z.read("manifest.json"))["entries"]}
        assert manifest[7]["recording_included"] is True and manifest[8]["recording_included"] is False
        assert "not_configured" in manifest[8]["recording_note"]
        assert z.read("recordings/7.mp3") == b"AUDIO"


def test_exports_never_contain_signed_urls_or_storage_keys():
    blob = export.build_json([{**ITEM, "recording": {"status": "ready", "detail": ""}}], transcripts={7: []}).decode()
    for needle in ("X-Amz", "Signature", "SPX-VOICE-INBOUND", "http://", "https://"):
        assert needle not in blob
