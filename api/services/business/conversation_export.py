"""Safe conversation exports (CSV / JSON / ZIP) using only the standard library.

* UTF-8 with BOM for CSV so Excel renders Telugu / Hindi correctly.
* Spreadsheet formula injection is neutralised (cells starting with = + - @).
* Filenames are generated server-side from a fixed alphabet.
* Recordings are never embedded; the manifest reports each recording's status.
* PDF and XLSX are intentionally NOT produced: no PDF / spreadsheet library is a
  project dependency, and hand-rolled output would not be safe for Unicode.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Sequence

EXPORT_FORMATS = ("csv", "json", "zip")
CSV_COLUMNS = [
    "run_id",
    "title",
    "date",
    "direction",
    "source",
    "status",
    "duration_seconds",
    "contact",
    "customer_name",
    "lead_classification",
    "lead_score",
    "outcome",
    "summary",
    "budget",
    "timeline",
    "customer_requirements",
    "unconfirmed_information",
    "recommended_next_action",
    "appointment_status",
    "analysis_status",
    "recording_status",
    "cost_status",
    "cost_total_inr",
    "cost_is_estimate",
    "owner_notes",
    "archived",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    """Render a value for CSV and neutralise spreadsheet formula injection."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def export_filename(prefix: str, extension: str, *, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]+", "-", prefix).strip("-") or "export"
    safe_ext = re.sub(r"[^a-z0-9]+", "", extension.lower()) or "bin"
    return f"{safe_prefix}-{stamp}.{safe_ext}"


def item_to_row(item: Dict[str, Any]) -> List[str]:
    cost = item.get("cost") or {}
    recording = item.get("recording") or {}
    appointment = item.get("appointment") or {}
    values = {
        "run_id": item.get("id"),
        "title": item.get("title"),
        "date": item.get("date"),
        "direction": item.get("call_type"),
        "source": item.get("origin"),
        "status": (item.get("run_status") or {}).get("status"),
        "duration_seconds": round(float(item.get("duration_seconds") or 0), 1),
        "contact": item.get("contact"),
        "customer_name": item.get("customer_name") if item.get("has_customer_name") else "",
        "lead_classification": item.get("lead_classification"),
        "lead_score": item.get("lead_score"),
        "outcome": item.get("outcome"),
        "summary": item.get("summary"),
        "budget": item.get("budget"),
        "timeline": item.get("timeline"),
        "customer_requirements": item.get("customer_requirements"),
        "unconfirmed_information": item.get("unconfirmed_information") or "",
        "recommended_next_action": item.get("recommended_next_action"),
        "appointment_status": appointment.get("status"),
        "analysis_status": item.get("analysis_status"),
        "recording_status": recording.get("status"),
        "cost_status": cost.get("status"),
        "cost_total_inr": cost.get("total_inr"),
        "cost_is_estimate": cost.get("estimated"),
        "owner_notes": item.get("owner_notes"),
        "archived": item.get("archived"),
    }
    return [safe_cell(values[col]) for col in CSV_COLUMNS]


def build_csv(items: Iterable[Dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in items:
        writer.writerow(item_to_row(item))
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def build_json(
    items: Sequence[Dict[str, Any]],
    *,
    transcripts: Dict[int, List[Dict[str, Any]]] | None = None,
    filters: Dict[str, Any] | None = None,
    truncated: bool = False,
    now: datetime | None = None,
) -> bytes:
    records = []
    for item in items:
        record = dict(item)
        if transcripts is not None:
            record["transcript"] = transcripts.get(item["id"], [])
        records.append(record)
    payload = {
        "schema": "spx-voice.conversations.v1",
        "exported_at": (now or datetime.now(UTC)).isoformat(),
        "count": len(records),
        "truncated": truncated,
        "filters": filters or {},
        "note": "Recordings are not embedded; see each record's recording.status.",
        "conversations": records,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")


def transcript_as_text(item: Dict[str, Any], turns: Sequence[Dict[str, Any]]) -> str:
    lines = [
        f"Conversation #{item.get('id')} — {item.get('title')}",
        f"Date: {item.get('date')}",
        f"Direction: {item.get('call_type')}  Source: {item.get('origin')}",
        f"Duration: {round(float(item.get('duration_seconds') or 0), 1)} s",
        f"Classification: {item.get('lead_classification')}  Outcome: {item.get('outcome')}",
        f"Summary: {item.get('summary')}",
        "",
        "Transcript (no timestamps were recorded):",
    ]
    if not turns:
        lines.append("(no transcript available)")
    for turn in turns:
        speaker = {"caller": "Caller", "agent": "AI Agent"}.get(turn.get("speaker"), "Unknown")
        lines.append(f"{speaker}: {turn.get('text')}")
    return "\n".join(lines) + "\n"


def build_zip(
    items: Sequence[Dict[str, Any]],
    transcripts: Dict[int, List[Dict[str, Any]]],
    *,
    filters: Dict[str, Any] | None = None,
    now: datetime | None = None,
    recordings: Dict[int, bytes] | None = None,
    recording_notes: Dict[int, str] | None = None,
) -> bytes:
    """ZIP of per-conversation JSON + text transcripts plus a manifest.

    Recordings are *not* included; the manifest lists each recording's status so
    nothing is silently missing.
    """
    buffer = io.BytesIO()
    manifest: List[Dict[str, Any]] = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            run_id = int(item["id"])
            turns = transcripts.get(run_id, [])
            base = f"conversations/{run_id}"
            record = dict(item)
            record["transcript"] = turns
            archive.writestr(f"{base}.json", json.dumps(record, ensure_ascii=False, indent=2))
            archive.writestr(f"{base}.txt", transcript_as_text(item, turns))
            files = [f"{base}.json", f"{base}.txt"]
            audio = (recordings or {}).get(run_id)
            if audio:
                # Name is built only from the integer run id: no user-controlled path parts.
                archive.writestr(f"recordings/{run_id}.mp3", audio)
                files.append(f"recordings/{run_id}.mp3")
            manifest.append(
                {
                    "run_id": run_id,
                    "files": files,
                    "transcript_turns": len(turns),
                    "recording_status": (item.get("recording") or {}).get("status"),
                    "recording_included": bool(audio),
                    "recording_note": (recording_notes or {}).get(run_id),
                }
            )
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema": "spx-voice.conversations.zip.v1",
                    "exported_at": (now or datetime.now(UTC)).isoformat(),
                    "count": len(manifest),
                    "filters": filters or {},
                    "recordings": (
                        "included only where explicitly requested and verified; see each entry"
                        if recordings
                        else "not included; request include_recordings=true or download individually"
                    ),
                    "entries": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return buffer.getvalue()
