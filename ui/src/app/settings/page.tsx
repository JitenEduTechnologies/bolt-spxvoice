"use client";

import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Brain,
  Building2,
  Check,
  CheckCircle2,
  Cpu,
  Phone,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import React, { useEffect, useState } from "react";
import { toast } from "sonner";

import { ProviderConnectionCenter } from "@/components/telephony/ProviderConnectionCenter";
import { MCPSection } from "@/components/MCPSection";
import { TelemetrySection } from "@/components/TelemetrySection";
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
import { useAuth } from "@/lib/auth";

export default function SettingsPage() {
  const { getAccessToken, loading: authLoading } = useAuth();
  const [presets, setPresets] = useState<any[]>([]);
  const [activeProfile, setActiveProfile] = useState<any>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string>("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [applying, setApplying] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    async function loadData() {
      try {
        const token = await getAccessToken();
        const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};

        // 1. Fetch presets
        const presetsRes = await fetch("/api/v1/business/presets");
        if (presetsRes.ok) {
          const pData = await presetsRes.json();
          setPresets(pData || []);
        }

        // 2. Fetch current agent training
        const agentRes = await fetch("/api/v1/business/agent", { headers });
        if (agentRes.ok) {
          const aData = await agentRes.json();
          const currentTraining = aData.training_data || aData.training || null;
          setActiveProfile(currentTraining);
          if (currentTraining?.id) {
            setSelectedPresetId(currentTraining.id);
          }
        }
      } catch (e) {
        console.error("Failed to load settings data:", e);
      }
    }
    if (!authLoading) {
      loadData();
    }
  }, [authLoading, getAccessToken]);

  const selectedPreset = presets.find((p) => p.id === selectedPresetId) || presets[0];

  const handleApplyPreset = async () => {
    if (!selectedPreset) return;
    try {
      setApplying(true);
      const token = await getAccessToken();
      const headers: HeadersInit = {
        "Content-Type": "application/json",
      };
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }
      const res = await fetch("/api/v1/business/agent/draft", {
        method: "PUT",
        headers,
        body: JSON.stringify({ training_data: selectedPreset }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to update industry template draft");
      }

      setActiveProfile(selectedPreset);
      toast.success(
        `Updated industry profile to "${selectedPreset.name}". Open Train Agent to customize.`
      );
      setDialogOpen(false);
      setConfirmed(false);
    } catch (e: any) {
      toast.error(e.message || "Failed to apply industry template");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="container mx-auto px-4 py-8 space-y-8 max-w-4xl animate-fade-in-up">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <div className="h-1.5 w-8 bg-gradient-to-r from-blue-500 to-indigo-500 rounded-full" />
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Configuration</span>
        </div>
        <h1 className="text-3xl font-bold tracking-tight">Platform Settings</h1>
        <p className="text-muted-foreground mt-1">
          Manage your business industry profile, telephony carriers (VoiceLink / Vobiz), real-time voice engines, and observability.
        </p>
      </div>

      <div className="grid gap-6">
        {/* Business Industry Profile Card */}
        <Card className="card-hover overflow-hidden border-primary/20 bg-gradient-to-r from-primary/5 via-card to-card">
          <CardHeader className="pb-4 relative z-10">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
              <div className="flex items-start sm:items-center gap-3">
                <div className="bg-gradient-to-br from-blue-600 to-indigo-600 p-2.5 rounded-xl shadow-md text-white shrink-0">
                  <Building2 className="h-5 w-5" />
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle className="text-lg">Business Industry Profile</CardTitle>
                    <Badge variant="outline" className="text-xs bg-primary/10 text-primary border-primary/20 font-semibold px-2.5 py-0.5">
                      {activeProfile?.name || "Standard Business Assistant"}
                    </Badge>
                    {activeProfile?.industry && (
                      <Badge variant="secondary" className="text-[10px] font-medium">
                        {activeProfile.industry}
                      </Badge>
                    )}
                  </div>
                  <CardDescription className="text-xs mt-1">
                    {activeProfile?.business_name ? `${activeProfile.business_name} • ` : ""}
                    Tailors conversation objectives, Indian language instructions, objection handling, and appointment booking.
                  </CardDescription>
                </div>
              </div>

              <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                <DialogTrigger asChild>
                  <Button
                    size="sm"
                    variant="default"
                    className="gap-1.5 text-xs shrink-0 shadow-sm"
                    onClick={() => setDialogOpen(true)}
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    Change Industry Template
                  </Button>
                </DialogTrigger>
                <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                  <DialogHeader>
                    <DialogTitle className="flex items-center gap-2 text-lg">
                      <Building2 className="h-5 w-5 text-primary" />
                      Select Business Industry Profile
                    </DialogTitle>
                    <DialogDescription className="text-xs">
                      Switching industry templates equips your agent with specialized Indian conversation objectives, multilingual scripts, and qualification questions.
                    </DialogDescription>
                  </DialogHeader>

                  {/* Template Picker Grid */}
                  <div className="space-y-4 py-2">
                    <div>
                      <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                        Available Industry Presets
                      </span>
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-2">
                        {presets.map((p) => {
                          const isSel = selectedPresetId === p.id;
                          return (
                            <button
                              key={p.id}
                              type="button"
                              onClick={() => {
                                setSelectedPresetId(p.id);
                                setConfirmed(false);
                              }}
                              className={`p-2.5 rounded-xl border text-left transition-all ${
                                isSel
                                  ? "bg-primary text-primary-foreground border-primary shadow-sm ring-2 ring-primary/20"
                                  : "bg-card hover:bg-accent/60 border-border text-foreground"
                              }`}
                            >
                              <div className="flex items-center justify-between">
                                <p className="font-semibold text-xs line-clamp-1">{p.name}</p>
                                {isSel && <Check className="h-3 w-3 shrink-0" />}
                              </div>
                              <p
                                className={`text-[10px] mt-1 line-clamp-2 ${
                                  isSel ? "text-primary-foreground/80" : "text-muted-foreground"
                                }`}
                              >
                                {p.description}
                              </p>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Selected Template Preview */}
                    {selectedPreset && (
                      <div className="p-4 rounded-xl border bg-muted/40 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-xs text-foreground flex items-center gap-1.5">
                            <Sparkles className="h-3.5 w-3.5 text-primary" />
                            Template Preview: {selectedPreset.name}
                          </span>
                          <Badge variant="outline" className="text-[10px]">
                            {selectedPreset.industry}
                          </Badge>
                        </div>

                        <div className="space-y-2 text-xs">
                          <div>
                            <span className="font-medium text-muted-foreground">Sample Greeting:</span>
                            <p className="italic text-foreground mt-0.5">&ldquo;{selectedPreset.greeting}&rdquo;</p>
                          </div>
                          <div>
                            <span className="font-medium text-muted-foreground">Specialized Objectives:</span>
                            <ul className="list-disc list-inside mt-0.5 space-y-0.5 text-muted-foreground">
                              {(selectedPreset.conversation_objectives || []).slice(0, 3).map((obj: string, i: number) => (
                                <li key={i}>{obj}</li>
                              ))}
                            </ul>
                          </div>
                          <div>
                            <span className="font-medium text-muted-foreground">Qualification Fields:</span>
                            <div className="flex flex-wrap gap-1 mt-1">
                              {(selectedPreset.qualification_fields || []).map((f: any) => (
                                <Badge key={f.key} variant="secondary" className="text-[10px] px-1.5 py-0">
                                  {f.label}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Safety Notice Callout */}
                    <div className="p-3 rounded-lg border border-amber-500/20 bg-amber-500/10 text-amber-800 dark:text-amber-300 text-xs flex items-start gap-2.5">
                      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                      <p>
                        <strong>Safety Guarantee:</strong> Applying this template updates your AI Agent <strong>DRAFT</strong>. Existing customer leads, appointment records, and published live workflows remain completely intact.
                      </p>
                    </div>

                    {/* Explicit Confirmation Check */}
                    <label className="flex items-center gap-2 cursor-pointer pt-1 text-xs select-none">
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                        className="rounded border-border text-primary focus:ring-primary h-4 w-4"
                      />
                      <span>I understand this will update my agent draft template to the selected industry.</span>
                    </label>
                  </div>

                  <DialogFooter className="gap-2 sm:gap-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setDialogOpen(false);
                        setConfirmed(false);
                      }}
                      className="text-xs"
                    >
                      Cancel
                    </Button>
                    <Button
                      size="sm"
                      onClick={handleApplyPreset}
                      disabled={!confirmed || applying}
                      className="gap-1.5 text-xs"
                    >
                      {applying ? (
                        <>
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                          Applying...
                        </>
                      ) : (
                        <>
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Apply Template to Agent Draft
                        </>
                      )}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </CardHeader>
        </Card>

        {/* Provider Connection Center */}
        <ProviderConnectionCenter />

        {/* Telephony Settings Quick Card */}
        <Card className="card-hover overflow-hidden">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="bg-gradient-to-br from-purple-500 to-indigo-600 p-2.5 rounded-xl shadow-md text-white">
                  <Phone className="h-5 w-5" />
                </div>
                <div>
                  <CardTitle className="text-lg">Telephony Providers (VoiceLink & Vobiz)</CardTitle>
                  <CardDescription className="text-xs">
                    Configure VoiceLink primary Indian VNO SIP trunk, DLT registered numbers, capacity limits, and Vobiz backup.
                  </CardDescription>
                </div>
              </div>
              <Link href="/telephony-configurations">
                <Button size="sm" variant="outline" className="gap-1.5 text-xs">
                  Configure
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
          </CardHeader>
        </Card>

        {/* Voice Engines Quick Card */}
        <Card className="card-hover overflow-hidden">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="bg-gradient-to-br from-amber-500 to-orange-600 p-2.5 rounded-xl shadow-md text-white">
                  <Brain className="h-5 w-5" />
                </div>
                <div>
                  <CardTitle className="text-lg">Voice Engines (Gemini Live & Cartesia)</CardTitle>
                  <CardDescription className="text-xs">
                    Switch between Gemini Live native speech-to-speech (Standard) and Cartesia + DeepSeek (Premium).
                  </CardDescription>
                </div>
              </div>
              <Link href="/model-configurations">
                <Button size="sm" variant="outline" className="gap-1.5 text-xs">
                  Manage Engines
                  <ArrowRight className="h-3.5 w-3.5" />
                </Button>
              </Link>
            </div>
          </CardHeader>
        </Card>

        {/* MCP Server */}
        <Card className="card-hover overflow-hidden">
          <CardHeader className="pb-4">
            <div className="flex items-center gap-3">
              <div className="bg-gradient-to-br from-blue-500 to-cyan-500 p-2.5 rounded-xl shadow-md text-white">
                <Cpu className="h-5 w-5" />
              </div>
              <div>
                <CardTitle className="text-lg">MCP Server</CardTitle>
                <CardDescription className="text-xs">
                  Let AI assistants access this workspace and its operational context via the Model Context Protocol.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <MCPSection />
          </CardContent>
        </Card>

        {/* Telemetry */}
        <Card className="card-hover overflow-hidden">
          <CardHeader className="pb-4">
            <div className="flex items-center gap-3">
              <div className="bg-gradient-to-br from-emerald-500 to-teal-500 p-2.5 rounded-xl shadow-md text-white">
                <Activity className="h-5 w-5" />
              </div>
              <div>
                <CardTitle className="text-lg">Telemetry & Tracing</CardTitle>
                <CardDescription className="text-xs">
                  Configure Langfuse tracing for your voice agent calls.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <TelemetrySection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
