"use client";

import {
  AlertOctagon,
  AlertTriangle,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Clock,
  Layers,
  PhoneCall,
  RefreshCw,
  Save,
  Server,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Zap,
} from "lucide-react";
import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
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
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

interface RateTier {
  name: string;
  rate_per_min_inr: number;
  pulse_seconds: number;
}

interface CostData {
  selected_mode: string;
  rates: Record<string, RateTier>;
  total_calls: number;
  telecom_calls: number;
  not_connected_calls?: number;
  simulated_calls: number;
  total_minutes: number;
  telecom_minutes: number;
  simulated_minutes: number;
  actual_telecom_spend_inr: number;
  simulated_spend_inr: number;
  spend_is_estimate?: boolean;
  spend_basis?: string;
  accounting_status?: string;
  itemized_costs: {
    carrier_telephony: number;
    ai_engine: number;
    infrastructure: number;
  };
  budget: {
    daily_cap_inr: number;
    monthly_cap_inr: number;
    per_call_cap_inr: number;
    current_month_spend_inr: number;
    remaining_budget_inr: number;
    status: string;
    emergency_stop_active?: boolean;
  };
  cost_per_lead_inr: number;
  reconciliation_status: string;
}

export default function VoiceCostsDashboard() {
  const { user, loading: authLoading, getAccessToken } = useAuth();
  const [data, setData] = useState<CostData | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingBudget, setSavingBudget] = useState(false);
  const [stopping, setStopping] = useState(false);

  // Form budget state
  const [dailyCap, setDailyCap] = useState("500");
  const [monthlyCap, setMonthlyCap] = useState("10000");
  const [perCallCap, setPerCallCap] = useState("25");
  const [selectedTier, setSelectedTier] = useState("standard");
  const [emergencyReason, setEmergencyReason] = useState("");
  const [resuming, setResuming] = useState(false);

  const handleResume = async () => {
    try {
      setResuming(true);
      let token = await getAccessToken();
      const res = await fetch("/api/v1/business/emergency-stop/resume", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      });
      if (res.ok) {
        toast.success("Emergency Stop released. Outbound calling and dispatch resumed.");
        fetchCostData();
      } else {
        toast.error("Failed to release emergency stop.");
      }
    } catch (err: any) {
      toast.error(`Error: ${err.message || "Network error"}`);
    } finally {
      setResuming(false);
    }
  };

  const fetchCostData = async () => {
    try {
      setLoading(true);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/costs", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const json: CostData = await res.json();
        setData(json);
        if (json.budget) {
          setDailyCap(String(json.budget.daily_cap_inr));
          setMonthlyCap(String(json.budget.monthly_cap_inr));
          setPerCallCap(String(json.budget.per_call_cap_inr));
        }
        if (json.selected_mode) {
          setSelectedTier(json.selected_mode);
        }
      }
    } catch (e) {
      console.error("Error fetching cost data:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authLoading) return;
    fetchCostData();
  }, [authLoading, user]);

  const handleSaveBudget = async () => {
    try {
      setSavingBudget(true);
      let token = await getAccessToken();
      const res = await fetch("/api/v1/business/costs/budget", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          daily_cap_inr: parseFloat(dailyCap) || 500,
          monthly_cap_inr: parseFloat(monthlyCap) || 10000,
          per_call_cap_inr: parseFloat(perCallCap) || 25,
          tier_preference: selectedTier,
        }),
      });

      if (res.ok) {
        toast.success("Budget caps and calling tier updated successfully!");
        fetchCostData();
      } else {
        toast.error("Failed to update budget settings.");
      }
    } catch (err: any) {
      toast.error(`Error: ${err.message || "Network error"}`);
    } finally {
      setSavingBudget(false);
    }
  };

  const handleEmergencyStop = async () => {
    try {
      setStopping(true);
      let token = await getAccessToken();
      const res = await fetch("/api/v1/business/emergency-stop", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          reason: emergencyReason || "Activated by owner from Voice Cost Dashboard",
          pause_all_campaigns: true,
        }),
      });

      if (res.ok) {
        const json = await res.json();
        toast.success(
          `Emergency Stop activated: ${json.campaigns_paused} campaigns paused. Active calls protected.`
        );
        fetchCostData();
      } else {
        toast.error("Emergency stop failed. Please retry.");
      }
    } catch (err: any) {
      toast.error(`Error: ${err.message || "Network error"}`);
    } finally {
      setStopping(false);
      setEmergencyReason("");
    }
  };

  const spentPercent = data?.budget?.monthly_cap_inr
    ? Math.min(
        100,
        Math.round(
          (data.actual_telecom_spend_inr / data.budget.monthly_cap_inr) * 100
        )
      )
    : 0;

  return (
    <div className="container mx-auto px-4 py-8 space-y-8 max-w-7xl animate-fade-in-up">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <Badge
              variant="outline"
              className="bg-primary/10 text-primary border-primary/20 gap-1 px-3 py-1 font-semibold"
            >
              <CircleDollarSign className="h-3.5 w-3.5 text-emerald-500" />
              Owner Financial Governance
            </Badge>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Voice Costs & Budgeting</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Real-time Indian Rupee (INR) cost accounting, agreed 60-second pulse tariffs, budget caps, and emergency stop protection.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {data?.budget?.emergency_stop_active && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleResume}
              disabled={resuming}
              className="gap-2 border-emerald-500 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/20 font-bold"
            >
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              {resuming ? "Resuming..." : "Resume Outbound Calling"}
            </Button>
          )}

          {/* Emergency Stop Dialog */}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant={data?.budget?.emergency_stop_active ? "outline" : "destructive"}
                className="gap-2 font-bold shadow-md shadow-destructive/20"
                disabled={data?.budget?.emergency_stop_active}
              >
                <AlertOctagon className="h-4 w-4" />
                {data?.budget?.emergency_stop_active ? "Halt Active" : "Emergency Stop"}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle className="flex items-center gap-2 text-destructive">
                  <ShieldAlert className="h-5 w-5" />
                  Activate Emergency Campaign Stop?
                </AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="space-y-3 pt-2 text-sm text-muted-foreground">
                    <p>
                      This will immediately freeze all queued outbound dispatches and pause active campaigns across your organization.
                    </p>
                    <p className="font-medium text-foreground">
                      Ongoing connected calls will <strong>NOT</strong> be abruptly dropped, ensuring caller trust and DLT compliance.
                    </p>
                    <div className="space-y-1.5 pt-2">
                      <Label htmlFor="stop-reason" className="text-xs">
                        Reason for audit log (optional):
                      </Label>
                      <Input
                        id="stop-reason"
                        placeholder="e.g. Budget review, carrier maintenance"
                        value={emergencyReason}
                        onChange={(e) => setEmergencyReason(e.target.value)}
                      />
                    </div>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleEmergencyStop}
                  className="bg-destructive hover:bg-destructive/90 text-destructive-foreground"
                >
                  {stopping ? "Stopping..." : "Confirm Emergency Halt"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <Button
            onClick={fetchCostData}
            variant="outline"
            size="sm"
            className="gap-2"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Top Level Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Actual Telecom Spend */}
        <Card className="p-5 border-emerald-500/20 bg-gradient-to-br from-emerald-500/5 to-teal-500/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">
              {data?.spend_is_estimate === false ? "Telecom Spend (verified)" : "Telecom Spend (estimate)"}
            </span>
            <CircleDollarSign className="h-5 w-5 text-emerald-600" />
          </div>
          <div className="text-3xl font-extrabold mt-3 text-emerald-700 dark:text-emerald-400">
            {loading ? "..." : `₹${data?.actual_telecom_spend_inr?.toFixed(2) || "0.00"}`}
          </div>
          <p className="text-[11px] text-muted-foreground mt-1" title={data?.spend_basis}>
            {data?.spend_is_estimate === false
              ? "Carrier records verified"
              : "Tariff × call duration (60s pulses). Not reconciled with a carrier invoice."}
          </p>
        </Card>

        {/* Simulated Spend (Separated) */}
        <Card className="p-5 border-blue-500/20 bg-gradient-to-br from-blue-500/5 to-indigo-500/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-blue-700 dark:text-blue-400 uppercase tracking-wider">
              Simulated Spend
            </span>
            <Bot className="h-5 w-5 text-blue-600" />
          </div>
          <div className="text-3xl font-extrabold mt-3 text-blue-700 dark:text-blue-400">
            {loading ? "..." : `₹${data?.simulated_spend_inr?.toFixed(2) || "0.00"}`}
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">
            {data?.simulated_calls || 0} synthetic test runs (₹0 carrier fee)
          </p>
        </Card>

        {/* Billable Minutes */}
        <Card className="p-5 border-amber-500/20 bg-gradient-to-br from-amber-500/5 to-orange-500/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wider">
              Live Telecom Minutes
            </span>
            <Clock className="h-5 w-5 text-amber-600" />
          </div>
          <div className="text-3xl font-extrabold mt-3">
            {loading ? "..." : `${data?.telecom_minutes || 0.0} min`}
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">
            {data?.telecom_calls || 0} live carrier calls connected{data?.not_connected_calls ? ` · ${data.not_connected_calls} attempts never connected` : ""}
          </p>
        </Card>

        {/* Cost Per Lead */}
        <Card className="p-5 border-purple-500/20 bg-gradient-to-br from-purple-500/5 to-violet-500/5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-purple-700 dark:text-purple-400 uppercase tracking-wider">
              Cost Per Lead
            </span>
            <TrendingUp className="h-5 w-5 text-purple-600" />
          </div>
          <div className="text-3xl font-extrabold mt-3">
            {loading ? "..." : `₹${data?.cost_per_lead_inr?.toFixed(2) || "0.00"}`}
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">
            Telecom cost divided by qualified leads
          </p>
        </Card>
      </div>

      {/* 3 Calling Tiers Selection */}
      <Card>
        <CardHeader>
          <CardTitle className="text-xl font-bold flex items-center gap-2">
            <Layers className="h-5 w-5 text-primary" />
            3 Production Calling Tiers & Agreed Tariffs
          </CardTitle>
          <CardDescription>
            Select your preferred operating tier. All tiers are billed in transparent 60-second pulses with zero hidden markup.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Tier 1: Budget IVR */}
            <div
              onClick={() => setSelectedTier("budget")}
              className={`p-5 rounded-xl border-2 cursor-pointer transition-all ${
                selectedTier === "budget"
                  ? "border-primary bg-primary/5 shadow-md"
                  : "border-border hover:border-border/80"
              }`}
            >
              <div className="flex items-center justify-between">
                <Badge variant="outline" className="font-semibold text-xs">
                  Budget Tier
                </Badge>
                {selectedTier === "budget" && (
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                )}
              </div>
              <h3 className="font-bold text-lg mt-3">Budget IVR</h3>
              <p className="text-2xl font-black text-foreground mt-1">
                ₹0.85{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  / minute
                </span>
              </p>
              <p className="text-xs text-muted-foreground mt-2">
                DTMF + Traditional STT/TTS. Ideal for high-volume routine reminders, attendance, and basic verification.
              </p>
              <div className="mt-4 pt-3 border-t text-[11px] text-muted-foreground space-y-1">
                <div>• Pulse: 60 seconds</div>
                <div>• Voice: Standard Neural TTS</div>
                <div>• SLA: 99.9% uptime</div>
              </div>
            </div>

            {/* Tier 2: Standard Gemini Live */}
            <div
              onClick={() => setSelectedTier("standard")}
              className={`p-5 rounded-xl border-2 cursor-pointer transition-all relative ${
                selectedTier === "standard"
                  ? "border-primary bg-primary/5 shadow-md"
                  : "border-border hover:border-border/80"
              }`}
            >
              <div className="absolute -top-3 right-4 bg-emerald-600 text-white text-[10px] font-bold px-2 py-0.5 rounded-full shadow-sm">
                MOST POPULAR
              </div>
              <div className="flex items-center justify-between">
                <Badge variant="outline" className="font-semibold text-xs text-emerald-600 border-emerald-500/30">
                  Standard Tier
                </Badge>
                {selectedTier === "standard" && (
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                )}
              </div>
              <h3 className="font-bold text-lg mt-3">Gemini Live Realtime</h3>
              <p className="text-2xl font-black text-foreground mt-1">
                ₹1.80{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  / minute
                </span>
              </p>
              <p className="text-xs text-muted-foreground mt-2">
                Native speech-to-speech with Indian English & regional language support, ultra-low latency & natural interruptions.
              </p>
              <div className="mt-4 pt-3 border-t text-[11px] text-muted-foreground space-y-1">
                <div>• Pulse: 60 seconds</div>
                <div>• Engine: Gemini 2.0 / 3.1 Flash Live</div>
                <div>• Indian Languages: Hindi, Telugu, Tamil, etc.</div>
              </div>
            </div>

            {/* Tier 3: Premium DeepSeek + Cartesia */}
            <div
              onClick={() => setSelectedTier("premium")}
              className={`p-5 rounded-xl border-2 cursor-pointer transition-all ${
                selectedTier === "premium"
                  ? "border-primary bg-primary/5 shadow-md"
                  : "border-border hover:border-border/80"
              }`}
            >
              <div className="flex items-center justify-between">
                <Badge variant="outline" className="font-semibold text-xs text-purple-600 border-purple-500/30">
                  Premium Tier
                </Badge>
                {selectedTier === "premium" && (
                  <CheckCircle2 className="h-5 w-5 text-primary" />
                )}
              </div>
              <h3 className="font-bold text-lg mt-3">DeepSeek + Cartesia</h3>
              <p className="text-2xl font-black text-foreground mt-1">
                ₹3.20{" "}
                <span className="text-xs font-normal text-muted-foreground">
                  / minute
                </span>
              </p>
              <p className="text-xs text-muted-foreground mt-2">
                Hyper-realistic voice cloning with Cartesia Sonic and DeepSeek reasoning for high-stakes executive sales.
              </p>
              <div className="mt-4 pt-3 border-t text-[11px] text-muted-foreground space-y-1">
                <div>• Pulse: 60 seconds</div>
                <div>• Engine: DeepSeek-V3 + Cartesia Sonic</div>
                <div>• Voice: Studio cloned quality</div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Two Column Section: Budget Caps & Itemized Cost Split */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Budget Caps & Spending Controls */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-bold flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-amber-500" />
              Owner Budget Caps & Spending Thresholds
            </CardTitle>
            <CardDescription>
              Hard limits to safeguard against unexpected spikes or runaway campaigns. Calls exceeding caps are safely blocked.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {/* Progress Bar */}
            <div className="space-y-2">
              <div className="flex justify-between text-xs font-medium">
                <span>Monthly Budget Consumption</span>
                <span>{spentPercent}% Used</span>
              </div>
              <Progress value={spentPercent} className="h-2.5" />
              <div className="flex justify-between text-[11px] text-muted-foreground">
                <span>₹{data?.actual_telecom_spend_inr?.toFixed(2) || "0.00"} spent</span>
                <span>₹{data?.budget?.remaining_budget_inr?.toFixed(2) || "0.00"} remaining</span>
              </div>
            </div>

            {/* Inputs */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="monthly-cap" className="text-xs">
                  Monthly Cap (₹)
                </Label>
                <Input
                  id="monthly-cap"
                  type="number"
                  value={monthlyCap}
                  onChange={(e) => setMonthlyCap(e.target.value)}
                  className="font-mono text-sm"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="daily-cap" className="text-xs">
                  Daily Cap (₹)
                </Label>
                <Input
                  id="daily-cap"
                  type="number"
                  value={dailyCap}
                  onChange={(e) => setDailyCap(e.target.value)}
                  className="font-mono text-sm"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="per-call-cap" className="text-xs">
                  Per-Call Cap (₹)
                </Label>
                <Input
                  id="per-call-cap"
                  type="number"
                  value={perCallCap}
                  onChange={(e) => setPerCallCap(e.target.value)}
                  className="font-mono text-sm"
                />
              </div>
            </div>

            <Button
              onClick={handleSaveBudget}
              disabled={savingBudget}
              className="gap-2 w-full sm:w-auto"
            >
              <Save className="h-4 w-4" />
              {savingBudget ? "Saving..." : "Save Budget Settings"}
            </Button>
          </CardContent>
        </Card>

        {/* Itemized Margin & Cost Split */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg font-bold flex items-center gap-2">
              <Server className="h-5 w-5 text-blue-500" />
              Transparent Telecom Cost Split
            </CardTitle>
            <CardDescription>
              Itemized per-minute costs estimated from the platform tariff and call duration.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="divide-y border rounded-lg p-3 space-y-3">
              <div className="flex items-center justify-between pt-1">
                <div className="flex items-center gap-2.5">
                  <PhoneCall className="h-4 w-4 text-emerald-600" />
                  <div>
                    <div className="text-sm font-semibold">Carrier / SIP Telephony</div>
                    <div className="text-[11px] text-muted-foreground">VoiceLink / Vobiz Indian DLT PRI</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-bold text-sm">
                    ₹{data?.itemized_costs?.carrier_telephony?.toFixed(2) || "0.00"}
                  </div>
                  <div className="text-[10px] text-muted-foreground">₹0.70 / min</div>
                </div>
              </div>

              <div className="flex items-center justify-between pt-3">
                <div className="flex items-center gap-2.5">
                  <Zap className="h-4 w-4 text-amber-600" />
                  <div>
                    <div className="text-sm font-semibold">AI Voice Engine</div>
                    <div className="text-[11px] text-muted-foreground">Gemini Realtime Native Audio API</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-bold text-sm">
                    ₹{data?.itemized_costs?.ai_engine?.toFixed(2) || "0.00"}
                  </div>
                  <div className="text-[10px] text-muted-foreground">₹0.90 / min</div>
                </div>
              </div>

              <div className="flex items-center justify-between pt-3">
                <div className="flex items-center gap-2.5">
                  <Server className="h-4 w-4 text-indigo-600" />
                  <div>
                    <div className="text-sm font-semibold">Platform & WebRTC Infra</div>
                    <div className="text-[11px] text-muted-foreground">LiveKit Cloud / Self-hosted SFU</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-bold text-sm">
                    ₹{data?.itemized_costs?.infrastructure?.toFixed(2) || "0.00"}
                  </div>
                  <div className="text-[10px] text-muted-foreground">₹0.20 / min</div>
                </div>
              </div>
            </div>

            <div className="p-3 bg-muted/40 rounded-lg text-xs flex items-center justify-between text-muted-foreground">
              <span>Cost basis:</span>
              <Badge
                variant="outline"
                className={`text-[10px] font-mono ${
                  data?.accounting_status === "VERIFIED"
                    ? "text-emerald-600 border-emerald-500/20"
                    : "text-amber-600 border-amber-500/30"
                }`}
              >
                {data?.accounting_status || data?.reconciliation_status || "NOT RECONCILED"}
              </Badge>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

