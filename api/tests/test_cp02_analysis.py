"""CP-02 pure-logic acceptance tests: analysis, durations, status, recording, cost, export.

No database, network or paid call. Fixtures mirror the real shapes observed for
runs #337 / #338 (LiveKit history items, usage_info/cost_info keys).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from api.services.business import conversation_export as export
from api.services.business import conversation_view as view
from api.services.business.lead_intelligence import (
    ANALYZER_VERSION,
    analyze_lead_from_transcript,
    extract_transcript_from_logs,
    parse_transcript_turns,
)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def run(**kw):
    base = dict(
        id=1,
        name="WR-1",
        mode="livekit",
        state="completed",
        is_completed=True,
        is_simulation=False,
        call_disposition="UNKNOWN",
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        annotations={},
        logs={},
        recording_url=None,
        created_at=NOW - timedelta(minutes=5),
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Duration (runs #337 / #338)
# --------------------------------------------------------------------------- #


def test_duration_for_runs_337_and_338_uses_canonical_key():
    r337 = run(usage_info={"call_duration_seconds": 225}, cost_info={"call_duration_seconds": 225.0})
    r338 = run(usage_info={"call_duration_seconds": 3605}, cost_info={"call_duration_seconds": 3605.0})
    assert view.resolve_duration_seconds(r337) == 225.0
    assert view.resolve_duration_seconds(r338) == 3605.0


def test_duration_legacy_and_cost_fallbacks_and_missing():
    assert view.resolve_duration_seconds(run(usage_info={"duration_seconds": 138.0})) == 138.0
    assert view.resolve_duration_seconds(run(cost_info={"call_duration_seconds": 61})) == 61.0
    # Canonical beats legacy when both exist.
    both = run(usage_info={"call_duration_seconds": 10, "duration_seconds": 99})
    assert view.resolve_duration_seconds(both) == 10.0
    assert view.resolve_duration_seconds(run()) == 0.0
    assert view.resolve_duration_seconds(run(usage_info={"call_duration_seconds": "bad"})) == 0.0


# --------------------------------------------------------------------------- #
# Run status: never report an unstarted run as completed
# --------------------------------------------------------------------------- #


def test_initialized_run_is_not_reported_completed():
    old = run(state="initialized", is_completed=False, created_at=NOW - timedelta(days=3))
    assert view.resolve_run_status(old, NOW)["status"] == "not_started"
    fresh = run(state="initialized", is_completed=False, created_at=NOW - timedelta(minutes=1))
    assert view.resolve_run_status(fresh, NOW)["status"] == "starting"


def test_running_run_goes_stale_and_failed_wins():
    stale = run(state="running", is_completed=False, created_at=NOW - timedelta(hours=5))
    assert view.resolve_run_status(stale, NOW)["status"] == "stale"
    live = run(state="running", is_completed=False, created_at=NOW - timedelta(minutes=3))
    assert view.resolve_run_status(live, NOW)["status"] == "in_progress"
    failed = run(annotations={"livekit_startup_error": "x"})
    assert view.resolve_run_status(failed, NOW)["status"] == "failed"
    done = run(annotations={"livekit_finalization": {"state": "failed"}})
    assert view.resolve_run_status(done, NOW)["status"] == "finalization_failed"
    assert view.resolve_run_status(run(), NOW)["status"] == "completed"


def test_call_origin_does_not_assume_browser_for_unknown_livekit():
    assert view.classify_call_origin(run(initial_context={"participant_kind": 0})) == "browser"
    assert view.classify_call_origin(run(initial_context={"participant_kind": 3})) == "pstn"
    assert view.classify_call_origin(run(initial_context={})) == "unknown"
    assert view.classify_call_origin(run(mode="vobiz")) == "pstn"
    assert view.classify_call_origin(run(mode="simulated")) == "simulated"
    assert view.classify_call_origin(run(name="SIM-INBOUND-1")) == "simulated"
    assert view.classify_call_origin(run(is_simulation=True, mode="vobiz")) == "simulated"


# --------------------------------------------------------------------------- #
# Transcript persistence shape (LiveKit history) is exposed correctly
# --------------------------------------------------------------------------- #

HISTORY = {
    "livekit_history": {
        "items": [
            {"type": "agent_config_update", "instructions": "SECRET SYSTEM PROMPT"},
            {"type": "agent_handoff"},
            {"type": "message", "role": "assistant", "content": ["Namaste, how can I help?"]},
            {"type": "message", "role": "user", "content": ["నమస్కారం, I want a 2BHK flat"]},
            {"type": "message", "role": "system", "content": ["INTERNAL RULES"]},
            {"type": "message", "role": "assistant", "content": ["Sure.\nWhich area?"]},
        ]
    }
}


def test_livekit_history_becomes_speaker_separated_unicode_transcript():
    text = extract_transcript_from_logs(HISTORY)
    assert "SECRET SYSTEM PROMPT" not in text and "INTERNAL RULES" not in text
    lines = text.split("\n")
    assert lines[0] == "Agent: Namaste, how can I help?"
    assert lines[1] == "Caller: నమస్కారం, I want a 2BHK flat"
    assert lines[2] == "Agent: Sure. Which area?"  # embedded newline collapsed, still one turn
    turns = view.transcript_turns(text)
    assert [t["speaker"] for t in turns] == ["agent", "caller", "agent"]
    assert all("timestamp" not in t for t in turns)  # nothing invented


# --------------------------------------------------------------------------- #
# Analyzer: required acceptance
# --------------------------------------------------------------------------- #

PROPERTY = (
    "Agent: Namaste! Welcome. How can I help you today? We stock a wide range.\n"
    "Caller: Hello, I am looking for a 2BHK flat for my family.\n"
    "Agent: Wonderful. Which area, and what price range?\n"
    "Caller: Around Gachibowli, budget is 80 lakhs."
)


def test_2bhk_enquiry_is_not_classified_retail():
    intel = analyze_lead_from_transcript(PROPERTY, business_context={"industry": "Real Estate"})
    assert intel["customer_intent"] == "Property Purchase Enquiry"
    assert "Retail" not in intel["customer_intent"]
    assert intel["customer_requirements"]["bhk"] == "2BHK"
    assert intel["customer_requirements"]["preferred_location"] == "Gachibowli"
    assert intel["title"] == "2BHK Property Enquiry"
    assert intel["industry_mismatch"] is False


def test_agent_speech_and_substrings_do_not_drive_intent():
    # "tv", "ac", "stock", "price", "room" appear only inside other words or agent speech.
    text = (
        "Agent: We stock TVs, ACs and rooms for stay. What price do you want?\n"
        "Caller: I practice yoga back home and I am calling about my doctor visit consultation."
    )
    intel = analyze_lead_from_transcript(text)
    assert intel["customer_intent"] == "Doctor Consultation & Appointment"
    assert "Retail" not in intel["customer_intent"] and "Hotel" not in intel["customer_intent"]


def test_substring_ac_inside_words_never_triggers_retail():
    text = "Caller: Please come back and practice with me in the vacation period."
    intel = analyze_lead_from_transcript(text)
    assert "Retail" not in intel["customer_intent"]


def test_industry_mismatch_is_flagged_not_hidden():
    intel = analyze_lead_from_transcript(PROPERTY, business_context={"industry": "Retail"})
    assert intel["customer_intent"] == "Property Purchase Enquiry"
    assert intel["industry_mismatch"] is True
    assert "does not match the configured business" in intel["recommended_next_action"]


def test_empty_and_no_speech_calls_do_not_become_leads():
    none = analyze_lead_from_transcript("")
    assert none["analysis_status"] == "no_transcript"
    assert none["lead_classification"] == "Needs Review" and none["lead_score"] == 0
    assert none["transcript_evidence"] == ""  # no fabricated "verified caller" evidence
    agent_only = analyze_lead_from_transcript("Agent: Hello, welcome. How can I help you today?")
    assert agent_only["analysis_status"] == "no_caller_speech"
    assert agent_only["lead_classification"] == "Unqualified"
    assert agent_only["lead_score"] == 0
    assert agent_only["title"] == "No-Speech Call"
    assert agent_only["customer_requirements"] == {}
    assert agent_only["appointment"]["requested"] is False
    assert agent_only["timeline"] == "Not specified"  # agent's "today" is not a timeline


def test_thin_caller_speech_needs_review_not_cold_lead():
    intel = analyze_lead_from_transcript("Agent: Hello?\nCaller: Hello")
    assert intel["lead_classification"] == "Needs Review"
    assert intel["analysis_status"] == "needs_review"
    assert intel["lead_score"] == 0


MADHURAWADA = (
    "Agent: Which area are you looking in?\n"
    "Caller: I want a 3BHK in Madhapur side.\n"
    "Agent: Thanks, noted."
)


def test_ambiguous_place_stays_unconfirmed_not_silently_saved():
    intel = analyze_lead_from_transcript(MADHURAWADA)
    assert "preferred_location" not in intel["customer_requirements"]
    unconfirmed = intel["unconfirmed_information"][0]
    assert unconfirmed["field"] == "preferred_location" and unconfirmed["heard"] == "Madhapur"
    assert "Madhurawada" in unconfirmed["alternatives"]
    assert "not confirmed" in intel["summary"]
    assert any("not confirmed" in m for m in intel["missing_information"])


def test_place_is_confirmed_only_after_explicit_caller_confirmation():
    text = (
        "Caller: I want a flat in Madhurawada.\n"
        "Agent: Just to confirm, Madhurawada?\n"
        "Caller: Yes, correct."
    )
    intel = analyze_lead_from_transcript(text)
    assert intel["customer_requirements"]["preferred_location"] == "Madhurawada"
    assert not intel["unconfirmed_information"]


def test_never_invents_budget_location_or_appointment():
    intel = analyze_lead_from_transcript("Agent: Hi\nCaller: I just want some general information please.")
    assert intel["budget"] == "Not specified"
    assert "preferred_location" not in intel["customer_requirements"]
    assert intel["appointment"]["requested"] is False
    assert intel["appointment"]["status"] == "None"


def test_titles_are_evidence_based_and_identity_free():
    dental = analyze_lead_from_transcript(
        "Agent: Clinic.\nCaller: I have a toothache, can I book a dentist appointment tomorrow?"
    )
    assert dental["title"] == "Dental Appointment Request"
    cb = analyze_lead_from_transcript("Agent: Hello\nCaller: I am busy right now, please call back later.")
    assert cb["title"] == "Callback Requested"
    for intel in (dental, cb):
        assert "Valued Caller" not in intel["title"]


# Ported legacy analyzer expectations (those test modules import the full app, which
# cannot be collected in this environment) ---------------------------------------


def test_legacy_hot_lead_extraction():
    sample = (
        "Agent: Namaste! Welcome to Sri Krishna Realty. How can I help you today?\n"
        "Caller: Namaskaram! I am looking for a 3BHK flat in Gachibowli with a budget of 1.6 Crores.\n"
        "Agent: We have Skyline Heights in Gachibowli fitting your 1.6 Cr budget. Would you like to schedule a site visit this Saturday at 11 AM?\n"
        "Caller: Yes, please book the site visit for this Saturday at 11 AM. Thank you!"
    )
    intel = analyze_lead_from_transcript(sample)
    assert intel["customer_intent"] == "Site Visit & Property Booking"
    assert intel["customer_requirements"].get("bhk") == "3BHK"
    assert "1.6" in intel["budget"]
    assert intel["customer_requirements"].get("preferred_location") == "Gachibowli"
    assert intel["lead_score"] >= 75
    assert intel["lead_classification"] == "Hot"
    assert intel["appointment"]["requested"] is True
    assert intel["appointment"]["status"] == "Requested"
    assert len(intel["transcript_evidence"]) > 0


def test_legacy_dnc_and_human_escalation():
    dnc = analyze_lead_from_transcript(
        "Agent: Hello, I am calling from IronFit Fitness Club.\n"
        "Caller: Please stop calling and remove my number immediately. Do not call me again."
    )
    assert dnc["lead_classification"] == "Do Not Contact" and dnc["lead_score"] == 0
    assert "Do Not Call" in dnc["recommended_next_action"]
    human = analyze_lead_from_transcript(
        "Agent: Hello, welcome to customer service.\n"
        "Caller: I have a serious billing issue. Please connect to executive or speak to manager right now."
    )
    assert human["lead_classification"] == "Human Assistance Required"
    assert human["human_escalation"]["requested"] is True


def test_legacy_tiers_hot_warm_cold():
    hot = analyze_lead_from_transcript(
        "Agent: Welcome to Royal Palms Real Estate.\n"
        "Caller: Namaste! I need a 3BHK luxury apartment in Banjara Hills within 2.5 Crores budget. "
        "We are ready to move next month and want to schedule a visit this Saturday."
    )
    assert hot["lead_score"] >= 75 and hot["lead_classification"] == "Hot"
    assert "2.5" in hot["budget"] and hot["customer_requirements"]["bhk"] == "3BHK"
    assert "Banjara Hills" in hot["customer_requirements"]["preferred_location"]
    assert hot["appointment"]["requested"] is True and "Caller:" in hot["transcript_evidence"]
    warm = analyze_lead_from_transcript(
        "Agent: Welcome to Royal Palms.\n"
        "Caller: Hello, what is the starting price for a 2BHK flat in Miyapur? Just checking options, maybe next year."
    )
    assert 45 <= warm["lead_score"] < 75 and warm["lead_classification"] == "Warm"
    cold = analyze_lead_from_transcript(
        "Agent: Hello, this is Royal Palms.\n"
        "Caller: Who is this? I don't know why you are calling. Not interested."
    )
    assert cold["lead_score"] <= 25 and cold["lead_classification"] in ("Not Interested", "Cold")
    dnc = analyze_lead_from_transcript("Agent: Good afternoon.\nCaller: Stop calling me! Put me on your do not call list immediately.")
    assert dnc["lead_score"] == 0 and dnc["lead_classification"] == "Do Not Contact"


def test_legacy_industry_aware_actions():
    clinic = analyze_lead_from_transcript(
        "Agent: Welcome to Sanjeevani Clinic. How may I assist you today?\n"
        "Caller: Hello, I have had a severe toothache since yesterday. Can I see a dentist or doctor tomorrow afternoon?\n"
        "Agent: We have consultation slots available tomorrow between 3 PM and 6 PM. Shall I reserve 4 PM for you?\n"
        "Caller: Yes, please book 4 PM tomorrow for consultation.",
        business_context={"industry": "Healthcare", "name": "Dental Clinic"},
    )
    assert clinic["customer_intent"] == "Doctor Consultation & Appointment"
    assert clinic["industry"] == "Healthcare"
    assert "brochure" not in clinic["recommended_next_action"].lower()
    assert any(t in clinic["recommended_next_action"] for t in ["Medical staff", "Clinic staff", "patient"])
    gym = analyze_lead_from_transcript(
        "Agent: Hi, welcome to IronFit Fitness Club!\n"
        "Caller: I want to know about your annual membership and if I can try a workout tomorrow.\n"
        "Agent: Our annual plan is ₹17,999 and we offer a complimentary 1-day trial workout.\n"
        "Caller: Yes, tomorrow morning at 7 AM would be great.",
        business_context={"industry": "Fitness"},
    )
    assert gym["customer_intent"] == "Gym Membership & Trial Workout"
    assert gym["industry"] == "Fitness"


# --------------------------------------------------------------------------- #
# Analysis record: idempotency, override survival, evidence separation
# --------------------------------------------------------------------------- #


def _intel(text=PROPERTY):
    return analyze_lead_from_transcript(text, business_context={"industry": "Real Estate"})


def test_analysis_is_idempotent_for_same_transcript_and_version():
    intel = _intel()
    th = view.transcript_hash("t1")
    ann, changed = view.apply_ai_analysis({}, intel, transcript_hash_value=th, now=NOW)
    assert changed is True and ann["analysis"]["analyzer_version"] == ANALYZER_VERSION
    again, changed_again = view.apply_ai_analysis(ann, intel, transcript_hash_value=th, now=NOW)
    assert changed_again is False and again == ann


def test_owner_overrides_survive_reanalysis_and_ai_evidence_stays_separate():
    intel = _intel()
    ann, _ = view.apply_ai_analysis({}, intel, transcript_hash_value="a", now=NOW)
    original_ai_class = ann["analysis"]["ai"]["lead_classification"]
    ann = view.apply_owner_override(
        ann, {"lead_classification": "Cold", "title": "Owner title"}, actor={"user_id": 7, "role": "owner"}, now=NOW
    )
    assert ann["lead_intelligence"]["lead_classification"] == "Cold"
    assert ann["lead_intelligence"]["owner_overridden_fields"] == ["lead_classification", "title"]
    assert ann["analysis"]["ai"]["lead_classification"] == original_ai_class  # AI evidence untouched

    # New transcript => re-analysis runs, owner overrides still win.
    ann2, changed = view.apply_ai_analysis(ann, _intel("Caller: I need a villa in Kondapur, budget 2 crores."), transcript_hash_value="b", now=NOW)
    assert changed is True
    assert ann2["lead_intelligence"]["lead_classification"] == "Cold"
    assert ann2["lead_intelligence"]["title"] == "Owner title"
    assert ann2["analysis"]["owner_overrides"] == ann["analysis"]["owner_overrides"]
    assert len(ann2["analysis"]["owner_history"]) == 2
    assert ann2["analysis"]["previous_ai"], "previous AI output is preserved, not overwritten"


def test_owner_history_records_who_what_before_after_and_reset():
    ann, _ = view.apply_ai_analysis({}, _intel(), transcript_hash_value="a", now=NOW)
    before = ann["lead_intelligence"]["lead_classification"]
    assert before != "Cold"
    ann = view.apply_owner_override(ann, {"lead_classification": "Cold"}, actor={"user_id": 9, "role": "owner"}, now=NOW)
    entry = ann["analysis"]["owner_history"][-1]
    assert entry["by"] == {"user_id": 9, "role": "owner"}
    assert entry["field"] == "lead_classification" and entry["before"] == before and entry["after"] == "Cold"
    # A no-op edit (same value) is not recorded as a correction.
    noop = view.apply_owner_override(ann, {"lead_classification": "Cold"}, actor={"user_id": 9}, now=NOW)
    assert len(noop["analysis"]["owner_history"]) == len(ann["analysis"]["owner_history"])
    ann = view.apply_owner_override(ann, {}, actor={"user_id": 9}, reset_fields=["lead_classification"], now=NOW)
    assert ann["lead_intelligence"]["lead_classification"] == before
    assert ann["analysis"]["owner_history"][-1]["after"] == "reset_to_ai"


def test_first_owner_edit_on_legacy_run_snapshots_existing_ai_output():
    legacy = {"lead_intelligence": {"lead_classification": "Warm", "lead_score": 60, "appointment": {"status": "None"}}}
    ann = view.apply_owner_override(legacy, {"lead_classification": "Hot"}, actor={"user_id": 1}, now=NOW)
    assert ann["analysis"]["ai"]["lead_classification"] == "Warm"  # original evidence retained
    assert ann["lead_intelligence"]["lead_classification"] == "Hot"


def test_historical_lead_intelligence_is_preserved_when_reanalysed():
    legacy = {"lead_intelligence": {"lead_classification": "Warm", "customer_intent": "Old"}, "post_call_processed_at": "x"}
    ann, changed = view.apply_ai_analysis(legacy, _intel(), transcript_hash_value="a", now=NOW)
    assert changed and ann["analysis"]["previous_ai"][0]["ai"]["customer_intent"] == "Old"


def test_failed_analysis_is_recorded_without_losing_evidence():
    ann, _ = view.apply_ai_analysis({}, _intel(), transcript_hash_value="a", now=NOW)
    failed = view.apply_analysis_failure(ann, "post_call_failed:Boom", now=NOW)
    assert failed["analysis"]["status"] == "failed" and failed["analysis"]["attempts"] == 1
    assert failed["analysis"]["ai"] == ann["analysis"]["ai"]


# --------------------------------------------------------------------------- #
# Archive keeps financial evidence
# --------------------------------------------------------------------------- #

COST_337 = {
    "billing_status": "ESTIMATED",
    "call_duration_seconds": 225.0,
    "actual_cost": {
        "estimated": True,
        "total_inr": 8.2709,
        "ai_total_inr": 8.2709,
        "telephony_total_inr": 0.0,
        "components": [
            {"service": "realtime", "priced": False, "cost_inr": 0.0, "warning": "No llm pricing configured"},
            {"service": "realtime", "estimated": True, "cost_inr": 8.2709},
        ],
        "warnings": ["No llm pricing configured for provider=google model=gemini-3.1-flash-live-preview"],
    },
}


def test_archive_and_restore_never_touch_financial_evidence():
    r = run(cost_info=COST_337, usage_info={"call_duration_seconds": 225}, annotations={"x": 1})
    before_cost = json.dumps(r.cost_info, sort_keys=True)
    before_summary = view.run_cost_summary(r)
    ann, changed = view.apply_archive(r.annotations, actor={"user_id": 1}, reason="tidy", now=NOW)
    assert changed and view.is_archived(ann)
    again, changed2 = view.apply_archive(ann, actor={"user_id": 1}, now=NOW)
    assert changed2 is False
    r.annotations = ann
    assert json.dumps(r.cost_info, sort_keys=True) == before_cost
    assert view.run_cost_summary(r) == before_summary
    restored, ch = view.apply_restore(ann, actor={"user_id": 1}, now=NOW)
    assert ch and not view.is_archived(restored) and restored["x"] == 1
    assert [h["action"] for h in restored["archive"]["history"]] == ["archived", "restored"]


# --------------------------------------------------------------------------- #
# Cost labels are truthful
# --------------------------------------------------------------------------- #


def test_cost_summary_is_labelled_estimate_with_unpriced_and_no_pstn():
    r = run(initial_context={"participant_kind": 0}, cost_info=COST_337)
    s = view.run_cost_summary(r)
    assert s["status"] == "estimated" and s["estimated"] is True
    assert s["unpriced_components"] == 1
    assert s["telephony_applicable"] is False and s["pstn"] is False
    assert s["total_inr"] == 8.2709


def test_cost_summary_reconciled_only_when_verified_and_not_estimated():
    cost = {"billing_status": "VERIFIED", "actual_cost": {"estimated": False, "total_inr": 3.0, "components": []}}
    assert view.run_cost_summary(run(mode="vobiz", cost_info=cost))["status"] == "reconciled"
    cost["actual_cost"]["estimated"] = True
    assert view.run_cost_summary(run(mode="vobiz", cost_info=cost))["status"] == "estimated"
    assert view.run_cost_summary(run())["status"] == "not_costed"


# --------------------------------------------------------------------------- #
# Recording status honesty
# --------------------------------------------------------------------------- #


def test_recording_not_configured_is_displayed_honestly():
    rec = view.recording_status(run(), storage_configured=False)
    assert rec["status"] == "not_configured" and rec["playable"] is False


def test_recording_disabled_starting_failed_ready():
    assert view.recording_status(run(), storage_configured=True)["status"] == "disabled"
    assert view.recording_status(run(state="running", is_completed=False), storage_configured=True)["status"] == "starting"
    lk = lambda **k: run(gathered_context={"livekit_recording": {"recording_key": "SPX-VOICE-INBOUND/1-x.mp3", **k}})
    assert view.recording_status(lk(status="started"), storage_configured=True)["status"] == "failed"  # call ended, never finished
    assert view.recording_status(lk(status="not_started", error="boom"), storage_configured=True)["status"] == "failed"
    assert view.recording_status(
        lk(status="complete", file_results=[{"size": 0}]), storage_configured=True
    )["status"] == "failed"
    assert view.recording_status(lk(status="complete"), storage_configured=True)["status"] == "failed"  # no URL stored
    ready = run(
        recording_url="http://minio/SPX-VOICE-INBOUND/1-x.mp3",
        gathered_context={"livekit_recording": {"status": "complete", "recording_key": "SPX-VOICE-INBOUND/1-x.mp3", "file_results": [{"size": 1234}]}},
    )
    r = view.recording_status(ready, storage_configured=True)
    assert r["status"] == "ready" and r["playable"] is True


def test_no_fabricated_recording_for_runs_337_338_shape():
    # Runs #337/#338 have no recording state and no URL; storage is unconfigured here.
    r = run(logs=HISTORY)
    assert view.recording_status(r, storage_configured=False)["status"] == "not_configured"
    assert view.recording_status(r, storage_configured=False)["playable"] is False


# --------------------------------------------------------------------------- #
# Export safety
# --------------------------------------------------------------------------- #


ITEM = {
    "id": 5,
    "title": "=HYPERLINK(\"http://x\")",
    "date": "2026-11-16T10:00:00+00:00",
    "call_type": "outbound",
    "origin": "browser",
    "run_status": {"status": "completed"},
    "duration_seconds": 225.0,
    "contact": "+919800000000",
    "customer_name": "Valued Caller",
    "has_customer_name": False,
    "lead_classification": "Warm",
    "lead_score": 60,
    "outcome": "information_provided",
    "summary": "తెలుగు సారాంశం — 2BHK",
    "budget": "Not specified",
    "timeline": "Not specified",
    "customer_requirements": {"bhk": "2BHK"},
    "recommended_next_action": "+cmd",
    "appointment": {"status": "Requested"},
    "analysis_status": "complete",
    "recording": {"status": "not_configured"},
    "cost": {"status": "estimated", "total_inr": 8.27, "estimated": True},
    "owner_notes": "@note",
    "archived": False,
}


def test_csv_export_is_unicode_safe_and_formula_safe():
    data = export.build_csv([ITEM])
    assert data.startswith("﻿".encode("utf-8"))
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    header, row = rows[0], dict(zip(rows[0], rows[1]))
    assert header == export.CSV_COLUMNS
    assert row["summary"] == "తెలుగు సారాంశం — 2BHK"
    assert row["title"].startswith("'=") and row["recommended_next_action"].startswith("'+")
    assert row["owner_notes"].startswith("'@")
    assert row["customer_name"] == ""  # generic placeholder is not exported as a name
    assert row["recording_status"] == "not_configured" and row["cost_is_estimate"] == "True"


def test_json_and_zip_exports_include_transcripts_manifest_and_no_recordings():
    turns = {5: [{"index": 0, "speaker": "caller", "text": "నమస్కారం"}]}
    payload = json.loads(export.build_json([ITEM], transcripts=turns, filters={"search": True}))
    assert payload["conversations"][0]["transcript"][0]["text"] == "నమస్కారం"
    assert payload["truncated"] is False
    blob = export.build_zip([ITEM], turns, filters={})
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        assert {"manifest.json", "conversations/5.json", "conversations/5.txt"} <= names
        manifest = json.loads(z.read("manifest.json"))
        assert manifest["entries"][0]["recording_included"] is False
        assert manifest["entries"][0]["recording_status"] == "not_configured"
        assert "నమస్కారం" in z.read("conversations/5.txt").decode("utf-8")


def test_export_filename_is_safe():
    name = export.export_filename("spx conv/../evil\n", "c$v", now=NOW)
    assert name == "spx-conv-evil-20261116-120000.cv"
    assert "/" not in name and ".." not in name.replace("...", "")


def test_parse_transcript_turns_keeps_unknown_text_out_of_caller():
    turns = parse_transcript_turns("preamble line\nCaller: hi\ncontinued\nAgent: ok")
    assert turns[0]["speaker"] == "unknown"
    assert turns[1] == {"speaker": "caller", "text": "hi continued"}
