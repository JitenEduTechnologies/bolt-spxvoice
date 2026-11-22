"use client";

import { Badge } from "@/components/ui/badge";
import type { ConversationItem, RecordingState } from "@/lib/conversationsApi";

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30",
  in_progress: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30",
  starting: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30",
  finalizing: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30",
  failed: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30",
  finalization_failed: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30",
  not_started: "bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-400/40",
  stale: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30",
  unknown: "bg-slate-500/10 text-slate-600 border-slate-400/40",
};

export function RunStatusBadge({ item }: { item: Pick<ConversationItem, "run_status"> }) {
  const { status, label, detail } = item.run_status;
  return (
    <Badge variant="outline" title={detail || label} className={`text-[10px] font-medium ${STATUS_STYLE[status] ?? STATUS_STYLE.unknown}`}>
      {label}
    </Badge>
  );
}

const ORIGIN_LABEL: Record<string, { label: string; cls: string; hint: string }> = {
  pstn: { label: "Phone call", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30", hint: "Carrier (PSTN) call" },
  browser: { label: "Browser call", cls: "bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border-indigo-500/30", hint: "Real AI conversation without a phone line" },
  simulated: { label: "Simulation", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30", hint: "Scripted or test run" },
  unknown: { label: "Source unverified", cls: "bg-slate-500/10 text-slate-600 border-slate-400/40", hint: "Origin could not be verified" },
};

export function OriginBadge({ origin }: { origin: ConversationItem["origin"] }) {
  const o = ORIGIN_LABEL[origin] ?? ORIGIN_LABEL.unknown;
  return (
    <Badge variant="outline" title={o.hint} className={`text-[10px] font-medium ${o.cls}`}>
      {o.label}
    </Badge>
  );
}

const RECORDING_LABEL: Record<RecordingState, { label: string; cls: string }> = {
  ready: { label: "Recording ready", cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30" },
  starting: { label: "Recording in progress", cls: "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/30" },
  failed: { label: "Recording failed", cls: "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/30" },
  disabled: { label: "Recording off", cls: "bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-400/40" },
  not_configured: { label: "Recording not set up", cls: "bg-slate-500/10 text-slate-600 dark:text-slate-400 border-slate-400/40" },
  unavailable: { label: "Recording unavailable", cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30" },
};

export function RecordingBadge({ recording }: { recording: ConversationItem["recording"] }) {
  const r = RECORDING_LABEL[recording.status] ?? RECORDING_LABEL.unavailable;
  return (
    <Badge variant="outline" title={recording.detail || r.label} className={`text-[10px] font-medium ${r.cls}`}>
      {r.label}
    </Badge>
  );
}

export function CostBadge({ cost }: { cost: ConversationItem["cost"] }) {
  if (cost.status === "not_costed") {
    return (
      <Badge variant="outline" className="text-[10px] bg-slate-500/10 text-slate-600 border-slate-400/40">
        Not costed
      </Badge>
    );
  }
  const reconciled = cost.status === "reconciled";
  const amount = typeof cost.total_inr === "number" ? `₹${cost.total_inr.toFixed(2)}` : "—";
  return (
    <Badge
      variant="outline"
      title={
        reconciled
          ? "Reconciled with the provider's records"
          : "Estimate: not reconciled with a provider invoice or CDR"
      }
      className={`text-[10px] font-medium ${
        reconciled
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30"
          : "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30"
      }`}
    >
      {amount} {reconciled ? "reconciled" : "estimate"}
    </Badge>
  );
}

export function ClassificationBadge({ classification, score }: { classification: string; score: number }) {
  let cls = "bg-slate-500/10 text-slate-700 dark:text-slate-400 border-slate-500/20";
  if (classification === "Hot") cls = "bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20";
  else if (classification === "Warm") cls = "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20";
  else if (classification === "Needs Review") cls = "bg-violet-500/10 text-violet-700 dark:text-violet-400 border-violet-500/20";
  else if (classification === "Cold") cls = "bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-500/20";
  const showScore = !["Unclassified", "Unqualified", "Needs Review"].includes(classification);
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      <span>{classification}</span>
      {showScore && <span className="opacity-75 font-mono">({score})</span>}
    </span>
  );
}

export function AnalysisBadge({ status, error }: { status: string; error?: string | null }) {
  if (status === "failed") {
    return (
      <Badge variant="outline" title={error || "Analysis failed"} className="text-[10px] bg-rose-500/10 text-rose-700 border-rose-500/30">
        Analysis failed
      </Badge>
    );
  }
  if (status === "legacy") {
    return (
      <Badge variant="outline" title="Analysed by an earlier version; re-analyze for the latest rules" className="text-[10px] bg-violet-500/10 text-violet-700 dark:text-violet-400 border-violet-500/30">
        Earlier analysis
      </Badge>
    );
  }
  if (status === "pending") {
    return (
      <Badge variant="outline" className="text-[10px] bg-slate-500/10 text-slate-600 border-slate-400/40">
        Analysis pending
      </Badge>
    );
  }
  return null;
}

export function LifecycleBadge({ lifecycle }: { lifecycle?: "active" | "archived" | "trashed" }) {
  if (!lifecycle || lifecycle === "active") return null;
  return lifecycle === "trashed" ? (
    <Badge variant="outline" title="In Trash: hidden from active lists, not deleted; restore any time" className="text-[10px] border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400">
      In Trash
    </Badge>
  ) : (
    <Badge variant="outline" title="Archived: hidden from active lists, not deleted" className="text-[10px]">
      Archived
    </Badge>
  );
}
