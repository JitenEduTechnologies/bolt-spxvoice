"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  FileText,
  Headphones,
  History,
  Loader2,
  RefreshCw,
  RotateCcw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  AnalysisBadge,
  ClassificationBadge,
  CostBadge,
  LifecycleBadge,
  OriginBadge,
  RecordingBadge,
  RunStatusBadge,
} from "@/components/conversations/badges";
import { MediaPreviewDialog } from "@/components/MediaPreviewDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  type ConversationDetail,
  downloadSingleExport,
  fetchConversationDetail,
  fetchRecordingAccess,
  formatDuration,
  type GetToken,
  reanalyze,
  updateLead,
} from "@/lib/conversationsApi";

const CLASSIFICATIONS = [
  "Hot",
  "Warm",
  "Cold",
  "Won",
  "Unqualified",
  "Needs Review",
  "Callback Requested",
  "Human Assistance Required",
  "Not Interested",
  "Do Not Contact",
];
const APPOINTMENT_STATUSES = ["Requested", "Confirmed", "Rescheduled", "Completed", "Cancelled"];

interface Props {
  runId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  getAccessToken: GetToken;
  isSuperuser: boolean;
  /** Bumped by the parent when a real-time event says this run changed. */
  refreshKey: number;
  onChanged: () => void;
}

function Section({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2.5">
      <Label className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-muted-foreground">
        {icon}
        {title}
      </Label>
      {children}
    </section>
  );
}

export function ConversationDrawer({ runId, open, onOpenChange, getAccessToken, isSuperuser, refreshKey, onChanged }: Props) {
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyRecording, setBusyRecording] = useState(false);
  const [classification, setClassification] = useState("");
  const [appointment, setAppointment] = useState("");
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const { openPreview, openPreviewUrl, dialog } = MediaPreviewDialog();

  const load = useCallback(
    async (silent: boolean) => {
      if (runId === null) return;
      if (!silent) setLoading(true);
      try {
        const d = await fetchConversationDetail(getAccessToken, runId);
        setDetail(d);
        setError(null);
        setClassification(d.lead_classification);
        setAppointment(d.appointment?.status && d.appointment.status !== "None" ? d.appointment.status : "");
        setTitle(d.title);
        setNotes(d.owner_notes || "");
      } catch (e) {
        setError(e instanceof Error ? e.message : "Conversation could not be loaded");
        if (!silent) setDetail(null);
      } finally {
        setLoading(false);
      }
    },
    [runId, getAccessToken],
  );

  useEffect(() => {
    if (open && runId !== null) void load(false);
  }, [open, runId, load]);

  // A real-time event about this run: refresh quietly without losing the owner's unsaved edits' context.
  useEffect(() => {
    if (open && runId !== null && refreshKey > 0) void load(true);
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!detail) return;
    const body: Parameters<typeof updateLead>[2] = {};
    if (classification && classification !== detail.lead_classification) body.lead_classification = classification;
    if (appointment && appointment !== (detail.appointment?.status ?? "")) body.appointment_status = appointment;
    if (title.trim() && title.trim() !== detail.title) body.title = title.trim();
    if (notes !== (detail.owner_notes || "")) body.owner_notes = notes;
    if (Object.keys(body).length === 0) {
      toast.info("No changes to save");
      return;
    }
    setSaving(true);
    try {
      await updateLead(getAccessToken, detail.id, body);
      toast.success("Correction saved. The original AI analysis is kept.");
      await load(true);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const resetField = async (field: string) => {
    if (!detail) return;
    try {
      await updateLead(getAccessToken, detail.id, { reset_fields: [field] });
      toast.success("Restored to the AI analysis");
      await load(true);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reset");
    }
  };

  const runAnalysis = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      const res = await reanalyze(getAccessToken, detail.id);
      toast.success(res.status === "unchanged" ? "Analysis is already up to date" : "Analysis updated");
      await load(true);
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Analysis failed");
    } finally {
      setSaving(false);
    }
  };

  const listen = async () => {
    if (!detail) return;
    setBusyRecording(true);
    try {
      const access = await fetchRecordingAccess(getAccessToken, detail.id, false);
      if (!access.playable) {
        toast.error(access.detail || "This recording is not available.");
        await load(true);
        return;
      }
      // Verified, short-lived link from the server; legacy storage still goes through the signed-URL route.
      if (access.url) openPreviewUrl(access.url, detail.id);
      else if (access.legacy_key) openPreview(access.legacy_key, null, detail.id);
      else toast.error("This recording could not be opened.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Recording could not be opened");
    } finally {
      setBusyRecording(false);
    }
  };

  const downloadRecording = async () => {
    if (!detail) return;
    setBusyRecording(true);
    try {
      const access = await fetchRecordingAccess(getAccessToken, detail.id, true);
      if (access.playable && access.url) window.location.assign(access.url);
      else toast.error(access.detail || "This recording is not available for download.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Download failed");
    } finally {
      setBusyRecording(false);
    }
  };

  const costHref = isSuperuser ? detail?.cost_links.superadmin_costs : detail?.cost_links.owner_costs;
  const overridden = new Set(detail?.owner_overridden_fields ?? []);

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full space-y-6 overflow-y-auto sm:max-w-2xl">
          {loading && !detail ? (
            <div className="space-y-3 pt-8">
              <Skeleton className="h-8 w-2/3" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-48 w-full" />
            </div>
          ) : error && !detail ? (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </div>
          ) : detail ? (
            <>
              <SheetHeader className="space-y-2 border-b pb-4">
                <SheetTitle className="text-xl font-bold">{detail.title}</SheetTitle>
                <SheetDescription className="text-xs">
                  Call #{detail.id} · {detail.date ? new Date(detail.date).toLocaleString() : "—"} · {detail.contact} ·{" "}
                  {detail.call_type}
                  {!detail.has_customer_name && " · caller name not captured"}
                </SheetDescription>
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <RunStatusBadge item={detail} />
                  <OriginBadge origin={detail.origin} />
                  <ClassificationBadge classification={detail.lead_classification} score={detail.lead_score} />
                  <Badge variant="secondary" className="font-mono text-[10px]">
                    {formatDuration(detail.duration_seconds)}
                  </Badge>
                  <AnalysisBadge status={detail.analysis_status} error={detail.analysis_error} />
                  <LifecycleBadge lifecycle={detail.lifecycle} />
                </div>
              </SheetHeader>

              {detail.lifecycle !== "active" && (
                <div className="flex items-start gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
                  <span>
                    This conversation is {detail.lifecycle === "trashed" ? "in Trash" : "archived"} and hidden from the active lists.
                    It is not deleted: its transcript, analysis and cost records are intact. Restore it from the list view.
                  </span>
                </div>
              )}

              {["failed", "not_started", "stale", "finalization_failed"].includes(detail.run_status.status) && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <span>
                    <strong>{detail.run_status.label}.</strong> {detail.run_status.detail}
                  </span>
                </div>
              )}

              {/* Transcript */}
              <Section icon={<Headphones className="h-3.5 w-3.5" />} title="Transcript">
                {detail.transcript_turns.length > 0 ? (
                  <div className="max-h-80 space-y-2 overflow-y-auto rounded-xl border bg-muted/30 p-3">
                    {detail.transcript_turns.map((turn) => {
                      const caller = turn.speaker === "caller";
                      return (
                        <div
                          key={turn.index}
                          className={`rounded-lg border p-2.5 text-xs leading-relaxed ${
                            caller ? "ml-6 border-primary/20 bg-primary/10" : "mr-6 bg-card"
                          }`}
                        >
                          <span className={`mb-0.5 block font-semibold ${caller ? "text-primary" : "text-indigo-600 dark:text-indigo-400"}`}>
                            {caller ? "Caller" : turn.speaker === "agent" ? "AI Agent" : "Unattributed"}
                            {turn.timestamp && (
                              <span className="ml-2 font-normal text-[10px] text-muted-foreground">
                                {new Date(turn.timestamp).toLocaleTimeString()}
                              </span>
                            )}
                          </span>
                          <span lang="und" className="whitespace-pre-wrap break-words">
                            {turn.text}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="rounded-xl border bg-muted/20 p-5 text-center text-xs italic text-muted-foreground">
                    {detail.transcript_status === "not_applicable"
                      ? "This call never connected, so there is no transcript."
                      : "No speech was transcribed for this call."}
                  </div>
                )}
                {detail.transcript_turns.length > 0 && !detail.timestamps_available && (
                  <p className="text-[11px] text-muted-foreground">Turn timestamps were not recorded for this call.</p>
                )}
              </Section>

              {/* Recording */}
              <Section icon={<Headphones className="h-3.5 w-3.5" />} title="Recording">
                <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3">
                  <RecordingBadge recording={detail.recording} />
                  {detail.recording.status === "ready" ? (
                    <>
                      <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" disabled={busyRecording} onClick={listen}>
                        {busyRecording ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Headphones className="h-3.5 w-3.5" />}
                        Listen
                      </Button>
                      <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" disabled={busyRecording} onClick={downloadRecording}>
                        <Download className="h-3.5 w-3.5" /> Download
                      </Button>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground">{detail.recording.detail}</span>
                  )}
                </div>
              </Section>

              {/* Insights */}
              <Section icon={<Sparkles className="h-3.5 w-3.5 text-amber-500" />} title="AI analysis">
                <div className="space-y-3 rounded-xl border bg-card p-3.5 text-xs">
                  <p className="leading-relaxed">{detail.summary}</p>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <span className="block text-[11px] text-muted-foreground">Outcome</span>
                      <span className="font-semibold">{detail.outcome ? detail.outcome.replace(/_/g, " ") : "—"}</span>
                    </div>
                    <div>
                      <span className="block text-[11px] text-muted-foreground">Appointment</span>
                      <span className="font-semibold">
                        {detail.appointment?.requested
                          ? `${detail.appointment.status ?? "Requested"} (not confirmed by a booking system)`
                          : "None requested"}
                      </span>
                    </div>
                    <div>
                      <span className="block text-[11px] text-muted-foreground">Budget</span>
                      <span className="font-semibold">{detail.budget === "Not specified" ? "Not stated" : detail.budget}</span>
                    </div>
                    <div>
                      <span className="block text-[11px] text-muted-foreground">Timeline</span>
                      <span className="font-semibold">{detail.timeline === "Not specified" ? "Not stated" : detail.timeline}</span>
                    </div>
                  </div>

                  {(detail.confirmed_information.length > 0 || detail.unconfirmed_information.length > 0) && (
                    <div className="space-y-1.5">
                      <span className="block text-[11px] text-muted-foreground">Customer requirements</span>
                      <div className="flex flex-wrap gap-1.5">
                        {detail.confirmed_information
                          .filter((c) => c.field !== "timeline" && c.field !== "budget")
                          .map((c) => (
                            <Badge key={c.field} variant="secondary" className="gap-1 text-[10px]">
                              <CheckCircle2 className="h-3 w-3 text-emerald-600" />
                              {c.field.replace(/_/g, " ")}: {c.value}
                            </Badge>
                          ))}
                        {detail.unconfirmed_information.map((u) => (
                          <Badge key={u.field} variant="outline" className="gap-1 border-amber-500/40 text-[10px] text-amber-700 dark:text-amber-400">
                            <AlertTriangle className="h-3 w-3" />
                            {u.field.replace(/_/g, " ")} unconfirmed: heard “{u.heard}”
                            {u.alternatives?.length ? ` (or ${u.alternatives.join(", ")})` : ""}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {detail.transcript_evidence && (
                    <p className="border-l-2 border-primary/40 pl-3 italic text-muted-foreground">{detail.transcript_evidence}</p>
                  )}
                  <div className="rounded-lg border border-blue-500/20 bg-blue-500/10 p-2.5">
                    <span className="font-semibold text-blue-800 dark:text-blue-400">Recommended next action: </span>
                    {detail.recommended_next_action}
                    {overridden.has("recommended_next_action") && (
                      <Badge variant="outline" className="ml-2 text-[9px]">
                        owner edited
                      </Badge>
                    )}
                  </div>
                  {detail.analysis_status === "failed" && detail.analysis_retryable === false && (
                    <p className="text-[11px] text-amber-700 dark:text-amber-400">
                      Automatic retries are exhausted. Run the analysis manually or correct the lead yourself.
                    </p>
                  )}
                  {detail.analysis_status === "legacy" && (
                    <p className="text-[11px] text-muted-foreground">
                      This call was analysed by an earlier version. Re-analyzing keeps that result in the history.
                    </p>
                  )}
                  {["pending", "failed", "legacy"].includes(detail.analysis_status) && detail.transcript_turns.length > 0 && (
                    <Button size="sm" variant="outline" className="h-8 gap-1.5 text-xs" disabled={saving} onClick={runAnalysis}>
                      <RefreshCw className="h-3.5 w-3.5" />{" "}
                      {detail.analysis_status === "legacy" ? "Re-analyze with latest rules" : "Run analysis now"}
                    </Button>
                  )}
                </div>
              </Section>

              {/* Cost */}
              <Section icon={<FileText className="h-3.5 w-3.5" />} title="Cost">
                <div className="flex flex-wrap items-center gap-2 rounded-xl border bg-card p-3 text-xs">
                  <CostBadge cost={detail.cost} />
                  {detail.cost.status !== "not_costed" && (
                    <span className="text-muted-foreground">
                      AI {typeof detail.cost.ai_inr === "number" ? `₹${detail.cost.ai_inr.toFixed(2)}` : "—"} · Telephony{" "}
                      {detail.cost.telephony_applicable
                        ? typeof detail.cost.telephony_inr === "number"
                          ? `₹${detail.cost.telephony_inr.toFixed(2)}`
                          : "—"
                        : "n/a (no phone line)"}
                    </span>
                  )}
                  {costHref && (
                    <Link href={costHref} className="ml-auto inline-flex items-center gap-1 font-medium text-primary hover:underline">
                      Open cost details <ExternalLink className="h-3 w-3" />
                    </Link>
                  )}
                </div>
              </Section>

              {/* Owner corrections */}
              <Section icon={<CheckCircle2 className="h-3.5 w-3.5" />} title="Your corrections">
                <div className="space-y-3 rounded-xl border bg-card p-3.5">
                  <div className="space-y-1">
                    <Label className="text-xs">Title</Label>
                    <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} className="h-8 text-xs" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">
                        Classification{" "}
                        {overridden.has("lead_classification") && (
                          <button className="ml-1 inline-flex items-center gap-0.5 text-[10px] text-primary underline" onClick={() => resetField("lead_classification")}>
                            <RotateCcw className="h-2.5 w-2.5" /> use AI value
                          </button>
                        )}
                      </Label>
                      <Select value={classification} onValueChange={setClassification}>
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {CLASSIFICATIONS.map((c) => (
                            <SelectItem key={c} value={c}>
                              {c}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Appointment status</Label>
                      <Select value={appointment} onValueChange={setAppointment}>
                        <SelectTrigger className="h-8 text-xs">
                          <SelectValue placeholder="None" />
                        </SelectTrigger>
                        <SelectContent>
                          {APPOINTMENT_STATUSES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {s}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">Private notes</Label>
                    <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} className="text-xs" placeholder="Follow-up notes, staff assignment…" />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" className="gap-1.5 text-xs" disabled={saving} onClick={save}>
                      {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                      Save corrections
                    </Button>
                    <Button size="sm" variant="outline" className="gap-1.5 text-xs" onClick={() => downloadSingleExport(getAccessToken, detail.id, "txt").catch((e) => toast.error(String(e.message ?? e)))}>
                      <Download className="h-3.5 w-3.5" /> Transcript (.txt)
                    </Button>
                    <Button size="sm" variant="outline" className="gap-1.5 text-xs" onClick={() => downloadSingleExport(getAccessToken, detail.id, "json").catch((e) => toast.error(String(e.message ?? e)))}>
                      <Download className="h-3.5 w-3.5" /> JSON
                    </Button>
                  </div>
                  {detail.analysis.owner_history.length > 0 && (
                    <details className="text-[11px] text-muted-foreground">
                      <summary className="flex cursor-pointer items-center gap-1 font-medium">
                        <History className="h-3 w-3" /> Correction history ({detail.analysis.owner_history.length})
                      </summary>
                      <ul className="mt-1.5 space-y-1">
                        {[...detail.analysis.owner_history].reverse().map((h, i) => (
                          <li key={`${h.at}-${i}`}>
                            {new Date(h.at).toLocaleString()} · {h.field}: {String(h.before ?? "—")} → {String(h.after ?? "—")}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </div>
              </Section>
            </>
          ) : null}
        </SheetContent>
      </Sheet>
      {dialog}
    </>
  );
}
