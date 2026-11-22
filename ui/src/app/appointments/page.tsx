"use client";

import {
  Calendar,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Filter,
  Phone,
  RefreshCw,
  Search,
  Sparkles,
  Tag,
  User,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import React, { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

interface AppointmentItem {
  run_id: number;
  contact: string;
  customer_name: string;
  date: string;
  appointment_type: string;
  preferred_slot: string;
  status: string;
  lead_classification: string;
  lead_score: number;
  evidence: string;
  agent_name: string;
}

const PAGE_SIZE = 10;

function isTestRun(contact: string, name: string): boolean {
  const c = (contact || "").toLowerCase();
  const n = (name || "").toLowerCase();
  return (
    c.includes("test") ||
    c.includes("simulat") ||
    c.includes("run-") ||
    c.startsWith("wr-") ||
    c.endsWith("run") ||
    n.includes("test")
  );
}

export default function AppointmentsPage() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const [appointments, setAppointments] = useState<AppointmentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [page, setPage] = useState<number>(1);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const fetchAppointments = async () => {
    try {
      setLoading(true);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/appointments", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const data = await res.json();
        setAppointments(data || []);
      }
    } catch (e) {
      console.error("Error fetching appointments:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authLoading) return;
    fetchAppointments();
  }, [authLoading, user]);

  const handleUpdateStatus = async (runId: number, newStatus: string) => {
    try {
      setUpdatingId(runId);
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/business/conversations/${runId}/lead`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({ appointment_status: newStatus }),
      });
      if (res.ok) {
        setAppointments((prev) =>
          prev.map((a) => (a.run_id === runId ? { ...a, status: newStatus } : a))
        );
        toast.success(`Appointment status updated to ${newStatus}`);
      } else {
        toast.error("Failed to update appointment status");
      }
    } catch (e) {
      console.error("Error updating appointment status:", e);
      toast.error("Failed to connect to appointments service");
    } finally {
      setUpdatingId(null);
    }
  };

  // Filtered appointments based on status, source and search query
  const filtered = useMemo(() => {
    return appointments.filter((item) => {
      if (statusFilter !== "all" && item.status.toLowerCase() !== statusFilter.toLowerCase()) {
        return false;
      }
      const testRun = isTestRun(item.contact, item.customer_name);
      if (sourceFilter === "real" && testRun) return false;
      if (sourceFilter === "test" && !testRun) return false;

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim();
        const match =
          item.customer_name.toLowerCase().includes(q) ||
          item.contact.toLowerCase().includes(q) ||
          (item.agent_name && item.agent_name.toLowerCase().includes(q)) ||
          item.appointment_type.toLowerCase().includes(q) ||
          item.preferred_slot.toLowerCase().includes(q);
        if (!match) return false;
      }
      return true;
    });
  }, [appointments, statusFilter, sourceFilter, searchQuery]);

  // Reset page to 1 when filters change
  useEffect(() => {
    setPage(1);
  }, [statusFilter, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginatedItems = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, page]);

  const confirmedCount = appointments.filter((a) => a.status === "Confirmed").length;
  const requestedCount = appointments.filter(
    (a) => a.status === "Requested" || a.status === "Pending"
  ).length;
  const rescheduledCount = appointments.filter((a) => a.status === "Rescheduled").length;

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
              <Calendar className="h-3.5 w-3.5" />
              Bookings & Consultations
            </Badge>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Appointments & Site Visits</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Confirmed visits, doctor consultations, trial sessions, and customer time slots captured by your AI agent.
          </p>
        </div>

        <Button onClick={fetchAppointments} variant="outline" size="sm" className="gap-2 shrink-0">
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="p-4 bg-gradient-to-br from-blue-500/10 to-indigo-500/10 border-blue-500/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-blue-700 dark:text-blue-400 uppercase">
              Total Bookings
            </span>
            <Calendar className="h-4 w-4 text-blue-600" />
          </div>
          <p className="text-2xl font-bold mt-2">{loading ? "..." : appointments.length}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">Captures across all phone calls</p>
        </Card>

        <Card className="p-4 bg-gradient-to-br from-emerald-500/10 to-teal-500/10 border-emerald-500/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 uppercase">
              Confirmed
            </span>
            <CheckCircle2 className="h-4 w-4 text-emerald-600" />
          </div>
          <p className="text-2xl font-bold mt-2">{loading ? "..." : confirmedCount}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">Authoritative bookings ready for staff</p>
        </Card>

        <Card className="p-4 bg-gradient-to-br from-amber-500/10 to-orange-500/10 border-amber-500/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase">
              Action Needed
            </span>
            <Clock className="h-4 w-4 text-amber-600" />
          </div>
          <p className="text-2xl font-bold mt-2">{loading ? "..." : requestedCount}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">Pending confirmation with caller</p>
        </Card>

        <Card className="p-4 bg-gradient-to-br from-purple-500/10 to-pink-500/10 border-purple-500/20">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-purple-700 dark:text-purple-400 uppercase">
              Rescheduled
            </span>
            <RefreshCw className="h-4 w-4 text-purple-600" />
          </div>
          <p className="text-2xl font-bold mt-2">{loading ? "..." : rescheduledCount}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">Updated appointment timeslots</p>
        </Card>
      </div>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-card p-4 rounded-xl border shadow-xs">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by caller, phone, agent or slot..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9 text-xs"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-muted-foreground uppercase whitespace-nowrap">
              Source
            </span>
            <Select value={sourceFilter} onValueChange={setSourceFilter}>
              <SelectTrigger className="w-36 h-9 text-xs">
                <SelectValue placeholder="All Sources" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Sources</SelectItem>
                <SelectItem value="real">Real Callers</SelectItem>
                <SelectItem value="test">Simulations</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-muted-foreground uppercase whitespace-nowrap">
              Filter Status
            </span>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-40 h-9 text-xs">
                <SelectValue placeholder="All Statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                <SelectItem value="Requested">Requested</SelectItem>
                <SelectItem value="Confirmed">Confirmed</SelectItem>
                <SelectItem value="Rescheduled">Rescheduled</SelectItem>
                <SelectItem value="Completed">Completed</SelectItem>
                <SelectItem value="Cancelled">Cancelled</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* Appointments List */}
      <Card>
        <CardContent className="p-0">
          {loading ? (
            <div className="p-6 space-y-3">
              {[1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-20 w-full rounded-lg" />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <div className="text-center py-16 space-y-3">
              <Calendar className="h-10 w-10 text-muted-foreground mx-auto opacity-50" />
              <h3 className="font-semibold text-lg">No appointments found</h3>
              <p className="text-sm text-muted-foreground max-w-sm mx-auto">
                {searchQuery
                  ? "No bookings match your search query."
                  : "No bookings match this filter. When callers book site visits or slots, they will appear here."}
              </p>
            </div>
          ) : (
            <div className="divide-y divide-border/60">
              {paginatedItems.map((item) => {
                const isTest = isTestRun(item.contact, item.customer_name);
                const isConfirmed = item.status === "Confirmed";
                const isSlotPending =
                  !item.preferred_slot ||
                  item.preferred_slot.toLowerCase() === "to be confirmed" ||
                  item.preferred_slot.toLowerCase() === "pending";

                // Format display name
                const displayName =
                  item.customer_name && item.customer_name !== "Valued Caller"
                    ? item.customer_name
                    : isTest
                    ? "Test / Demo Run"
                    : "Caller (" + item.contact + ")";

                return (
                  <div
                    key={item.run_id}
                    className="p-5 hover:bg-accent/40 transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                  >
                    <div className="space-y-1.5 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-sm">{displayName}</span>
                        <span className="text-xs text-muted-foreground font-mono">
                          {item.contact}
                        </span>

                        {isTest && (
                          <Badge
                            variant="secondary"
                            className="text-[10px] bg-slate-500/10 text-slate-600 dark:text-slate-400 border border-slate-300/40"
                          >
                            Test Run
                          </Badge>
                        )}

                        <Badge
                          variant="secondary"
                          className="text-xs bg-primary/10 text-primary border-primary/20"
                        >
                          {item.appointment_type}
                        </Badge>

                        {/* Resolved Slot Badge: eliminates Confirmed vs To be confirmed contradiction */}
                        {isConfirmed ? (
                          isSlotPending ? (
                            <Badge
                              variant="outline"
                              className="text-xs font-semibold text-blue-600 dark:text-blue-400 border-blue-300 dark:border-blue-800 bg-blue-50/50 dark:bg-blue-950/20"
                            >
                              Slot: Confirmed (Time TBA)
                            </Badge>
                          ) : (
                            <Badge
                              variant="outline"
                              className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 border-emerald-300 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-950/20"
                            >
                              Slot: {item.preferred_slot}
                            </Badge>
                          )
                        ) : isSlotPending ? (
                          <Badge
                            variant="outline"
                            className="text-xs font-normal text-amber-600 dark:text-amber-400 border-amber-300 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20"
                          >
                            Slot: Unspecified (Pending)
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="text-xs font-medium text-amber-600 dark:text-amber-400 border-amber-300 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20"
                          >
                            Requested Slot: {item.preferred_slot}
                          </Badge>
                        )}
                      </div>

                      {item.evidence && (
                        <p className="text-xs text-muted-foreground italic line-clamp-1">
                          "{item.evidence}"
                        </p>
                      )}

                      <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
                        <span>
                          Captured: {item.date ? new Date(item.date).toLocaleString() : "Recently"}
                        </span>
                        <span>•</span>
                        <span>Agent: {item.agent_name || "Voice Assistant"}</span>
                        <span>•</span>
                        <span>
                          Lead Score: {item.lead_score} ({item.lead_classification})
                        </span>
                      </div>
                    </div>

                    {/* Status Dropdown & Actions */}
                    <div className="flex items-center gap-2 shrink-0 self-start sm:self-center">
                      <Select
                        value={item.status}
                        disabled={updatingId === item.run_id}
                        onValueChange={(val) => handleUpdateStatus(item.run_id, val)}
                      >
                        <SelectTrigger className="w-36 h-8 text-xs font-semibold">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="Requested">Requested</SelectItem>
                          <SelectItem value="Confirmed">Confirmed</SelectItem>
                          <SelectItem value="Rescheduled">Rescheduled</SelectItem>
                          <SelectItem value="Completed">Completed</SelectItem>
                          <SelectItem value="Cancelled">Cancelled</SelectItem>
                        </SelectContent>
                      </Select>

                      <Link href={`/conversations?runId=${item.run_id}`}>
                        <Button variant="ghost" size="sm" className="h-8 text-xs">
                          Call Details
                        </Button>
                      </Link>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Bounded Pagination Controls */}
          {filtered.length > PAGE_SIZE && (
            <div className="flex items-center justify-between px-6 py-4 border-t bg-muted/20">
              <span className="text-xs text-muted-foreground">
                Showing {(page - 1) * PAGE_SIZE + 1}–
                {Math.min(page * PAGE_SIZE, filtered.length)} of {filtered.length} bookings
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  className="h-8 text-xs gap-1"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </Button>
                <span className="text-xs font-medium px-2">
                  {page} / {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
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
    </div>
  );
}
