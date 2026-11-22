"use client";

import {
  AlertTriangle,
  Archive,
  ArrowRight,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Flame,
  Phone,
  PhoneCall,
  PhoneForwarded,
  PhoneMissed,
  RefreshCw,
  Search,
  Sparkles,
  TrendingUp,
  Users,
  Wifi,
  WifiOff,
} from "lucide-react";
import Link from "next/link";
import React, { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { BulkToolbar, PaginationBar } from "@/components/conversations/BulkToolbar";
import {
  AnalysisBadge,
  ClassificationBadge,
  LifecycleBadge,
  OriginBadge,
  RecordingBadge,
} from "@/components/conversations/badges";
import { ConversationDrawer } from "@/components/conversations/ConversationDrawer";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useConversationList } from "@/hooks/useConversationList";
import { useAuth, type LocalUser } from "@/lib/auth";
import { formatDuration } from "@/lib/conversationsApi";

interface MissedCallItem {
  id: string;
  organization_id: number;
  item_type: string;
  priority: string;
  status: string;
  title: string;
  description: string;
  customer_phone: string;
  customer_name?: string;
  workflow_run_id?: number;
  created_at: string;
  evidence?: {
    call_disposition?: string;
    run_name?: string;
  };
}

const MISSED_PAGE_SIZE = 10;

const SORTS: Record<string, { key: string; order: "asc" | "desc"; label: string }> = {
  newest: { key: "created_at", order: "desc", label: "Newest first" },
  oldest: { key: "created_at", order: "asc", label: "Oldest first" },
  score: { key: "lead_score", order: "desc", label: "Highest score" },
  longest: { key: "duration", order: "desc", label: "Longest call" },
};

export default function LeadsPage() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const isSuperuser = Boolean((user as LocalUser | undefined)?.is_superuser);
  const [activeTab, setActiveTab] = useState("leads");

  // Leads: server-side pagination, filters, selection and counts (shared with Conversations)
  const list = useConversationList({
    authReady: !authLoading,
    getAccessToken,
    pageSize: 20,
    initialFilters: { status: "completed" }, // never-started and stale runs are not leads; widen via the status filter
  });
  const [searchText, setSearchText] = useState("");
  const [drawerRunId, setDrawerRunId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [sortChoice, setSortChoice] = useState("newest");

  useEffect(() => {
    const handle = setTimeout(() => {
      const next = searchText.trim();
      if (next !== list.filters.search && (next.length === 0 || next.length >= 2)) list.setFilters({ search: next });
    }, 400);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText]);

  // Missed Calls State
  const [missedCalls, setMissedCalls] = useState<MissedCallItem[]>([]);
  const [loadingMissed, setLoadingMissed] = useState(true);
  const [searchMissed, setSearchMissed] = useState("");
  const [statusFilterMissed, setStatusFilterMissed] = useState("PENDING");
  const [missedPage, setMissedPage] = useState<number>(1);
  const [redialingId, setRedialingId] = useState<string | null>(null);

  const fetchMissedCalls = async () => {
    try {
      setLoadingMissed(true);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/inbox?item_type=MISSED_CALL", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setMissedCalls(data.items || []);
      }
    } catch (e) {
      console.error("Error fetching missed calls:", e);
    } finally {
      setLoadingMissed(false);
    }
  };

  const handleRedial = async (item: MissedCallItem) => {
    setRedialingId(item.id);
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/telephony/initiate-call", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          workflow_id: 1,
          phone_number: item.customer_phone,
        }),
      });

      if (res.ok) {
        toast.success(`Outbound redial initiated to ${item.customer_phone}`);
        await fetch(`/api/v1/business/inbox/${item.id}/status`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({ status: "RESOLVED" }),
        });
        fetchMissedCalls();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || "Failed to initiate call via telephony carrier.");
      }
    } catch (e) {
      console.error("Redial failed:", e);
      toast.error("Failed to connect to telephony service.");
    } finally {
      setRedialingId(null);
    }
  };

  const handleDismissMissed = async (item: MissedCallItem) => {
    try {
      const token = await getAccessToken();
      await fetch(`/api/v1/business/inbox/${item.id}/status`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ status: "ARCHIVED" }),
      });
      toast.success("Missed call archived");
      fetchMissedCalls();
    } catch (e) {
      console.error(e);
      toast.error("Failed to archive missed call");
    }
  };

  const handleUnarchiveMissed = async (item: MissedCallItem) => {
    try {
      const token = await getAccessToken();
      await fetch(`/api/v1/business/inbox/${item.id}/status`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ status: "PENDING" }),
      });
      toast.success("Missed call restored to Pending");
      fetchMissedCalls();
    } catch (e) {
      console.error(e);
      toast.error("Failed to restore missed call");
    }
  };

  useEffect(() => {
    if (authLoading) return;
    fetchMissedCalls();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, user]);

  // Filtered Missed Calls
  const filteredMissedCalls = useMemo(() => {
    return missedCalls.filter((item) => {
      if (statusFilterMissed !== "ALL" && item.status !== statusFilterMissed) {
        return false;
      }
      if (searchMissed.trim()) {
        const q = searchMissed.toLowerCase().trim();
        const match =
          item.customer_phone.toLowerCase().includes(q) ||
          (item.customer_name && item.customer_name.toLowerCase().includes(q)) ||
          item.title.toLowerCase().includes(q);
        if (!match) return false;
      }
      return true;
    });
  }, [missedCalls, statusFilterMissed, searchMissed]);

  useEffect(() => {
    setMissedPage(1);
  }, [statusFilterMissed, searchMissed]);

  const totalMissedPages = Math.max(1, Math.ceil(filteredMissedCalls.length / MISSED_PAGE_SIZE));
  const paginatedMissed = useMemo(() => {
    const start = (missedPage - 1) * MISSED_PAGE_SIZE;
    return filteredMissedCalls.slice(start, start + MISSED_PAGE_SIZE);
  }, [filteredMissedCalls, missedPage]);

  const pendingMissedCount = missedCalls.filter((m) => m.status === "PENDING").length;

  // Server-side counts for the whole filtered set (not just the visible page).
  const hotCount = list.facets["Hot"] ?? 0;
  const warmCount = list.facets["Warm"] ?? 0;
  const coldCount = list.facets["Cold"] ?? 0;
  const facetTotal = Object.values(list.facets).reduce((a, b) => a + b, 0);
  const otherCount = Math.max(0, facetTotal - hotCount - warmCount - coldCount);
  const sourceNote =
    list.filters.source === "pstn"
      ? "Phone calls"
      : list.filters.source === "browser"
      ? "Browser calls"
      : list.filters.source === "simulated"
      ? "Simulations"
      : "All sources";

  return (
    <div className="container mx-auto px-4 py-8 space-y-6 max-w-7xl animate-fade-in-up">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <Badge
              variant="outline"
              className="bg-primary/10 text-primary border-primary/20 gap-1 px-3 py-1 font-semibold"
            >
              <Sparkles className="h-3.5 w-3.5 text-amber-500" />
              Lead & Call Intelligence
            </Badge>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Leads & Missed Calls</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Every analysed call with its evidence-based title, score and next action. Select, archive, export and correct leads here.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <span
            className={`inline-flex items-center gap-1 text-[11px] ${list.connection === "connected" ? "text-emerald-600" : "text-muted-foreground"}`}
            title={list.connection === "connected" ? "Updates arrive automatically" : "Live updates are reconnecting; use Refresh"}
          >
            {list.connection === "connected" ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
            {list.connection === "connected" ? "Live" : "Offline"}
          </span>
          <Button
            onClick={() => {
              list.reload();
              fetchMissedCalls();
            }}
            variant="outline"
            size="sm"
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${list.loading || list.refreshing || loadingMissed ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid w-full sm:w-96 grid-cols-2 mb-4">
          <TabsTrigger value="leads" className="gap-2">
            <Flame className="h-4 w-4 text-rose-500" />
            Leads ({list.total})
          </TabsTrigger>
          <TabsTrigger value="missed" className="gap-2">
            <PhoneMissed className="h-4 w-4 text-amber-500" />
            Missed Calls
            {pendingMissedCount > 0 && (
              <Badge variant="destructive" className="ml-1 text-[10px] px-1.5 py-0">
                {pendingMissedCount}
              </Badge>
            )}
          </TabsTrigger>
        </TabsList>

        {/* Tab 1: Leads */}
        <TabsContent value="leads" className="space-y-5">
          {/* KPI cards: computed server-side over the whole filtered set */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <Card className="p-4 bg-gradient-to-br from-rose-500/10 to-red-500/10 border-rose-500/20">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-rose-700 dark:text-rose-400 uppercase">Hot</span>
                <Flame className="h-4 w-4 text-rose-600" />
              </div>
              <p className="text-2xl font-bold mt-2">{list.loading ? "…" : hotCount}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">{sourceNote} • High intent</p>
            </Card>
            <Card className="p-4 bg-gradient-to-br from-amber-500/10 to-orange-500/10 border-amber-500/20">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase">Warm</span>
                <TrendingUp className="h-4 w-4 text-amber-600" />
              </div>
              <p className="text-2xl font-bold mt-2">{list.loading ? "…" : warmCount}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">{sourceNote} • Exploring</p>
            </Card>
            <Card className="p-4 bg-gradient-to-br from-slate-500/10 to-gray-500/10 border-slate-500/20">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-700 dark:text-slate-400 uppercase">Cold</span>
                <Users className="h-4 w-4 text-slate-600" />
              </div>
              <p className="text-2xl font-bold mt-2">{list.loading ? "…" : coldCount}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">{sourceNote} • Low intent</p>
            </Card>
            <Card className="p-4 bg-gradient-to-br from-indigo-500/10 to-blue-500/10 border-indigo-500/20">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-indigo-700 dark:text-indigo-400 uppercase">Other</span>
                <Sparkles className="h-4 w-4 text-indigo-600" />
              </div>
              <p className="text-2xl font-bold mt-2">{list.loading ? "…" : otherCount}</p>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                Unclassified, needs review, callbacks… (Hot + Warm + Cold + Other = {facetTotal})
              </p>
            </Card>
          </div>

          {/* Filters */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 bg-card p-4 rounded-xl border shadow-xs">
            <div className="relative sm:col-span-2">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search name, phone, title or transcript text…"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                className="pl-9 h-9 text-xs"
                aria-label="Search leads"
              />
            </div>
            <Select value={list.filters.source} onValueChange={(v) => list.setFilters({ source: v })}>
              <SelectTrigger className="h-9 text-xs" aria-label="Source">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All sources</SelectItem>
                <SelectItem value="pstn">Phone calls only</SelectItem>
                <SelectItem value="browser">Browser calls</SelectItem>
                <SelectItem value="simulated">Simulations</SelectItem>
                <SelectItem value="test">Browser + simulations</SelectItem>
              </SelectContent>
            </Select>
            <Select value={list.filters.classification} onValueChange={(v) => list.setFilters({ classification: v })}>
              <SelectTrigger className="h-9 text-xs" aria-label="Classification">
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
            <Select value={list.filters.archived} onValueChange={(v) => list.setFilters({ archived: v as "active" | "archived" | "trashed" | "all" })}>
              <SelectTrigger className="h-9 text-xs" aria-label="Archive state">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="archived">Archived</SelectItem>
                <SelectItem value="trashed">Trash</SelectItem>
                <SelectItem value="all">Everything</SelectItem>
              </SelectContent>
            </Select>
            <Select value={list.filters.status} onValueChange={(v) => list.setFilters({ status: v })}>
              <SelectTrigger className="h-9 text-xs" aria-label="Call status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any call status</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="failed">Failed</SelectItem>
                <SelectItem value="not_started">Never started</SelectItem>
                <SelectItem value="in_progress">In progress</SelectItem>
                <SelectItem value="stale">Stale (never finished)</SelectItem>
              </SelectContent>
            </Select>
            <Select value={list.filters.call_type} onValueChange={(v) => list.setFilters({ call_type: v })}>
              <SelectTrigger className="h-9 text-xs" aria-label="Direction">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Inbound & outbound</SelectItem>
                <SelectItem value="inbound">Inbound</SelectItem>
                <SelectItem value="outbound">Outbound</SelectItem>
              </SelectContent>
            </Select>
            <Input type="date" value={list.filters.date_from} onChange={(e) => list.setFilters({ date_from: e.target.value })} aria-label="From date" className="h-9 text-xs" />
            <Input type="date" value={list.filters.date_to} onChange={(e) => list.setFilters({ date_to: e.target.value })} aria-label="To date" className="h-9 text-xs" />
            <Select
              value={sortChoice}
              onValueChange={(v) => {
                setSortChoice(v);
                list.setSort({ key: SORTS[v].key, order: SORTS[v].order });
              }}
            >
              <SelectTrigger className="h-9 text-xs" aria-label="Sort order">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SORTS).map(([k, so]) => (
                  <SelectItem key={k} value={k}>
                    {so.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <BulkToolbar list={list} noun="lead" />

          {list.error && (
            <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="flex-1">{list.error}</span>
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={() => list.reload()}>
                Retry
              </Button>
            </div>
          )}

          {/* Leads list */}
          <Card>
            <CardContent className="p-0">
              {list.loading && list.items.length === 0 ? (
                <div className="p-6 space-y-3">
                  {[1, 2, 3, 4].map((i) => (
                    <Skeleton key={i} className="h-20 w-full rounded-lg" />
                  ))}
                </div>
              ) : list.items.length === 0 ? (
                <div className="text-center py-16 space-y-3">
                  <Users className="h-10 w-10 text-muted-foreground mx-auto opacity-50" />
                  <h3 className="font-semibold text-lg">No leads found</h3>
                  <p className="text-sm text-muted-foreground max-w-sm mx-auto">
                    {list.filters.search
                      ? "No leads match your search."
                      : "No calls match the current filters. New calls appear here automatically once analysed."}
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-3 border-b bg-muted/30 px-4 py-2">
                    <Checkbox checked={list.allPageSelected} onCheckedChange={list.togglePage} aria-label="Select all leads on this page" />
                    <span className="text-xs text-muted-foreground">Select page</span>
                  </div>
                  <div className="divide-y divide-border/60">
                    {list.items.map((lead) => {
                      const scored = !["Unclassified", "Unqualified", "Needs Review"].includes(lead.lead_classification);
                      const isHot = lead.lead_classification === "Hot";
                      const isWarm = lead.lead_classification === "Warm";
                      const budgetDisplay = !lead.budget || lead.budget.toLowerCase() === "not specified" ? null : lead.budget;
                      const timelineDisplay = !lead.timeline || lead.timeline.toLowerCase() === "not specified" ? null : lead.timeline;
                      return (
                        <div key={lead.id} className={`flex items-start gap-3 p-5 hover:bg-accent/40 transition-colors ${lead.lifecycle !== "active" ? "opacity-70" : ""}`}>
                          <Checkbox
                            className="mt-3"
                            checked={list.selectAllMatching || list.selected.has(lead.id)}
                            onCheckedChange={() => list.toggleRow(lead.id)}
                            aria-label={`Select lead from call ${lead.id}`}
                          />
                          <div className="min-w-0 flex-1 space-y-3">
                            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                              <div className="flex items-center gap-3 min-w-0">
                                <div
                                  className={`w-10 h-10 rounded-xl flex items-center justify-center font-bold text-sm shrink-0 ${
                                    isHot
                                      ? "bg-rose-500/10 text-rose-600 border border-rose-500/20"
                                      : isWarm
                                      ? "bg-amber-500/10 text-amber-600 border border-amber-500/20"
                                      : "bg-slate-500/10 text-slate-600 border border-slate-500/20"
                                  }`}
                                  title={scored ? "Evidence-based lead score" : "No score: not enough evidence"}
                                >
                                  {scored ? lead.lead_score : "—"}
                                </div>
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <span className="font-semibold text-sm">{lead.title}</span>
                                    <ClassificationBadge classification={lead.lead_classification} score={lead.lead_score} />
                                    <OriginBadge origin={lead.origin} />
                                    <AnalysisBadge status={lead.analysis_status} error={lead.analysis_error} />
                                    <RecordingBadge recording={lead.recording} />
                                    <LifecycleBadge lifecycle={lead.lifecycle} />
                                  </div>
                                  <p className="text-xs text-muted-foreground mt-0.5">
                                    <span className="font-mono">{lead.contact}</span>
                                    {lead.has_customer_name && <> • {lead.customer_name}</>} • Call #{lead.id} •{" "}
                                    {lead.date
                                      ? new Date(lead.date).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                                      : "Recently"}{" "}
                                    • {formatDuration(lead.duration_seconds)}
                                  </p>
                                  <p className="text-xs text-muted-foreground mt-0.5">
                                    Budget:{" "}
                                    {budgetDisplay ? (
                                      <span className="font-medium text-foreground">{budgetDisplay}</span>
                                    ) : (
                                      <span className="italic text-muted-foreground/70">Not stated</span>
                                    )}{" "}
                                    • Timeline:{" "}
                                    {timelineDisplay ? (
                                      <span className="font-medium text-foreground">{timelineDisplay}</span>
                                    ) : (
                                      <span className="italic text-muted-foreground/70">Not stated</span>
                                    )}
                                  </p>
                                </div>
                              </div>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="gap-1 text-xs shrink-0"
                                onClick={() => {
                                  setDrawerRunId(lead.id);
                                  setDrawerOpen(true);
                                }}
                              >
                                Transcript & details
                                <ArrowRight className="h-3.5 w-3.5" />
                              </Button>
                            </div>

                            {lead.summary && <p className="text-xs text-foreground/80 line-clamp-2">{lead.summary}</p>}

                            {lead.transcript_evidence && (
                              <div className="p-2.5 bg-muted/50 rounded-lg text-xs border border-border/50">
                                <span className="font-semibold text-muted-foreground block mb-0.5">Evidence:</span>
                                <p className="italic text-foreground/90">{lead.transcript_evidence}</p>
                              </div>
                            )}

                            <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs text-muted-foreground border-t border-border/40">
                              <div>
                                {lead.objections && lead.objections.length > 0 ? (
                                  <span className="text-amber-600 dark:text-amber-400">⚠️ Objections: {lead.objections.join(", ")}</span>
                                ) : (
                                  <span>No critical objections raised</span>
                                )}
                              </div>
                              {lead.recommended_next_action && (
                                <div className="font-medium text-primary">Next: {lead.recommended_next_action}</div>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <PaginationBar list={list} noun="lead" />
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Tab 2: Missed Calls */}
        <TabsContent value="missed" className="space-y-6">
          {/* Missed Calls Filter Bar */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-card p-4 rounded-xl border shadow-xs">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search missed calls by phone or title..."
                value={searchMissed}
                onChange={(e) => setSearchMissed(e.target.value)}
                className="pl-9 h-9 text-xs"
              />
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-muted-foreground uppercase whitespace-nowrap">
                Filter Status
              </span>
              <Select value={statusFilterMissed} onValueChange={setStatusFilterMissed}>
                <SelectTrigger className="w-36 h-9 text-xs">
                  <SelectValue placeholder="Filter Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="PENDING">Pending (Action Required)</SelectItem>
                  <SelectItem value="RESOLVED">Resolved (Redialed)</SelectItem>
                  <SelectItem value="ARCHIVED">Archived</SelectItem>
                  <SelectItem value="ALL">All Missed Calls</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Missed Calls List */}
          <Card>
            <CardContent className="p-0">
              {loadingMissed ? (
                <div className="p-6 space-y-3">
                  {[1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-20 w-full rounded-lg" />
                  ))}
                </div>
              ) : filteredMissedCalls.length === 0 ? (
                <div className="text-center py-16 space-y-3">
                  <CheckCircle2 className="h-10 w-10 text-emerald-500 mx-auto opacity-70" />
                  <h3 className="font-semibold text-lg">No pending missed calls</h3>
                  <p className="text-sm text-muted-foreground max-w-sm mx-auto">
                    All dropped, busy, or unserved inbound calls have been resolved.
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-border/60">
                  {paginatedMissed.map((item) => (
                    <div
                      key={item.id}
                      className="p-5 hover:bg-accent/40 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                    >
                      <div className="space-y-1.5 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-sm font-mono text-foreground">
                            {item.customer_phone}
                          </span>
                          <Badge
                            variant={item.status === "PENDING" ? "destructive" : "secondary"}
                            className="text-[10px]"
                          >
                            {item.status}
                          </Badge>
                          {item.evidence?.call_disposition && (
                            <Badge variant="outline" className="text-[10px] text-muted-foreground">
                              {item.evidence.call_disposition}
                            </Badge>
                          )}
                        </div>

                        <p className="text-xs text-foreground/80 font-medium">
                          {item.title}
                        </p>
                        <p className="text-xs text-muted-foreground line-clamp-1">
                          {item.description}
                        </p>

                        <div className="text-[11px] text-muted-foreground">
                          Logged: {new Date(item.created_at).toLocaleString()}
                        </div>
                      </div>

                      {/* 1-Click Redial Action Button */}
                      <div className="flex items-center gap-2 shrink-0 self-start sm:self-center">
                        {item.status === "PENDING" && (
                          <Button
                            size="sm"
                            disabled={redialingId === item.id}
                            onClick={() => handleRedial(item)}
                            className="gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white h-8 text-xs font-semibold"
                          >
                            <PhoneForwarded className="h-3.5 w-3.5" />
                            {redialingId === item.id ? "Dialing..." : "Redial"}
                          </Button>
                        )}

                        {item.status === "ARCHIVED" ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleUnarchiveMissed(item)}
                            className="h-8 text-xs text-muted-foreground hover:text-foreground"
                          >
                            Unarchive
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleDismissMissed(item)}
                            className="h-8 text-xs text-muted-foreground hover:text-foreground"
                          >
                            Archive
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Bounded Pagination Controls for Missed Calls */}
              {filteredMissedCalls.length > MISSED_PAGE_SIZE && (
                <div className="flex items-center justify-between px-6 py-4 border-t bg-muted/20">
                  <span className="text-xs text-muted-foreground">
                    Showing {(missedPage - 1) * MISSED_PAGE_SIZE + 1}–
                    {Math.min(missedPage * MISSED_PAGE_SIZE, filteredMissedCalls.length)} of {filteredMissedCalls.length} calls
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={missedPage <= 1}
                      onClick={() => setMissedPage((p) => Math.max(1, p - 1))}
                      className="h-8 text-xs gap-1"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" />
                      Previous
                    </Button>
                    <span className="text-xs font-medium px-2">
                      {missedPage} / {totalMissedPages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={missedPage >= totalMissedPages}
                      onClick={() => setMissedPage((p) => Math.min(totalMissedPages, p + 1))}
                      className="h-8 text-xs gap-1"
                    >
                      Next
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <ConversationDrawer
        runId={drawerRunId}
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        getAccessToken={getAccessToken}
        isSuperuser={isSuperuser}
        refreshKey={0}
        onChanged={() => list.reload()}
      />
    </div>
  );
}
