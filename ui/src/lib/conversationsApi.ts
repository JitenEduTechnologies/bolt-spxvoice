/**
 * Typed helpers for the owner-facing Conversations / Leads endpoints (CP-02).
 *
 * All lists are server-side paginated; nothing here loads full transcripts for
 * list rows. Exports are fetched with the user's token and saved as blobs (a plain
 * link cannot carry the Authorization header).
 */

export type RecordingState = "disabled" | "not_configured" | "starting" | "failed" | "ready" | "unavailable";
export type RunStatus =
  | "completed"
  | "failed"
  | "not_started"
  | "starting"
  | "in_progress"
  | "stale"
  | "finalizing"
  | "finalization_failed"
  | "unknown";

export interface ConversationCost {
  status: "not_costed" | "estimated" | "reconciled" | "reconciliation_failed" | "unverified";
  total_inr: number | null;
  ai_inr: number | null;
  telephony_inr: number | null;
  estimated: boolean | null;
  telephony_applicable?: boolean;
  unpriced_components?: number;
  warnings: string[];
}

export interface UnconfirmedInfo {
  field: string;
  heard: string;
  alternatives?: string[];
  reason?: string;
}

export interface ConversationItem {
  id: number;
  name: string;
  title: string;
  contact: string;
  customer_name: string;
  has_customer_name: boolean;
  call_type: string;
  origin: "simulated" | "browser" | "pstn" | "unknown";
  is_test: boolean;
  call_source: string;
  mode: string;
  state: string;
  run_status: { status: RunStatus; label: string; detail: string };
  date: string | null;
  duration_seconds: number;
  workflow_id: number;
  agent_name: string;
  voice_engine: string;
  telephony_provider: string;
  summary: string;
  outcome: string | null;
  analysis_status: string;
  analysis_error: string | null;
  customer_requirements: Record<string, unknown>;
  confirmed_information: { field: string; value: string }[];
  unconfirmed_information: UnconfirmedInfo[];
  budget: string;
  timeline: string;
  objections: string[];
  buying_signals: string[];
  lead_score: number;
  lead_classification: string;
  transcript_evidence: string;
  appointment: { requested?: boolean; type?: string; preferred_slot?: string; status?: string };
  human_escalation: { requested?: boolean };
  callback: { requested?: boolean };
  recommended_next_action: string;
  owner_overridden_fields: string[];
  owner_notes: string;
  recording: { status: RecordingState; detail: string; playable: boolean };
  archived: boolean;
  archived_at: string | null;
  trashed: boolean;
  trashed_at: string | null;
  lifecycle: "active" | "archived" | "trashed";
  contact_masked: boolean;
  cost: ConversationCost;
}

export interface ConversationListResponse {
  items: ConversationItem[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
  sort: string;
  order: "asc" | "desc";
  facets?: { classification: Record<string, number> };
}

export interface TranscriptTurn {
  index: number;
  speaker: "caller" | "agent" | "unknown";
  text: string;
  /** ISO time, present only when the call recorded one (never invented). */
  timestamp: string | null;
}

export interface OwnerHistoryEntry {
  at: string;
  by: { user_id?: number; role?: string };
  field: string;
  before: unknown;
  after: unknown;
}

export interface ConversationDetail extends ConversationItem {
  transcript: string;
  transcript_turns: TranscriptTurn[];
  transcript_status: "available" | "empty" | "not_applicable";
  timestamps_available: boolean;
  analysis: {
    status: string;
    analyzer_version?: string | null;
    analyzed_at?: string | null;
    error?: string | null;
    ai: Record<string, unknown>;
    owner_overrides: Record<string, unknown>;
    owner_history: OwnerHistoryEntry[];
  };
  recording: { status: RecordingState; detail: string; playable: boolean };
  analysis_retryable?: boolean | null;
  cost_links: { owner_costs: string; superadmin_costs: string };
}

export interface ConversationFilters {
  search: string;
  classification: string; // "all" or a classification
  call_type: string; // "all" | inbound | outbound
  source: string; // all | real | pstn | browser | simulated | test
  status: string; // all | completed | failed | not_started | in_progress | stale
  archived: "active" | "archived" | "trashed" | "all";
  date_from: string;
  date_to: string;
}

export const DEFAULT_FILTERS: ConversationFilters = {
  search: "",
  classification: "all",
  call_type: "all",
  source: "all",
  status: "all",
  archived: "active",
  date_from: "",
  date_to: "",
};

export type GetToken = () => Promise<string | null | undefined>;

export async function authHeaders(getAccessToken: GetToken): Promise<Record<string, string>> {
  let token: string | null | undefined = null;
  try {
    token = await getAccessToken();
  } catch {
    token = null;
  }
  if (!token) {
    try {
      const res = await fetch("/api/auth/oss");
      if (res.ok) token = (await res.json()).token;
    } catch {
      /* unauthenticated request will be rejected by the API */
    }
  }
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function filtersToParams(filters: ConversationFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.search.trim().length >= 2) params.set("search", filters.search.trim());
  if (filters.classification !== "all") params.set("classification", filters.classification);
  if (filters.call_type !== "all") params.set("call_type", filters.call_type);
  if (filters.source !== "all") params.set("source", filters.source);
  if (filters.status !== "all") params.set("status", filters.status);
  if (filters.archived !== "active") params.set("archived", filters.archived);
  if (filters.date_from) params.set("date_from", filters.date_from);
  if (filters.date_to) params.set("date_to", filters.date_to);
  return params;
}

/** Filter body for "select all matching" (classification is part of the scope). */
export function filtersToBody(filters: ConversationFilters) {
  return {
    search: filters.search.trim().length >= 2 ? filters.search.trim() : null,
    classification: filters.classification === "all" ? null : filters.classification,
    call_type: filters.call_type === "all" ? null : filters.call_type,
    source: filters.source,
    status: filters.status,
    archived: filters.archived,
    date_from: filters.date_from || null,
    date_to: filters.date_to || null,
  };
}

async function errorText(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    /* not JSON */
  }
  return fallback;
}

export async function fetchConversations(
  getAccessToken: GetToken,
  filters: ConversationFilters,
  opts: { limit: number; offset: number; sort: string; order: string },
  signal?: AbortSignal,
): Promise<ConversationListResponse> {
  const params = filtersToParams(filters);
  params.set("limit", String(opts.limit));
  params.set("offset", String(opts.offset));
  params.set("sort", opts.sort);
  params.set("order", opts.order);
  const res = await fetch(`/api/v1/business/conversations?${params.toString()}`, {
    headers: await authHeaders(getAccessToken),
    signal,
  });
  if (!res.ok) throw new Error(await errorText(res, `Failed to load conversations (${res.status})`));
  return res.json();
}

export async function fetchConversationDetail(
  getAccessToken: GetToken,
  runId: number | string,
): Promise<ConversationDetail> {
  const res = await fetch(`/api/v1/business/conversations/${runId}`, {
    headers: await authHeaders(getAccessToken),
  });
  if (!res.ok) throw new Error(await errorText(res, `Conversation not available (${res.status})`));
  return res.json();
}

export interface RecordingAccess {
  run_id: number;
  status: RecordingState;
  detail: string;
  playable: boolean;
  url?: string | null;
  legacy_key?: string | null;
  expires_in?: number;
}

export async function fetchRecordingAccess(
  getAccessToken: GetToken,
  runId: number,
  download = false,
): Promise<RecordingAccess> {
  const res = await fetch(`/api/v1/business/conversations/${runId}/recording?download=${download}`, {
    headers: await authHeaders(getAccessToken),
  });
  if (!res.ok) throw new Error(await errorText(res, "Recording is not available"));
  return res.json();
}

export type BulkSelection =
  | { ids: number[] }
  | { filters: ReturnType<typeof filtersToBody>; expected_count: number };

export type BulkMode = "archive" | "trash" | "restore";

/** Reversible bulk Archive / Move to Trash / Restore. Nothing is permanently deleted. */
export async function bulkLifecycle(
  getAccessToken: GetToken,
  selection: BulkSelection,
  mode: BulkMode,
  reason?: string,
): Promise<{ status: string; selected: number; changed: number; skipped: number }> {
  const res = await fetch(`/api/v1/business/conversations/bulk/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders(getAccessToken)) },
    body: JSON.stringify({ ...selection, reason: reason || null }),
  });
  if (!res.ok) throw new Error(await errorText(res, `Bulk action failed (${res.status})`));
  return res.json();
}

export interface BulkAnalyzeResult {
  status: "dry_run" | "analyzed";
  changed?: number;
  selected: number;
  eligible: number;
  skipped: { no_transcript: number; up_to_date: number };
  skipped_other_tenant: number;
  max_per_request: number;
  examples: {
    run_id: number;
    before: { classification: string; intent?: string | null };
    after: { classification: string; title: string };
  }[];
}

/** Re-analyze with the latest rules. `dryRun` previews exactly what would change and writes nothing. */
export async function bulkAnalyze(
  getAccessToken: GetToken,
  selection: BulkSelection,
  dryRun: boolean,
): Promise<BulkAnalyzeResult> {
  const res = await fetch(`/api/v1/business/conversations/bulk/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders(getAccessToken)) },
    body: JSON.stringify({ ...selection, dry_run: dryRun }),
  });
  if (!res.ok) throw new Error(await errorText(res, `Re-analysis failed (${res.status})`));
  return res.json();
}

export interface UpdateLeadBody {
  lead_classification?: string;
  lead_score?: number;
  appointment_status?: string;
  owner_notes?: string;
  title?: string;
  recommended_next_action?: string;
  reset_fields?: string[];
}

export async function updateLead(
  getAccessToken: GetToken,
  runId: number,
  body: UpdateLeadBody,
): Promise<{ owner_history: OwnerHistoryEntry[]; lead_intelligence: Record<string, unknown> }> {
  const res = await fetch(`/api/v1/business/conversations/${runId}/lead`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(await authHeaders(getAccessToken)) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorText(res, `Could not save changes (${res.status})`));
  return res.json();
}

export async function reanalyze(getAccessToken: GetToken, runId: number): Promise<{ status: string }> {
  const res = await fetch(`/api/v1/business/conversations/${runId}/analyze`, {
    method: "POST",
    headers: await authHeaders(getAccessToken),
  });
  if (!res.ok) throw new Error(await errorText(res, `Analysis failed (${res.status})`));
  return res.json();
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function filenameFrom(res: Response, fallback: string): string {
  const match = /filename="?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") || "");
  return match?.[1] || fallback;
}

export async function downloadExport(
  getAccessToken: GetToken,
  format: "csv" | "json" | "zip",
  scope: { ids?: number[]; filters?: ConversationFilters; includeTranscripts?: boolean; includeRecordings?: boolean },
): Promise<number> {
  const params = scope.ids?.length
    ? new URLSearchParams({ ids: scope.ids.join(",") })
    : filtersToParams(scope.filters ?? DEFAULT_FILTERS);
  params.set("format", format);
  if (scope.includeTranscripts) params.set("include_transcripts", "true");
  if (scope.includeRecordings) params.set("include_recordings", "true");
  const res = await fetch(`/api/v1/business/conversations/export?${params.toString()}`, {
    headers: await authHeaders(getAccessToken),
  });
  if (!res.ok) throw new Error(await errorText(res, `Export failed (${res.status})`));
  const blob = await res.blob();
  saveBlob(blob, filenameFrom(res, `spx-conversations.${format}`));
  return Number(res.headers.get("X-Export-Count") || "0");
}

export async function downloadSingleExport(
  getAccessToken: GetToken,
  runId: number,
  format: "json" | "txt" | "csv",
): Promise<void> {
  const res = await fetch(`/api/v1/business/conversations/${runId}/export?format=${format}`, {
    headers: await authHeaders(getAccessToken),
  });
  if (!res.ok) throw new Error(await errorText(res, `Export failed (${res.status})`));
  saveBlob(await res.blob(), filenameFrom(res, `conversation-${runId}.${format}`));
}

export function formatDuration(seconds: number): string {
  if (!seconds || seconds <= 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}
