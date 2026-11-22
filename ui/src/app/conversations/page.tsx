"use client";

import { AlertCircle, ArrowRight, MessageSquare, PhoneIncoming, PhoneOutgoing, RefreshCw, Search, Wifi, WifiOff } from "lucide-react";
import { useSearchParams } from "next/navigation";
import React, { Suspense, useEffect, useRef, useState } from "react";

import { BulkToolbar, PaginationBar } from "@/components/conversations/BulkToolbar";
import {
  AnalysisBadge,
  ClassificationBadge,
  CostBadge,
  LifecycleBadge,
  OriginBadge,
  RecordingBadge,
  RunStatusBadge,
} from "@/components/conversations/badges";
import { ConversationDrawer } from "@/components/conversations/ConversationDrawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useConversationList } from "@/hooks/useConversationList";
import { useRealtimeDashboard } from "@/hooks/useRealtimeDashboard";
import { useAuth, type LocalUser } from "@/lib/auth";
import { formatDuration } from "@/lib/conversationsApi";

const SORTS: Record<string, { key: string; order: "asc" | "desc"; label: string }> = {
  newest: { key: "created_at", order: "desc", label: "Newest first" },
  oldest: { key: "created_at", order: "asc", label: "Oldest first" },
  longest: { key: "duration", order: "desc", label: "Longest first" },
  score: { key: "lead_score", order: "desc", label: "Highest score" },
};

function ConversationsContent() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const searchParams = useSearchParams();
  const runIdParam = searchParams.get("runId");
  const isSuperuser = Boolean((user as LocalUser | undefined)?.is_superuser);

  const list = useConversationList({ authReady: !authLoading, getAccessToken });
  const [searchText, setSearchText] = useState("");
  const [sortChoice, setSortChoice] = useState("newest");
  const [drawerRunId, setDrawerRunId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerRefresh, setDrawerRefresh] = useState(0);

  // Debounce the free-text search so typing does not issue a request per key.
  useEffect(() => {
    const handle = setTimeout(() => {
      const next = searchText.trim();
      if (next !== list.filters.search && (next.length === 0 || next.length >= 2)) list.setFilters({ search: next });
    }, 400);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText]);

  // Deep link: /conversations?runId=337 opens that conversation directly.
  const opened = useRef<string | null>(null);
  useEffect(() => {
    if (!runIdParam || opened.current === runIdParam) return;
    const id = Number(runIdParam);
    if (Number.isFinite(id)) {
      opened.current = runIdParam;
      setDrawerRunId(id);
      setDrawerOpen(true);
    }
  }, [runIdParam]);

  // When a real-time event concerns the open conversation, refresh the drawer too.
  const { subscribe } = useRealtimeDashboard();
  useEffect(() => {
    return subscribe("*", (event) => {
      const data = (event.data ?? {}) as { workflow_run_id?: number; run_id?: number };
      const rid = Number(data.workflow_run_id ?? data.run_id ?? event.resource_id);
      if (drawerRunId !== null && rid === drawerRunId) setDrawerRefresh((n) => n + 1);
    });
  }, [subscribe, drawerRunId]);

  const f = list.filters;
  const live = list.connection === "connected";

  return (
    <div className="container mx-auto max-w-7xl animate-fade-in-up space-y-5 px-4 py-8">
      <div className="flex flex-col justify-between gap-4 border-b pb-5 sm:flex-row sm:items-center">
        <div>
          <Badge variant="outline" className="mb-1.5 gap-1 border-primary/20 bg-primary/10 px-3 py-1 font-semibold text-primary">
            <MessageSquare className="h-3.5 w-3.5" />
            Conversation Intelligence
          </Badge>
          <h1 className="text-3xl font-bold tracking-tight">Conversations & Transcripts</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Speaker-separated transcripts, an evidence-based title and summary for every call, recordings when available, and your own corrections.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 text-[11px] ${live ? "text-emerald-600" : "text-muted-foreground"}`}
            title={live ? "Updates arrive automatically" : "Live updates are reconnecting; use Refresh"}
          >
            {live ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {live ? "Live" : "Offline"}
          </span>
          <Button onClick={() => list.reload()} variant="outline" size="sm" className="gap-2">
            <RefreshCw className={`h-4 w-4 ${list.loading || list.refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 gap-3 rounded-xl border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="relative sm:col-span-2">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search phone, name, title or transcript text…"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            className="pl-9"
            aria-label="Search conversations"
          />
        </div>
        <Select value={f.source} onValueChange={(v) => list.setFilters({ source: v })}>
          <SelectTrigger aria-label="Source">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All sources</SelectItem>
            <SelectItem value="pstn">Phone calls</SelectItem>
            <SelectItem value="browser">Browser calls</SelectItem>
            <SelectItem value="simulated">Simulations</SelectItem>
            <SelectItem value="test">Browser + simulations</SelectItem>
          </SelectContent>
        </Select>
        <Select value={f.call_type} onValueChange={(v) => list.setFilters({ call_type: v })}>
          <SelectTrigger aria-label="Direction">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Inbound & outbound</SelectItem>
            <SelectItem value="inbound">Inbound</SelectItem>
            <SelectItem value="outbound">Outbound</SelectItem>
          </SelectContent>
        </Select>
        <Select value={f.classification} onValueChange={(v) => list.setFilters({ classification: v })}>
          <SelectTrigger aria-label="Classification">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All classifications</SelectItem>
            {["Hot", "Warm", "Cold", "Needs Review", "Unqualified", "Callback Requested", "Human Assistance Required", "Not Interested", "Unclassified"].map((c) => (
              <SelectItem key={c} value={c}>
                {c}
                {list.facets[c] !== undefined ? ` (${list.facets[c]})` : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={f.status} onValueChange={(v) => list.setFilters({ status: v })}>
          <SelectTrigger aria-label="Status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Any status</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="not_started">Never started</SelectItem>
            <SelectItem value="in_progress">In progress</SelectItem>
            <SelectItem value="stale">Stale (never finished)</SelectItem>
          </SelectContent>
        </Select>
        <Select value={f.archived} onValueChange={(v) => list.setFilters({ archived: v as "active" | "archived" | "trashed" | "all" })}>
          <SelectTrigger aria-label="Archive state">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="archived">Archived</SelectItem>
            <SelectItem value="trashed">Trash</SelectItem>
            <SelectItem value="all">Everything</SelectItem>
          </SelectContent>
        </Select>
        <Input type="date" value={f.date_from} onChange={(e) => list.setFilters({ date_from: e.target.value })} aria-label="From date" />
        <Input type="date" value={f.date_to} onChange={(e) => list.setFilters({ date_to: e.target.value })} aria-label="To date" />
        <Select
          value={sortChoice}
          onValueChange={(v) => {
            setSortChoice(v);
            list.setSort({ key: SORTS[v].key, order: SORTS[v].order });
          }}
        >
          <SelectTrigger aria-label="Sort order">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(SORTS).map(([k, s]) => (
              <SelectItem key={k} value={k}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <BulkToolbar list={list} />

      {list.error && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="flex-1">{list.error}</span>
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => list.reload()}>
            Retry
          </Button>
        </div>
      )}

      <Card>
        <CardContent className="p-0">
          {list.loading && list.items.length === 0 ? (
            <div className="space-y-3 p-6">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-16 w-full rounded-lg" />
              ))}
            </div>
          ) : list.items.length === 0 ? (
            <div className="space-y-3 py-16 text-center">
              <MessageSquare className="mx-auto h-10 w-10 text-muted-foreground opacity-50" />
              <h3 className="text-lg font-semibold">No conversations found</h3>
              <p className="mx-auto max-w-sm text-sm text-muted-foreground">
                No calls match the selected filters. Calls appear here as soon as they start and update when they finish.
              </p>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3 border-b bg-muted/30 px-4 py-2">
                <Checkbox checked={list.allPageSelected} onCheckedChange={list.togglePage} aria-label="Select all conversations on this page" />
                <span className="text-xs text-muted-foreground">Select page</span>
              </div>
              <ul className="divide-y divide-border/60">
                {list.items.map((item) => (
                  <li key={item.id} className={`flex items-start gap-3 px-4 py-3.5 transition-colors hover:bg-accent/40 ${item.lifecycle !== "active" ? "opacity-70" : ""}`}>
                    <Checkbox
                      className="mt-1"
                      checked={list.selectAllMatching || list.selected.has(item.id)}
                      onCheckedChange={() => list.toggleRow(item.id)}
                      aria-label={`Select call ${item.id}`}
                    />
                    <button
                      className="min-w-0 flex-1 space-y-1.5 text-left"
                      onClick={() => {
                        setDrawerRunId(item.id);
                        setDrawerOpen(true);
                      }}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold">{item.title}</span>
                        <ClassificationBadge classification={item.lead_classification} score={item.lead_score} />
                        <RunStatusBadge item={item} />
                        <OriginBadge origin={item.origin} />
                        {item.call_type === "inbound" ? (
                          <Badge variant="secondary" className="gap-1 text-[10px]">
                            <PhoneIncoming className="h-3 w-3" /> Inbound
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="gap-1 text-[10px]">
                            <PhoneOutgoing className="h-3 w-3" /> Outbound
                          </Badge>
                        )}
                        <AnalysisBadge status={item.analysis_status} error={item.analysis_error} />
                        <LifecycleBadge lifecycle={item.lifecycle} />
                      </div>
                      {item.summary && <p className="line-clamp-2 text-xs text-muted-foreground">{item.summary}</p>}
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                        <span className="font-mono">{item.contact}</span>
                        <span>{item.date ? new Date(item.date).toLocaleString() : "—"}</span>
                        <span>Duration {formatDuration(item.duration_seconds)}</span>
                        <RecordingBadge recording={item.recording} />
                        <CostBadge cost={item.cost} />
                      </div>
                    </button>
                    <ArrowRight className="mt-1 hidden h-4 w-4 shrink-0 text-muted-foreground sm:block" />
                  </li>
                ))}
              </ul>
              <PaginationBar list={list} />
            </>
          )}
        </CardContent>
      </Card>

      <ConversationDrawer
        runId={drawerRunId}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        getAccessToken={getAccessToken}
        isSuperuser={isSuperuser}
        refreshKey={drawerRefresh}
        onChanged={() => list.reload()}
      />
    </div>
  );
}

export default function ConversationsPage() {
  return (
    <Suspense
      fallback={
        <div className="container mx-auto max-w-7xl space-y-6 px-4 py-8">
          <Skeleton className="h-12 w-64 rounded-lg" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
      }
    >
      <ConversationsContent />
    </Suspense>
  );
}
