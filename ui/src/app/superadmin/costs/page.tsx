"use client";

import {
  AlertTriangle,
  ArrowRight,
  ExternalLink,
  Info,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { OriginBadge } from "@/components/conversations/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth, type LocalUser } from "@/lib/auth";
import { costApi, type CostSummary } from "@/lib/costApi";
import { useRealtimeDashboard } from "@/hooks/useRealtimeDashboard";

interface WorkflowRun {
  id: number;
  workflow_id: number;
  workflow_name?: string | null;
  organization_id?: number | null;
  organization_name?: string | null;
  mode: string;
  is_completed: boolean;
  usage_info?: Record<string, unknown> | null;
  cost_info?: Record<string, unknown> | null;
  created_at: string;
  call_origin?: "simulated" | "browser" | "pstn" | "unknown" | null;
  cost_summary?: {
    status: "not_costed" | "estimated" | "reconciled" | "reconciliation_failed" | "unverified";
    estimated?: boolean | null;
    unpriced_components?: number;
    telephony_applicable?: boolean;
  } | null;
  duration_seconds?: number | null;
  conversation_state?: "active" | "archived" | "trashed" | null;
}

interface WorkflowRunsResponse {
  workflow_runs: WorkflowRun[];
  total_count: number;
  page: number;
  limit: number;
  total_pages: number;
}

interface ActualCostComponent {
  service?: string;
  provider?: string;
  model?: string;
  label?: string;
  currency?: string;
  cost_usd?: number;
  cost_inr?: number;
  estimated?: boolean;
  priced?: boolean;
  usage?: Record<string, unknown>;
  pricing?: Record<string, unknown>;
  warning?: string;
  note?: string;
  source_url?: string;
}

interface ActualCostInfo {
  total_usd?: number;
  ai_total_usd?: number;
  telephony_total_usd?: number;
  total_inr?: number;
  ai_total_inr?: number;
  telephony_total_inr?: number;
  estimated?: boolean;
  components?: ActualCostComponent[];
  warnings?: string[];
  exchange_rates?: Record<string, unknown>[];
  pricing_sources?: Record<string, unknown>[];
}

const PAGE_SIZE = 100;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatUsd(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value < 1 ? 4 : 2,
    maximumFractionDigits: value < 1 ? 6 : 2,
  }).format(value);
}

function formatInr(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: value < 1 ? 4 : 2,
    maximumFractionDigits: value < 1 ? 4 : 2,
  }).format(value);
}

function formatInrPrimary(inr: number | null | undefined, usd?: number | null | undefined): string {
  const inrLabel = formatInr(inr);
  if (inrLabel !== "-") return inrLabel;
  return formatUsd(usd);
}

function formatDate(value: string): string {
  return new Date(value).toLocaleString("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function getActualCost(costInfo?: Record<string, unknown> | null): ActualCostInfo | null {
  if (!costInfo) return null;
  if (isRecord(costInfo.actual_cost)) {
    return normalizeActualCost(costInfo.actual_cost as ActualCostInfo);
  }
  const legacyTotal = numberValue(costInfo.total_cost_usd);
  return legacyTotal === null
    ? null
    : { total_usd: legacyTotal, ai_total_usd: legacyTotal, components: [] };
}

function normalizeActualCost(actualCost: ActualCostInfo): ActualCostInfo {
  const components = Array.isArray(actualCost.components) ? actualCost.components : [];
  if (components.length === 0) return actualCost;

  const sum = (
    predicate: (component: ActualCostComponent) => boolean,
    field: "cost_inr" | "cost_usd",
  ) => components.reduce((total, component) => {
    if (!predicate(component)) return total;
    return total + (numberValue(component[field]) ?? 0);
  }, 0);
  const isTelephony = (component: ActualCostComponent) => component.service === "telephony";
  const nonTelephony = (component: ActualCostComponent) => !isTelephony(component);

  return {
    ...actualCost,
    total_inr: numberValue(actualCost.total_inr) ?? sum(() => true, "cost_inr"),
    ai_total_inr: numberValue(actualCost.ai_total_inr) ?? sum(nonTelephony, "cost_inr"),
    telephony_total_inr: numberValue(actualCost.telephony_total_inr) ?? sum(isTelephony, "cost_inr"),
    total_usd: numberValue(actualCost.total_usd) ?? sum(() => true, "cost_usd"),
    ai_total_usd: numberValue(actualCost.ai_total_usd) ?? sum(nonTelephony, "cost_usd"),
    telephony_total_usd: numberValue(actualCost.telephony_total_usd) ?? sum(isTelephony, "cost_usd"),
  };
}

function getDurationSeconds(run: WorkflowRun): number | null {
  if (typeof run.duration_seconds === "number" && run.duration_seconds > 0) return run.duration_seconds;
  const costDuration = numberValue(run.cost_info?.call_duration_seconds);
  if (costDuration !== null) return costDuration;
  return numberValue(run.usage_info?.call_duration_seconds);
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`;
}

function getProviderCostPerMinute(
  actualCost: ActualCostInfo | null,
  durationSeconds: number | null,
): { label: string; isAboveCeiling: boolean } {
  if (!actualCost || durationSeconds === null || durationSeconds <= 0) return { label: "-", isAboveCeiling: false };
  const minutes = durationSeconds / 60;
  const totalInr = numberValue(actualCost.total_inr);
  const totalUsd = numberValue(actualCost.total_usd);
  const rateInr = totalInr !== null ? totalInr / minutes : null;
  const isAboveCeiling = rateInr !== null && rateInr > 3.20;
  const label = `${formatInrPrimary(rateInr, totalUsd === null ? null : totalUsd / minutes)}/min`;
  return { label, isAboveCeiling };
}

/* old label helper replaced */
function getProviderCostPerMinuteLabel(
  actualCost: ActualCostInfo | null,
  durationSeconds: number | null,
): string {
  return getProviderCostPerMinute(actualCost, durationSeconds).label;
}

function _deleted(
  actualCost: ActualCostInfo | null,
  durationSeconds: number | null,
): string {
  if (!actualCost || durationSeconds === null || durationSeconds <= 0) return "-";
  const minutes = durationSeconds / 60;
  const totalInr = numberValue(actualCost.total_inr);
  const totalUsd = numberValue(actualCost.total_usd);
  return `${formatInrPrimary(
    totalInr === null ? null : totalInr / minutes,
    totalUsd === null ? null : totalUsd / minutes,
  )}/min`;
}

function componentName(component: ActualCostComponent): string {
  if (component.label) return component.label;
  return [component.service, component.provider, component.model]
    .filter(Boolean)
    .join(" / ") || "Cost component";
}

function componentUsageText(component: ActualCostComponent): string {
  if (!component.usage) return "";
  return Object.entries(component.usage)
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .slice(0, 4)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(", ");
}

function componentCostLabel(component: ActualCostComponent): string {
  return formatInrPrimary(numberValue(component.cost_inr), numberValue(component.cost_usd));
}

function CostDetails({ actualCost }: { actualCost: ActualCostInfo | null }) {
  const components = actualCost?.components ?? [];
  if (!actualCost || components.length === 0) {
    return <span className="text-muted-foreground">-</span>;
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="h-8 w-8">
          <Info className="h-4 w-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent side="left" className="max-w-xl bg-popover text-popover-foreground border shadow-lg">
        <div className="space-y-2 text-xs">
          <div className="grid grid-cols-3 gap-3 border-b pb-2">
            <div>
              <p className="text-muted-foreground">AI APIs</p>
              <p className="font-semibold">
                {formatInrPrimary(numberValue(actualCost.ai_total_inr), numberValue(actualCost.ai_total_usd))}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Telephony CDR</p>
              <p className="font-semibold">
                {formatInrPrimary(
                  numberValue(actualCost.telephony_total_inr),
                  numberValue(actualCost.telephony_total_usd),
                )}
              </p>
            </div>
            <div>
              <p className="text-muted-foreground">Total actual</p>
              <p className="font-semibold">
                {formatInrPrimary(numberValue(actualCost.total_inr), numberValue(actualCost.total_usd))}
              </p>
            </div>
          </div>
          <div className="space-y-2">
            {components.map((component, index) => (
              <div key={`${componentName(component)}-${index}`} className="border-b pb-2 last:border-b-0 last:pb-0">
                <div className="flex items-center justify-between gap-4">
                  <span className="font-medium">{componentName(component)}</span>
                  <span>{componentCostLabel(component)}</span>
                </div>
                <p className="text-muted-foreground">
                  {[component.provider, component.model].filter(Boolean).join(" / ")}
                </p>
                {componentUsageText(component) && (
                  <p className="text-muted-foreground">{componentUsageText(component)}</p>
                )}
                {component.estimated && <p className="text-amber-600">Estimated</p>}
                {component.warning && <p className="text-destructive">{component.warning}</p>}
                {component.note && <p className="text-muted-foreground">{component.note}</p>}
                {component.source_url && (
                  <p className="text-muted-foreground">
                    Source: {component.source_url}
                  </p>
                )}
              </div>
            ))}
          </div>
          {actualCost.warnings && actualCost.warnings.length > 0 && (
            <div className="space-y-1 border-t pt-2 text-destructive">
              {actualCost.warnings.map((warning, index) => (
                <p key={`${warning}-${index}`}>{warning}</p>
              ))}
            </div>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}


const BASIS_COPY: Record<CostSummary["basis"], { label: string; cls: string; note: string }> = {
  estimate: {
    label: "Estimate",
    cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30",
    note: "Every costed run is an estimate. Nothing here is reconciled with a provider invoice or CDR.",
  },
  mixed: {
    label: "Partly reconciled",
    cls: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/30",
    note: "Some runs are reconciled with provider records; the rest are estimates.",
  },
  reconciled: {
    label: "Reconciled",
    cls: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/30",
    note: "All costed runs are reconciled with provider records.",
  },
  no_cost_data: {
    label: "No cost data",
    cls: "bg-slate-500/10 text-slate-600 border-slate-400/40",
    note: "No run in this selection has a recorded cost.",
  },
};

function RunCostBadge({ run }: { run: WorkflowRun }) {
  const status = run.cost_summary?.status ?? "not_costed";
  if (status === "not_costed") {
    return <Badge variant="outline" className="text-[10px]">Not costed</Badge>;
  }
  if (status === "reconciled") {
    return <Badge variant="outline" className="text-[10px] border-emerald-500/30 bg-emerald-500/10 text-emerald-700">Reconciled</Badge>;
  }
  if (status === "reconciliation_failed") {
    return <Badge variant="outline" className="text-[10px] border-rose-500/30 bg-rose-500/10 text-rose-700">Reconciliation failed</Badge>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Badge variant="outline" className="text-[10px] border-amber-500/30 bg-amber-500/10 text-amber-700">Estimate</Badge>
      {(run.cost_summary?.unpriced_components ?? 0) > 0 && (
        <Badge variant="outline" className="text-[10px] border-rose-500/30 bg-rose-500/10 text-rose-700" title="A model used on this run has no configured token price">
          Unpriced model
        </Badge>
      )}
    </span>
  );
}

/** Unknown is never shown as zero or blank: the reason is always explicit. */
const na = (formatted: string, reason = "n/a") => (formatted === "-" ? reason : formatted);

function CostsContent() {
  const auth = useAuth();
  const searchParams = useSearchParams();
  const runIdParam = searchParams.get("runId");
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [recalculatingRunId, setRecalculatingRunId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportFormat, setExportFormat] = useState<"csv" | "json" | "zip">("csv");
  const [runStatus, setRunStatus] = useState("all");

  // Filters (sent to the server; summary and pagination always cover the whole filtered set)
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [origin, setOrigin] = useState("all");
  const [billing, setBilling] = useState("all");
  const [direction, setDirection] = useState("all");
  const [engine, setEngine] = useState("all");
  const [minCost, setMinCost] = useState("");
  const [maxCost, setMaxCost] = useState("");
  const [sortKey, setSortKey] = useState("created_at");
  const [runIdFilter, setRunIdFilter] = useState<number | null>(null);

  useEffect(() => {
    const id = runIdParam ? Number(runIdParam) : NaN;
    if (Number.isFinite(id)) setRunIdFilter(id);
  }, [runIdParam]);

  const filtersJson = useMemo(() => {
    const filters: Record<string, unknown>[] = [];
    if (runIdFilter !== null) filters.push({ attribute: "runId", type: "number", value: { value: runIdFilter } });
    if (search.trim().length >= 2) filters.push({ attribute: "search", type: "text", value: { value: search.trim() } });
    if (dateFrom || dateTo) {
      filters.push({
        attribute: "dateRange",
        type: "dateRange",
        value: {
          ...(dateFrom ? { from: new Date(`${dateFrom}T00:00:00`).toISOString() } : {}),
          ...(dateTo ? { to: new Date(`${dateTo}T23:59:59`).toISOString() } : {}),
        },
      });
    }
    if (origin !== "all") filters.push({ attribute: "callOrigin", type: "multiSelect", value: { codes: [origin] } });
    if (billing !== "all") filters.push({ attribute: "billingStatus", type: "multiSelect", value: { codes: [billing] } });
    if (direction !== "all") filters.push({ attribute: "direction", type: "radio", value: { status: direction } });
    if (runStatus !== "all") filters.push({ attribute: "status", type: "radio", value: { status: runStatus } });
    if (engine !== "all") filters.push({ attribute: "engine", type: "multiSelect", value: { codes: [engine] } });
    const min = minCost.trim() === "" ? undefined : Number(minCost);
    const max = maxCost.trim() === "" ? undefined : Number(maxCost);
    if ((min !== undefined && Number.isFinite(min)) || (max !== undefined && Number.isFinite(max))) {
      filters.push({
        attribute: "costRange",
        type: "numberRange",
        value: { ...(min !== undefined && Number.isFinite(min) ? { min } : {}), ...(max !== undefined && Number.isFinite(max) ? { max } : {}) },
      });
    }
    return filters.length ? JSON.stringify(filters) : undefined;
  }, [runIdFilter, search, dateFrom, dateTo, origin, billing, direction, runStatus, engine, minCost, maxCost]);

  const fetchCosts = useCallback(
    async (targetPage: number, refresh = false) => {
      if (!auth.isAuthenticated) return;
      if (refresh) setIsRefreshing(true);
      else setIsLoading(true);
      setError("");
      try {
        const [data, sum] = await Promise.all([
          costApi.listRuns<WorkflowRunsResponse>({
            page: targetPage,
            limit: PAGE_SIZE,
            sort_by: sortKey,
            sort_order: "desc",
            filters: filtersJson,
          }),
          costApi.getSummary(filtersJson),
        ]);
        setRuns(data.workflow_runs);
        setPage(data.page);
        setTotalPages(data.total_pages);
        setTotalCount(data.total_count);
        setSummary(sum);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load cost analytics");
      } finally {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    },
    [auth.isAuthenticated, filtersJson, sortKey],
  );

  const recalculateRunCost = useCallback(
    async (runId: number) => {
      setRecalculatingRunId(runId);
      setError("");
      try {
        await costApi.recalculateWorkflowRunCost(runId);
        await fetchCosts(page, true);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to recalculate run cost");
      } finally {
        setRecalculatingRunId(null);
      }
    },
    [fetchCosts, page],
  );

  const isSuperuser = Boolean((auth.user as LocalUser | undefined)?.is_superuser);

  // Filters or sorting changed: go back to page 1 and clear any selection.
  useEffect(() => {
    setPage(1);
    setSelected(new Set());
  }, [filtersJson, sortKey]);

  useEffect(() => {
    if (!auth.loading && auth.isAuthenticated && isSuperuser) {
      void fetchCosts(page);
    }
  }, [auth.loading, auth.isAuthenticated, isSuperuser, fetchCosts, page]);

  // Real-time: conversations finishing or being analysed change cost rows. Bounded refetch.
  const { subscribe } = useRealtimeDashboard();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = subscribe("*", (event) => {
      if (!["conversation_finalized", "conversation_started", "conversations_archived", "conversations_restored"].includes(event.event_type)) return;
      if (timer || document.visibilityState === "hidden") return;
      timer = setTimeout(() => {
        timer = null;
        void fetchCosts(page, true);
      }, 2000);
    });
    return () => {
      unsubscribe();
      if (timer) clearTimeout(timer);
    };
  }, [subscribe, fetchCosts, page]);

  const exportCsv = async (scope: "selected" | "filtered") => {
    setExporting(true);
    try {
      const count = await costApi.exportRecords(auth.getAccessToken, exportFormat, {
        ids: scope === "selected" ? Array.from(selected) : undefined,
        filters: scope === "filtered" ? filtersJson : undefined,
      });
      toast.success(`Exported ${count} cost record${count === 1 ? "" : "s"} as ${exportFormat.toUpperCase()} (estimates are labelled as such)`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  if ((auth.loading || isLoading) && runs.length === 0 && !summary) {
    return (
      <div className="container mx-auto flex min-h-[400px] items-center justify-center p-6">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Loading cost analytics...</span>
        </div>
      </div>
    );
  }

  if (!auth.loading && (!auth.isAuthenticated || !isSuperuser)) {
    return (
      <div className="container mx-auto p-6 max-w-lg min-h-[60vh] flex items-center justify-center">
        <Card className="w-full p-8 text-center space-y-4 border-amber-500/30 shadow-lg">
          <div className="w-12 h-12 rounded-full bg-amber-500/10 text-amber-600 flex items-center justify-center mx-auto">
            <ShieldAlert className="h-6 w-6" />
          </div>
          <CardTitle className="text-xl">Superadministrator Access Required</CardTitle>
          <CardDescription className="text-sm text-muted-foreground">
            This technical infrastructure cost page is restricted to platform superadministrators.
            Business owners can review caller usage and budget caps in{" "}
            <Link href="/costs" className="text-primary font-medium underline">
              Voice Costs
            </Link>.
          </CardDescription>
          <div className="pt-2">
            <Link href="/overview">
              <Button variant="outline" size="sm">Return to Dashboard</Button>
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  const basis = summary ? BASIS_COPY[summary.basis] : null;
  const allOnPageSelected = runs.length > 0 && runs.every((r) => selected.has(r.id));
  const inr = (v: number | null | undefined) => (typeof v === "number" ? formatInr(v) : "-");

  return (
    <main className="container mx-auto max-w-full space-y-6 p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-3xl font-bold">Cost Analytics</h1>
          <p className="text-muted-foreground">
            Provider cost for every conversation, labelled as an estimate or reconciled. Totals cover the whole filtered set.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void fetchCosts(page, true)} disabled={isRefreshing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Link href="/superadmin/runs">
            <Button variant="outline">
              All Runs
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </Link>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          <AlertTriangle className="mt-0.5 h-4 w-4" />
          <span>{error}</span>
        </div>
      )}

      {/* Summary: computed on the server across the entire filtered set */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription className="flex items-center gap-2">
              Recorded cost
              {basis && <Badge variant="outline" className={`text-[10px] ${basis.cls}`}>{basis.label}</Badge>}
            </CardDescription>
            <CardTitle>{summary ? formatInr(summary.totals_inr.total) : "-"}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            {summary
              ? `${summary.scope.costed_runs} of ${summary.scope.runs} runs have a cost; ${summary.scope.uncosted_runs} have none.`
              : ""}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Cost per costed minute</CardDescription>
            <CardTitle>{summary?.per_minute_inr != null ? `${formatInr(summary.per_minute_inr)}/min` : "-"}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            {summary ? `${summary.costed_minutes} costed minutes. ${basis?.note ?? ""}` : ""}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>AI API cost</CardDescription>
            <CardTitle>{summary ? formatInr(summary.totals_inr.ai) : "-"}</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Duration-based estimate unless token pricing is configured for the model.
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Telephony</CardDescription>
            <CardTitle>
              {summary && summary.telephony.applicable ? formatInr(summary.totals_inr.telephony) : "Not applicable"}
            </CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            {summary
              ? summary.telephony.applicable
                ? `${summary.telephony.pstn_runs} phone-call runs. Only provider CDRs are reconciled; no negotiated carrier rate is stored.`
                : "No phone-line (PSTN) calls in this selection; browser and simulated calls have no carrier charge."
              : ""}
          </CardContent>
        </Card>
      </div>

      {/* Missing pricing inputs: shown instead of any target claim */}
      {summary && (summary.pricing_inputs.unpriced_models.length > 0 || summary.pricing_inputs.notes.length > 0) && (
        <Card className="border-amber-500/30">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Pricing inputs still needed</CardTitle>
            <CardDescription>No cost-per-minute target is evaluated until these are available.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            {summary.pricing_inputs.unpriced_models.map((m) => (
              <p key={m.model} className="text-destructive">
                No token price configured for <span className="font-mono">{m.model}</span> ({m.runs} run{m.runs === 1 ? "" : "s"} in the latest sample).
              </p>
            ))}
            {summary.pricing_inputs.fx.map((fx, i) => (
              <p key={i} className="text-muted-foreground">
                Exchange rate {fx.pair} {fx.rate} was last checked {fx.source_last_checked ?? "on an unknown date"}.
              </p>
            ))}
            {summary.pricing_inputs.notes.map((n) => (
              <p key={n} className="text-muted-foreground">{n}</p>
            ))}
            <p className="pt-1 text-xs text-muted-foreground">
              Reference sources:{" "}
              <a className="underline" href="https://ai.google.dev/gemini-api/docs/pricing" target="_blank" rel="noreferrer">Gemini pricing</a>,{" "}
              <a className="underline" href="https://docs.vobiz.ai/cdr/get-cdr" target="_blank" rel="noreferrer">Vobiz CDR</a>.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <Card>
        <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <Input placeholder="Search run or phone…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search runs" className="xl:col-span-2" />
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} aria-label="From date" />
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} aria-label="To date" />
          <Select value={origin} onValueChange={setOrigin}>
            <SelectTrigger aria-label="Call origin"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All origins</SelectItem>
              <SelectItem value="pstn">Phone (PSTN)</SelectItem>
              <SelectItem value="browser">Browser</SelectItem>
              <SelectItem value="simulated">Simulated</SelectItem>
            </SelectContent>
          </Select>
          <Select value={billing} onValueChange={setBilling}>
            <SelectTrigger aria-label="Billing status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any cost status</SelectItem>
              <SelectItem value="ESTIMATED">Estimated</SelectItem>
              <SelectItem value="PENDING_RECONCILIATION">Pending reconciliation</SelectItem>
              <SelectItem value="VERIFIED">Reconciled</SelectItem>
              <SelectItem value="RECONCILIATION_FAILED">Reconciliation failed</SelectItem>
              <SelectItem value="NOT_COSTED">Not costed</SelectItem>
            </SelectContent>
          </Select>
          <Select value={direction} onValueChange={setDirection}>
            <SelectTrigger aria-label="Direction"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Inbound & outbound</SelectItem>
              <SelectItem value="inbound">Inbound</SelectItem>
              <SelectItem value="outbound">Outbound</SelectItem>
            </SelectContent>
          </Select>
          <Select value={runStatus} onValueChange={setRunStatus}>
            <SelectTrigger aria-label="Run status"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any run status</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="in_progress">Not completed</SelectItem>
            </SelectContent>
          </Select>
          <Select value={engine} onValueChange={setEngine}>
            <SelectTrigger aria-label="Engine"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All engines / carriers</SelectItem>
              <SelectItem value="livekit">LiveKit</SelectItem>
              <SelectItem value="vobiz">Vobiz</SelectItem>
              <SelectItem value="voicelink">VoiceLink</SelectItem>
              <SelectItem value="smallwebrtc">Web RTC</SelectItem>
            </SelectContent>
          </Select>
          <Input type="number" min={0} placeholder="Min ₹" value={minCost} onChange={(e) => setMinCost(e.target.value)} aria-label="Minimum cost in rupees" />
          <Input type="number" min={0} placeholder="Max ₹" value={maxCost} onChange={(e) => setMaxCost(e.target.value)} aria-label="Maximum cost in rupees" />
          <Select value={sortKey} onValueChange={setSortKey}>
            <SelectTrigger aria-label="Sort"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="created_at">Newest first</SelectItem>
              <SelectItem value="cost">Highest cost</SelectItem>
              <SelectItem value="duration">Longest call</SelectItem>
            </SelectContent>
          </Select>
          {runIdFilter !== null && (
            <div className="flex items-center gap-2 text-xs">
              <Badge variant="secondary">Run #{runIdFilter}</Badge>
              <button className="underline" onClick={() => setRunIdFilter(null)}>clear</button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <CardTitle>Conversation Cost Details</CardTitle>
              <CardDescription>
                Showing {runs.length} of {totalCount} conversations
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={exportFormat} onValueChange={(v) => setExportFormat(v as "csv" | "json" | "zip")}>
                <SelectTrigger className="h-8 w-24 text-xs" aria-label="Export format"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="csv">CSV</SelectItem>
                  <SelectItem value="json">JSON</SelectItem>
                  <SelectItem value="zip">ZIP</SelectItem>
                </SelectContent>
              </Select>
              <Button variant="outline" size="sm" disabled={exporting || selected.size === 0} onClick={() => void exportCsv("selected")}>
                Export {selected.size || ""} selected
              </Button>
              <Button variant="outline" size="sm" disabled={exporting || totalCount === 0} onClick={() => void exportCsv("filtered")}>
                Export all {totalCount} filtered
              </Button>
              <Badge variant="outline">Superadmin only</Badge>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <div className="py-10 text-center text-muted-foreground">No conversations match these filters.</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted">
                    <TableHead className="w-8">
                      <Checkbox
                        checked={allOnPageSelected}
                        onCheckedChange={() => setSelected(allOnPageSelected ? new Set() : new Set(runs.map((r) => r.id)))}
                        aria-label="Select all runs on this page"
                      />
                    </TableHead>
                    <TableHead>Run</TableHead>
                    <TableHead>Workflow</TableHead>
                    <TableHead>Organization</TableHead>
                    <TableHead>Origin</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>AI APIs</TableHead>
                    <TableHead>Telephony</TableHead>
                    <TableHead>Total</TableHead>
                    <TableHead>Cost status</TableHead>
                    <TableHead>Per min</TableHead>
                    <TableHead>Breakdown</TableHead>
                    <TableHead>Reprice</TableHead>
                    <TableHead>Created</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((run) => {
                    const actualCost = getActualCost(run.cost_info);
                    const durationSeconds = getDurationSeconds(run);
                    const noPstn = run.call_origin !== "pstn" && run.call_origin != null;
                    return (
                      <TableRow key={run.id} data-state={selected.has(run.id) ? "selected" : undefined}>
                        <TableCell>
                          <Checkbox
                            checked={selected.has(run.id)}
                            onCheckedChange={() =>
                              setSelected((prev) => {
                                const next = new Set(prev);
                                if (next.has(run.id)) next.delete(run.id);
                                else next.add(run.id);
                                return next;
                              })
                            }
                            aria-label={`Select run ${run.id}`}
                          />
                        </TableCell>
                        <TableCell className="font-mono text-sm">
                          <div className="flex flex-col gap-0.5">
                            <Link
                              className="inline-flex items-center gap-1 font-medium text-primary underline-offset-4 hover:underline"
                              href={`/conversations?runId=${run.id}`}
                              title="Open the conversation, transcript and recording"
                            >
                              #{run.id}
                              <ExternalLink className="h-3 w-3" />
                            </Link>
                            <Link
                              className="text-[10px] text-muted-foreground underline-offset-4 hover:underline"
                              href={`/superadmin/runs?filters=${encodeURIComponent(JSON.stringify([{ id: "id", value: { value: run.id } }]))}`}
                            >
                              run record
                            </Link>
                            {run.conversation_state && run.conversation_state !== "active" && (
                              <span className="text-[10px] text-muted-foreground">
                                conversation {run.conversation_state === "trashed" ? "in Trash" : "archived"} (cost kept)
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="max-w-[220px] truncate font-medium">
                            {run.workflow_name || `Workflow #${run.workflow_id}`}
                          </div>
                          <div className="text-xs text-muted-foreground">#{run.workflow_id}</div>
                        </TableCell>
                        <TableCell>
                          <div className="max-w-[180px] truncate">{run.organization_name || "-"}</div>
                          {run.organization_id && (
                            <div className="text-xs text-muted-foreground">#{run.organization_id}</div>
                          )}
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            {run.call_origin && <OriginBadge origin={run.call_origin} />}
                            <span className="text-[10px] text-muted-foreground">{run.mode}</span>
                          </div>
                        </TableCell>
                        <TableCell>{formatDuration(durationSeconds)}</TableCell>
                        <TableCell>
                          {na(formatInrPrimary(numberValue(actualCost?.ai_total_inr), numberValue(actualCost?.ai_total_usd)), "n/a")}
                        </TableCell>
                        <TableCell>
                          {noPstn ? (
                            <span className="text-xs text-muted-foreground" title="No phone line: no carrier cost applies">n/a (no PSTN)</span>
                          ) : (
                            na(
                              formatInrPrimary(numberValue(actualCost?.telephony_total_inr), numberValue(actualCost?.telephony_total_usd)),
                              "n/a (carrier cost pending)",
                            )
                          )}
                        </TableCell>
                        <TableCell className="font-medium">
                          {na(formatInrPrimary(numberValue(actualCost?.total_inr), numberValue(actualCost?.total_usd)), "n/a (not costed)")}
                        </TableCell>
                        <TableCell><RunCostBadge run={run} /></TableCell>
                        <TableCell>{getProviderCostPerMinuteLabel(actualCost, durationSeconds)}</TableCell>
                        <TableCell>
                          <CostDetails actualCost={actualCost} />
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            disabled={recalculatingRunId === run.id}
                            onClick={() => void recalculateRunCost(run.id)}
                            title="Reprice"
                          >
                            <RefreshCw className={`h-4 w-4 ${recalculatingRunId === run.id ? "animate-spin" : ""}`} />
                          </Button>
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                          {formatDate(run.created_at)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-between">
              <p className="text-sm text-muted-foreground">Page {page} of {totalPages}</p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page === 1 || isLoading} onClick={() => setPage((current) => Math.max(1, current - 1))}>
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={page === totalPages || isLoading} onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>
                  Next
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}

export default function SuperadminCostsPage() {
  return (
    <Suspense
      fallback={
        <div className="container mx-auto flex min-h-[400px] items-center justify-center p-6">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CostsContent />
    </Suspense>
  );
}
