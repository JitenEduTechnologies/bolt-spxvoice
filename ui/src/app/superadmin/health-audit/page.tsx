"use client";

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  Download,
  FileText,
  HardDrive,
  Info,
  Loader2,
  Phone,
  Radio,
  RefreshCw,
  Search,
  Server,
  Shield,
  ShieldAlert,
  Sparkles,
  Terminal,
  Trash2,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth, type LocalUser } from "@/lib/auth";

export default function SystemHealthAuditCenterPage() {
  const { user, getAccessToken } = useAuth();
  const isSuperuser = Boolean((user as LocalUser | undefined)?.is_superuser);

  // States
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Diagnostics Data
  const [diagnostics, setDiagnostics] = useState<any>(null);

  // Incidents Data
  const [incidents, setIncidents] = useState<any[]>([]);
  const [incidentsCount, setIncidentsCount] = useState(0);

  // Logs Explorer Data
  const [logs, setLogs] = useState<any[]>([]);
  const [logFilterSeverity, setLogFilterSeverity] = useState("");
  const [logFilterCallId, setLogFilterCallId] = useState("");
  const [logFilterReqId, setLogFilterReqId] = useState("");
  const [logLoading, setLogLoading] = useState(false);

  // Call Timeline Data
  const [lookupCallId, setLookupCallId] = useState("");
  const [callTimeline, setCallTimeline] = useState<any>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  // Retention Data
  const [retentionDays, setRetentionDays] = useState(30);
  const [retentionPreview, setRetentionPreview] = useState<any>(null);
  const [purgeConfirmToken, setPurgeConfirmToken] = useState("");
  const [purgeLoading, setPurgeLoading] = useState(false);
  const [purgeMessage, setPurgeMessage] = useState<string | null>(null);

  // Fetch Main Health Status
  const fetchDiagnostics = useCallback(async () => {
    try {
      setError(null);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/health/status", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (!res.ok) {
        throw new Error(`Diagnostics request failed (HTTP ${res.status})`);
      }
      const data = await res.json();
      setDiagnostics(data);
    } catch (err: any) {
      console.error("Health fetch error:", err);
      setError(err.message || "Failed to load system health diagnostics");
    }
  }, [getAccessToken]);

  // Fetch Durable Incidents
  const fetchIncidents = useCallback(async () => {
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/health/incidents?limit=50", {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.ok) {
        const data = await res.json();
        setIncidents(data.incidents || []);
        setIncidentsCount(data.total_count || 0);
      }
    } catch (err) {
      console.error("Incidents fetch error:", err);
    }
  }, [getAccessToken]);

  // Fetch Logs Explorer
  const fetchLogs = useCallback(async () => {
    setLogLoading(true);
    try {
      const token = await getAccessToken();
      const params = new URLSearchParams();
      if (logFilterSeverity) params.set("severity", logFilterSeverity);
      if (logFilterCallId) params.set("call_id", logFilterCallId);
      if (logFilterReqId) params.set("request_id", logFilterReqId);
      params.set("limit", "50");

      const res = await fetch(`/api/v1/health/logs?${params.toString()}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.ok) {
        const data = await res.json();
        setLogs(data.events || []);
      }
    } catch (err) {
      console.error("Logs fetch error:", err);
    } finally {
      setLogLoading(false);
    }
  }, [getAccessToken, logFilterSeverity, logFilterCallId, logFilterReqId]);

  // Fetch Retention Preview
  const fetchRetentionPreview = useCallback(async () => {
    try {
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/health/retention/preview?days=${retentionDays}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.ok) {
        const data = await res.json();
        setRetentionPreview(data);
      }
    } catch (err) {
      console.error("Retention preview error:", err);
    }
  }, [getAccessToken, retentionDays]);

  // Initial Load
  useEffect(() => {
    if (isSuperuser) {
      Promise.all([
        fetchDiagnostics(),
        fetchIncidents(),
        fetchLogs(),
        fetchRetentionPreview(),
      ]).finally(() => setLoading(false));
    }
  }, [isSuperuser, fetchDiagnostics, fetchIncidents, fetchLogs, fetchRetentionPreview]);

  // Manual Refresh
  const handleRefresh = async () => {
    setRefreshing(true);
    await Promise.all([
      fetchDiagnostics(),
      fetchIncidents(),
      fetchLogs(),
      fetchRetentionPreview(),
    ]);
    setRefreshing(false);
  };

  // Acknowledge Incident Action
  const handleAcknowledge = async (incidentId: string) => {
    try {
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/health/alerts/${incidentId}/acknowledge`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (res.ok) {
        await Promise.all([fetchDiagnostics(), fetchIncidents()]);
      }
    } catch (err) {
      console.error("Acknowledge error:", err);
    }
  };

  // Lookup Call Timeline
  const handleLookupCall = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!lookupCallId.trim()) return;
    setTimelineLoading(true);
    setTimelineError(null);
    setCallTimeline(null);

    try {
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/health/calls/${encodeURIComponent(lookupCallId.trim())}/timeline`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });
      if (!res.ok) {
        if (res.status === 404) {
          throw new Error(`Call '${lookupCallId}' was not found in operational records.`);
        }
        throw new Error(`Timeline request failed (HTTP ${res.status})`);
      }
      const data = await res.json();
      setCallTimeline(data);
    } catch (err: any) {
      setTimelineError(err.message || "Failed to load call timeline");
    } finally {
      setTimelineLoading(false);
    }
  };

  // Execute Retention Purge
  const handleExecutePurge = async () => {
    setPurgeLoading(true);
    setPurgeMessage(null);
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/health/retention/purge", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          days: retentionDays,
          confirm_token: purgeConfirmToken,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Purge request rejected");
      }
      setPurgeMessage(`Successfully purged ${data.purged_operational_records} operational events older than ${data.retention_days} days.`);
      setPurgeConfirmToken("");
      await fetchRetentionPreview();
    } catch (err: any) {
      setPurgeMessage(`Error: ${err.message}`);
    } finally {
      setPurgeLoading(false);
    }
  };

  // Export Events (JSON or CSV)
  const handleExport = async (format: "json" | "csv") => {
    try {
      const token = await getAccessToken();
      const res = await fetch("/api/v1/health/export", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ format }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `spx_voice_audit_export.${format}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      }
    } catch (err) {
      console.error("Export error:", err);
    }
  };

  // Superadmin Access Gate
  if (!user || !isSuperuser) {
    return (
      <div className="container mx-auto p-6 max-w-lg min-h-[60vh] flex items-center justify-center">
        <Card className="w-full p-8 text-center space-y-4 border-amber-500/30 shadow-lg">
          <div className="w-12 h-12 rounded-full bg-amber-500/10 text-amber-600 flex items-center justify-center mx-auto">
            <ShieldAlert className="h-6 w-6" />
          </div>
          <CardTitle className="text-xl">Superadministrator Access Required</CardTitle>
          <CardDescription className="text-sm text-muted-foreground">
            The System Health & Audit Center requires elevated platform privileges.
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

  // Loading Screen
  if (loading) {
    return (
      <div className="container mx-auto p-6 max-w-6xl min-h-[50vh] flex flex-col items-center justify-center space-y-4">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">Connecting to platform diagnostic subsystem...</p>
      </div>
    );
  }

  const overallStatus = diagnostics?.overall_status || "UNKNOWN";
  const hostMetrics = diagnostics?.host_metrics;
  const components = diagnostics?.components || {};
  const providers = diagnostics?.providers || {};
  const activeAlerts = diagnostics?.active_alerts || [];
  const notifications = diagnostics?.external_notifications || {};

  const isVobizReady = providers?.vobiz?.status === "HEALTHY";
  const isLiveKitReady = components?.livekit?.status === "HEALTHY" || providers?.livekit_sip?.status === "HEALTHY";
  const isTelephonyConfigured = Boolean(
    (providers?.vobiz?.status && providers.vobiz.status !== "NOT_CONFIGURED") ||
    (providers?.voicelink?.status && providers.voicelink.status !== "NOT_CONFIGURED")
  );
  const isLiveKitConfigured = Boolean(
    (components?.livekit?.status && components.livekit.status !== "NOT_CONFIGURED") ||
    (providers?.livekit_sip?.status && providers.livekit_sip.status !== "NOT_CONFIGURED")
  );

  const callingReadinessStatus = (isVobizReady && isLiveKitReady)
    ? "READY"
    : (!isTelephonyConfigured || !isLiveKitConfigured)
    ? "NOT CONFIGURED"
    : "DEGRADED";

  return (
    <main className="container mx-auto p-4 md:p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-4">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight">System Health & Audit Center</h1>
            <Badge
              variant={
                overallStatus === "HEALTHY"
                  ? "default"
                  : overallStatus === "DEGRADED"
                  ? "secondary"
                  : "destructive"
              }
              className="text-xs uppercase px-2.5 py-0.5"
            >
              INFRASTRUCTURE: {overallStatus}
            </Badge>
            <Badge
              variant="outline"
              className={`text-xs uppercase px-2.5 py-0.5 font-medium ${
                callingReadinessStatus === "READY"
                  ? "bg-emerald-500/10 text-emerald-600 border-emerald-500/30"
                  : callingReadinessStatus === "NOT CONFIGURED"
                  ? "bg-amber-500/10 text-amber-600 border-amber-500/30"
                  : "bg-red-500/10 text-red-600 border-red-500/30"
              }`}
            >
              CALLING: {callingReadinessStatus}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Real-time platform observability, host telemetry, telephony health, and durable audit logs
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={refreshing}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Link href="/superadmin">
            <Button variant="secondary" size="sm">Superadmin Hub</Button>
          </Link>
        </div>
      </div>

      {/* Critical Alert Warning if webhook unconfigured */}
      {notifications?.status?.webhook_configured === false && (
        <div className="bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400 p-3 rounded-lg text-sm flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0" />
          <div className="flex-1">
            <span className="font-semibold">External Alerts Unconfigured:</span> SPX_ALERT_WEBHOOK_URL is not set. Incidents are logged durably on-disk and in PostgreSQL, but external push notifications (Slack/Discord/PagerDuty) are disabled.
          </div>
        </div>
      )}

      {/* Main Functional Tabs */}
      <Tabs defaultValue="overview" className="space-y-6">
        <TabsList className="grid grid-cols-3 md:grid-cols-5 lg:grid-cols-9 h-auto p-1 gap-1">
          <TabsTrigger value="overview" className="text-xs py-2">Overview</TabsTrigger>
          <TabsTrigger value="hosting" className="text-xs py-2">Hosting</TabsTrigger>
          <TabsTrigger value="telephony" className="text-xs py-2">Telephony</TabsTrigger>
          <TabsTrigger value="airuntime" className="text-xs py-2">AI Runtime</TabsTrigger>
          <TabsTrigger value="workflows" className="text-xs py-2">Workflows</TabsTrigger>
          <TabsTrigger value="billing" className="text-xs py-2">Billing</TabsTrigger>
          <TabsTrigger value="audit" className="text-xs py-2">Audit</TabsTrigger>
          <TabsTrigger value="incidents" className="text-xs py-2">
            Incidents {activeAlerts.length > 0 && `(${activeAlerts.length})`}
          </TabsTrigger>
          <TabsTrigger value="retention" className="text-xs py-2">Retention</TabsTrigger>
        </TabsList>

        {/* 1. System Overview */}
        <TabsContent value="overview" className="space-y-6">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Core Liveness</CardDescription>
                <CardTitle className="text-xl flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  {diagnostics?.liveness ? "Active" : "Down"}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                FastAPI event loop responsive
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Data Plane Readiness</CardDescription>
                <CardTitle className="text-xl flex items-center gap-2">
                  {diagnostics?.readiness ? (
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  ) : (
                    <XCircle className="h-5 w-5 text-red-500" />
                  )}
                  {diagnostics?.readiness ? "Ready" : "Degraded"}
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                PostgreSQL & Redis query verified
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Calling Readiness</CardDescription>
                <CardTitle className="text-lg flex items-center gap-2">
                  {callingReadinessStatus === "READY" ? (
                    <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  ) : callingReadinessStatus === "NOT CONFIGURED" ? (
                    <AlertTriangle className="h-5 w-5 text-amber-500" />
                  ) : (
                    <XCircle className="h-5 w-5 text-red-500" />
                  )}
                  <span className="text-sm font-semibold truncate">{callingReadinessStatus}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                {callingReadinessStatus === "READY"
                  ? "Carrier trunk & LiveKit active"
                  : "Trunk credentials not provisioned"}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Active Alerts</CardDescription>
                <CardTitle className="text-xl flex items-center gap-2">
                  {activeAlerts.length === 0 ? (
                    <Shield className="h-5 w-5 text-emerald-500" />
                  ) : (
                    <AlertTriangle className="h-5 w-5 text-amber-500" />
                  )}
                  {activeAlerts.length} Fired
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                {activeAlerts.length === 0 ? "No active failure states" : "Action required"}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Historical Incidents</CardDescription>
                <CardTitle className="text-xl flex items-center gap-2">
                  <Clock className="h-5 w-5 text-muted-foreground" />
                  {incidentsCount} Recorded
                </CardTitle>
              </CardHeader>
              <CardContent className="text-xs text-muted-foreground">
                Durable append-only event store
              </CardContent>
            </Card>
          </div>

          {/* Core Dependencies Table */}
          <Card>
            <CardHeader>
              <CardTitle>Infrastructure Subsystems</CardTitle>
              <CardDescription>Direct status of local services, databases, and voice engines</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Subsystem</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Latency</TableHead>
                    <TableHead>Diagnostic Message</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(components).map(([name, comp]: [string, any]) => (
                    <TableRow key={name}>
                      <TableCell className="font-medium capitalize">{name.replace("_", " ")}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            comp.status === "HEALTHY"
                              ? "default"
                              : comp.status === "DEGRADED"
                              ? "secondary"
                              : "destructive"
                          }
                          className="text-xs"
                        >
                          {comp.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        {comp.latency_ms !== null && comp.latency_ms !== undefined
                          ? `${comp.latency_ms} ms`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {comp.message || "Operational"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 2. Hosting & Infrastructure */}
        <TabsContent value="hosting" className="space-y-6">
          {/* Scope Identification Banner */}
          <div className="p-4 rounded-lg border bg-card space-y-2">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-primary" />
              <h3 className="font-semibold">Execution Environment Scope</h3>
            </div>
            <div className="grid gap-2 md:grid-cols-3 text-sm pt-2">
              <div>
                <span className="text-muted-foreground">Scope Tag:</span>{" "}
                <Badge variant="outline" className="font-mono text-xs">
                  {hostMetrics?.metrics_scope || "container_linux"}
                </Badge>
              </div>
              <div>
                <span className="text-muted-foreground">Environment:</span>{" "}
                <span className="font-medium">{hostMetrics?.environment || "local"}</span>
              </div>
              <div>
                <span className="text-muted-foreground">Platform Kernel:</span>{" "}
                <span className="font-mono text-xs">{hostMetrics?.node_platform || "Linux"}</span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground pt-1 border-t mt-2">
              <strong>Telemetry Scope:</strong> {hostMetrics?.scope_description || "Measurements reflect the visible container and virtual machine runtime environment. Hostinger production metrics apply when deployed to VPS."}
            </p>
          </div>

          {/* Host Resources Gauges */}
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription className="flex items-center gap-1.5">
                  <Cpu className="h-4 w-4" /> CPU Utilization
                </CardDescription>
                <CardTitle className="text-2xl">{hostMetrics?.cpu_percent ?? 0}%</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="w-full bg-secondary h-2 rounded-full overflow-hidden">
                  <div
                    className="bg-primary h-full transition-all"
                    style={{ width: `${Math.min(100, hostMetrics?.cpu_percent ?? 0)}%` }}
                  />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription className="flex items-center gap-1.5">
                  <HardDrive className="h-4 w-4" /> Memory Utilization
                </CardDescription>
                <CardTitle className="text-2xl">{hostMetrics?.memory_percent ?? 0}%</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <div className="w-full bg-secondary h-2 rounded-full overflow-hidden">
                  <div
                    className="bg-primary h-full transition-all"
                    style={{ width: `${Math.min(100, hostMetrics?.memory_percent ?? 0)}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground pt-1">
                  Available: {hostMetrics?.memory_available_mb ?? 0} MB / {hostMetrics?.memory_total_mb ?? 0} MB
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardDescription className="flex items-center gap-1.5">
                  <Database className="h-4 w-4" /> Disk Utilization
                </CardDescription>
                <CardTitle className="text-2xl">{hostMetrics?.disk_percent ?? 0}%</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1">
                <div className="w-full bg-secondary h-2 rounded-full overflow-hidden">
                  <div
                    className={`h-full transition-all ${
                      (hostMetrics?.disk_percent ?? 0) >= 90
                        ? "bg-red-500"
                        : (hostMetrics?.disk_percent ?? 0) >= 80
                        ? "bg-amber-500"
                        : "bg-primary"
                    }`}
                    style={{ width: `${Math.min(100, hostMetrics?.disk_percent ?? 0)}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground pt-1">
                  Free: {hostMetrics?.disk_free_gb ?? 0} GB / {hostMetrics?.disk_total_gb ?? 0} GB
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* 3. Telephony & SIP */}
        <TabsContent value="telephony" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Carrier & SIP Connectivity</CardTitle>
              <CardDescription>LiveKit SIP trunks, Vobiz integration, and Smart DID pools</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="p-4 rounded-lg border space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm">Vobiz Carrier Integration</span>
                    <Badge variant={providers?.vobiz?.status === "HEALTHY" ? "default" : "secondary"}>
                      {providers?.vobiz?.status || "NOT_CONFIGURED"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {providers?.vobiz?.message || "Inbound/Outbound PSTN carrier trunking for India DIDs"}
                  </p>
                </div>

                <div className="p-4 rounded-lg border space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm">LiveKit SIP Inbound Trunk</span>
                    <Badge variant={components?.livekit?.status === "HEALTHY" ? "default" : "secondary"}>
                      {components?.livekit?.status || "NOT_CONFIGURED"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {components?.livekit?.message || "Direct SIP participant bridge to LiveKit agents"}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 4. AI Runtime */}
        <TabsContent value="airuntime" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Voice AI Runtime Providers</CardTitle>
              <CardDescription>Realtime Gemini Live and fallback pipeline models</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Provider</TableHead>
                    <TableHead>Role</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(providers).map(([pName, p]: [string, any]) => (
                    <TableRow key={pName}>
                      <TableCell className="font-medium capitalize">{pName.replace("_", " ")}</TableCell>
                      <TableCell className="text-xs text-muted-foreground capitalize">
                        {p.details?.type || "Voice Pipeline"}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            p.status === "HEALTHY"
                              ? "default"
                              : p.status === "NOT_CONFIGURED"
                              ? "outline"
                              : "secondary"
                          }
                          className="text-xs"
                        >
                          {p.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {p.message}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 5. Business Workflows & Call Inspector */}
        <TabsContent value="workflows" className="space-y-6">
          {/* Call Timeline Inspector */}
          <Card>
            <CardHeader>
              <CardTitle>Complete Call Diagnostic Timeline</CardTitle>
              <CardDescription>
                Correlate carrier SIP events, LiveKit lifecycle, voice latency, and billing for any Call ID or Run ID
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <form onSubmit={handleLookupCall} className="flex gap-2">
                <Input
                  placeholder="Enter Call ID, SIP Session ID, or Workflow Run ID"
                  value={lookupCallId}
                  onChange={(e) => setLookupCallId(e.target.value)}
                  className="max-w-md"
                />
                <Button type="submit" disabled={timelineLoading}>
                  {timelineLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  ) : (
                    <Search className="h-4 w-4 mr-2" />
                  )}
                  Inspect Call
                </Button>
              </form>

              {timelineError && (
                <div className="p-3 bg-red-500/10 border border-red-500/30 text-red-600 rounded-lg text-sm">
                  {timelineError}
                </div>
              )}

              {callTimeline && (
                <div className="space-y-4 pt-2">
                  <div className="flex items-center justify-between border-b pb-2">
                    <div>
                      <h4 className="font-semibold text-base">Run #{callTimeline.workflow_run_id} ({callTimeline.call_id})</h4>
                      <p className="text-xs text-muted-foreground">Organization ID: {callTimeline.organization_id || "Platform"}</p>
                    </div>
                    <Badge variant={callTimeline.billing_reconciliation_status === "RECONCILED" ? "default" : "secondary"}>
                      Billing: {callTimeline.billing_reconciliation_status}
                    </Badge>
                  </div>

                  <div className="space-y-3">
                    {callTimeline.timeline?.map((ev: any, idx: number) => (
                      <div key={idx} className="p-3 rounded-lg border bg-muted/40 space-y-1">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-sm">{ev.title}</span>
                          <span className="text-xs font-mono text-muted-foreground">{ev.timestamp}</span>
                        </div>
                        <div className="grid gap-2 md:grid-cols-3 text-xs pt-1 text-muted-foreground">
                          {Object.entries(ev.details || {}).map(([k, v]: [string, any]) => (
                            <div key={k}>
                              <span className="capitalize">{k.replace(/_/g, " ")}:</span>{" "}
                              <span className="font-mono text-foreground">
                                {typeof v === "object" ? JSON.stringify(v) : String(v)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* 6. Billing & Reconciliation */}
        <TabsContent value="billing" className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Carrier Billing & Reconciliation</CardTitle>
                <CardDescription>Verified CDR matching vs pulse estimates for Indian voice traffic</CardDescription>
              </div>
              <Link href="/superadmin/costs">
                <Button variant="outline" size="sm">
                  Cost Analytics <ArrowRight className="h-4 w-4 ml-1.5" />
                </Button>
              </Link>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="p-4 rounded-lg border space-y-2">
                  <h4 className="font-semibold text-sm">Rate Card Configuration</h4>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div>Inbound Voice Pulse: <span className="font-mono font-medium text-foreground">₹0.30 / 60s pulse</span></div>
                    <div>Outbound Voice Pulse: <span className="font-mono font-medium text-foreground">₹0.40 / 60s pulse</span></div>
                    <div>Unanswered Calls: <span className="font-mono font-medium text-foreground">₹0.00 (Zero Charge)</span></div>
                  </div>
                </div>

                <div className="p-4 rounded-lg border space-y-2">
                  <h4 className="font-semibold text-sm">CDR Reconciliation Idempotency</h4>
                  <p className="text-xs text-muted-foreground">
                    Authoritative CDR reconciliation enforces single final charge per carrier call ID using PostgreSQL action idempotency. Replayed webhooks return existing charges without double-billing.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 7. Security & Administrative Audit */}
        <TabsContent value="audit" className="space-y-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Centralized Log Explorer & Audit Trail</CardTitle>
                <CardDescription>Durable PostgreSQL audit events, security actions, and C1 correlation IDs</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => handleExport("csv")}>
                  <Download className="h-4 w-4 mr-1.5" /> Export CSV
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleExport("json")}>
                  <Download className="h-4 w-4 mr-1.5" /> Export JSON
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Search Filters */}
              <div className="flex flex-wrap gap-2">
                <Input
                  placeholder="Filter by Call ID"
                  value={logFilterCallId}
                  onChange={(e) => setLogFilterCallId(e.target.value)}
                  className="max-w-[200px] h-9 text-xs"
                />
                <Input
                  placeholder="Filter by Request ID"
                  value={logFilterReqId}
                  onChange={(e) => setLogFilterReqId(e.target.value)}
                  className="max-w-[200px] h-9 text-xs"
                />
                <Button size="sm" variant="secondary" onClick={fetchLogs} disabled={logLoading}>
                  {logLoading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Search className="h-4 w-4 mr-1" />}
                  Apply Filter
                </Button>
              </div>

              {/* Logs Table */}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Time</TableHead>
                    <TableHead>Event Type</TableHead>
                    <TableHead>Component</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Request ID</TableHead>
                    <TableHead>Details</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {logs.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-6">
                        No matching audit or operational log events found.
                      </TableCell>
                    </TableRow>
                  ) : (
                    logs.map((ev: any) => (
                      <TableRow key={ev.id}>
                        <TableCell className="text-xs font-mono whitespace-nowrap">{ev.timestamp}</TableCell>
                        <TableCell className="text-xs font-medium">{ev.event_type}</TableCell>
                        <TableCell className="text-xs uppercase">{ev.component}</TableCell>
                        <TableCell>
                          <Badge variant={ev.status === "SUCCESS" || ev.status === "RESOLVED" ? "default" : "secondary"} className="text-xs">
                            {ev.status}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs font-mono text-muted-foreground">
                          {ev.request_id || "untracked"}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground max-w-md truncate">
                          {ev.message}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 8. Incidents & Alerts */}
        <TabsContent value="incidents" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Active Incidents & Alerts</CardTitle>
              <CardDescription>Ongoing failure conditions detected by autonomous health monitoring</CardDescription>
            </CardHeader>
            <CardContent>
              {activeAlerts.length === 0 ? (
                <div className="p-6 text-center text-sm text-muted-foreground">
                  <CheckCircle2 className="h-8 w-8 text-emerald-500 mx-auto mb-2" />
                  All infrastructure and provider dependencies are operating normally.
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Component</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Message</TableHead>
                      <TableHead>Occurrences</TableHead>
                      <TableHead>Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {activeAlerts.map((inc: any) => (
                      <TableRow key={inc.incident_id}>
                        <TableCell className="font-semibold capitalize">{inc.component}</TableCell>
                        <TableCell>
                          <Badge variant={inc.severity === "CRITICAL" ? "destructive" : "secondary"}>
                            {inc.severity}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm">{inc.message}</TableCell>
                        <TableCell className="text-sm font-mono">{inc.count}</TableCell>
                        <TableCell>
                          {inc.acknowledged ? (
                            <Badge variant="outline">Acknowledged</Badge>
                          ) : (
                            <Button size="sm" variant="outline" onClick={() => handleAcknowledge(inc.incident_id)}>
                              Acknowledge
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>

          {/* Durable Incident History */}
          <Card>
            <CardHeader>
              <CardTitle>Durable Incident History</CardTitle>
              <CardDescription>Persistent event history surviving process and container restarts</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Component</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>First Seen</TableHead>
                    <TableHead>Resolved At</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {incidents.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-center text-sm text-muted-foreground py-4">
                        No durable incidents recorded yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    incidents.map((inc: any) => {
                      const isSynthetic = Boolean(
                        inc.is_synthetic ||
                        inc.component?.startsWith("synthetic_") ||
                        inc.component?.startsWith("test_") ||
                        inc.incident_id?.startsWith("test_")
                      );
                      const isResolved = Boolean(inc.resolved_at);
                      return (
                        <TableRow key={inc.incident_id}>
                          <TableCell className="font-medium">
                            <span className="capitalize">{inc.component}</span>
                            {isSynthetic && (
                              <Badge variant="outline" className="ml-2 text-[10px] text-amber-600 dark:text-amber-400 border-amber-500/30">
                                SYNTHETIC PROBE
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell>
                            <Badge variant={!isSynthetic && inc.severity === "CRITICAL" ? "destructive" : "secondary"}>
                              {inc.severity}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs font-mono">{inc.first_seen}</TableCell>
                          <TableCell className="text-xs font-mono">
                            {inc.resolved_at ? inc.resolved_at : isSynthetic ? "— (Synthetic Probe)" : "Still Active"}
                          </TableCell>
                          <TableCell>
                            {isSynthetic ? (
                              <Badge variant="outline" className="text-xs border-muted-foreground/30 text-muted-foreground">
                                TEST VERIFIED
                              </Badge>
                            ) : isResolved ? (
                              <Badge variant="default" className="bg-emerald-600 hover:bg-emerald-600">
                                RESOLVED
                              </Badge>
                            ) : inc.lifecycle_status === "RECOVERED" ? (
                              <Badge variant="secondary">
                                RECOVERED
                              </Badge>
                            ) : (
                              <Badge variant="destructive">
                                ACTIVE
                              </Badge>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* 9. Retention & Export Management */}
        <TabsContent value="retention" className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Log Retention & Safe Purge Controls</CardTitle>
              <CardDescription>Preview and prune eligible operational records without deleting protected audit trails</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-4 rounded-lg border bg-muted/40 space-y-2">
                <div className="flex items-center gap-2">
                  <Shield className="h-5 w-5 text-emerald-600" />
                  <h4 className="font-semibold text-sm">Protected Records Guarantee</h4>
                </div>
                <p className="text-xs text-muted-foreground">
                  Security audit logs (<code>resource_type='audit'</code>), carrier billing CDR evidence, customer transcripts, call recordings, and database backups are <strong>strictly excluded</strong> from operational log purges.
                </p>
              </div>

              {retentionPreview && (
                <div className="grid gap-4 md:grid-cols-3 text-sm pt-2">
                  <div className="p-3 border rounded-lg">
                    <span className="text-muted-foreground">Eligible Operational Records:</span>{" "}
                    <span className="font-bold text-lg">{retentionPreview.eligible_operational_records}</span>
                    <p className="text-xs text-muted-foreground mt-1">Older than {retentionPreview.retention_days} days</p>
                  </div>
                  <div className="p-3 border rounded-lg bg-emerald-500/5 border-emerald-500/20">
                    <span className="text-muted-foreground">Total Protected Records in DB:</span>{" "}
                    <span className="font-bold text-lg text-emerald-600">
                      {retentionPreview.total_protected_records ?? retentionPreview.protected_audit_records_excluded}
                    </span>
                    <p className="text-xs text-muted-foreground mt-1">Audits, leads, appointments & calls (all-time)</p>
                  </div>
                  <div className="p-3 border rounded-lg bg-muted/40">
                    <span className="text-muted-foreground">Protected Records &gt; {retentionPreview.retention_days}d:</span>{" "}
                    <span className="font-bold text-lg">{retentionPreview.protected_audit_records_excluded}</span>
                    <p className="text-xs text-muted-foreground mt-1">Excluded from purge</p>
                  </div>
                </div>
              )}

              <div className="pt-4 border-t space-y-3">
                <Label htmlFor="purgeToken" className="text-sm font-semibold">Execute Controlled Operational Purge</Label>
                <p className="text-xs text-muted-foreground">
                  To purge eligible operational records older than {retentionDays} days, type <code>CONFIRM_PURGE_OPERATIONAL_LOGS</code>:
                </p>
                <div className="flex gap-2 max-w-md">
                  <Input
                    id="purgeToken"
                    placeholder="CONFIRM_PURGE_OPERATIONAL_LOGS"
                    value={purgeConfirmToken}
                    onChange={(e) => setPurgeConfirmToken(e.target.value)}
                  />
                  <Button
                    variant="destructive"
                    disabled={purgeConfirmToken !== "CONFIRM_PURGE_OPERATIONAL_LOGS" || purgeLoading}
                    onClick={handleExecutePurge}
                  >
                    {purgeLoading ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Trash2 className="h-4 w-4 mr-1" />}
                    Purge
                  </Button>
                </div>
                {purgeMessage && (
                  <p className="text-xs font-semibold text-primary pt-1">{purgeMessage}</p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </main>
  );
}

