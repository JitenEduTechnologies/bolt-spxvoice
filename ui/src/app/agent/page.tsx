"use client";

import {
  Activity,
  Settings,
  AlertCircle,
  ArrowRight,
  Bot,
  Building2,
  CheckCircle2,
  Clock,
  Globe,
  HelpCircle,
  History,
  Languages,
  Plus,
  RotateCcw,
  Save,
  Send,
  Sparkles,
  Tag,
  Trash2,
  Users,
  Volume2,
  PhoneCall,
  Eye,
  KeyRound,
  Mic,
  PhoneForwarded,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import React, { Suspense, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

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
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";

interface IndustryPreset {
  id: string;
  name: string;
  industry: string;
  description: string;
  business_name: string;
  location: string;
  working_hours: string;
  agent_name: string;
  agent_personality: string;
  greeting: string;
  closing_script: string;
  language_instructions: string;
  products_and_pricing: Array<{ name: string; price: string; description: string }>;
  conversation_objectives: string[];
  qualification_fields: Array<{
    key: string;
    label: string;
    type: string;
    options?: string[];
    required: boolean;
    question: string;
  }>;
  objection_handling: Array<{ objection: string; response: string }>;
  appointment_rules: {
    enabled: boolean;
    title: string;
    slots_description: string;
    requires_confirmation: boolean;
  };
  escalation_rules: {
    enabled: boolean;
    trigger_words: string[];
    action: string;
    staff_contact: string;
  };
  max_call_duration_seconds: number;
  knowledge_text: string;
  product_mode?: string;
}

interface VersionItem {
  id: number;
  version_number: number;
  status: string;
  is_current: boolean;
  published_at?: string;
  created_at?: string;
}


interface EconomyKeypadAction {
  key: string;
  action_type: string;
  label: string;
  confirmation_recording_id?: string;
  action_params?: Record<string, any>;
}

interface EconomyIVRConfigState {
  enabled: boolean;
  default_language: string;
  greeting_recording_id: string;
  language_greetings: Record<string, string>;
  keypad_actions: Record<string, EconomyKeypadAction>;
  timeout_seconds: number;
  max_invalid_retries: number;
  invalid_input_recording_id?: string;
  timeout_recording_id?: string;
  auto_hangup_after_action: boolean;
  allow_barge_in: boolean;
}

interface IVRConfig {
  enabled: boolean;
  greeting: string;
  timeout_seconds: number;
  human_phone: string;
  business_hours_enabled: boolean;
  start_time: string;
  end_time: string;
}

const DEFAULT_IVR_CONFIG: IVRConfig = {
  enabled: false,
  greeting: "Thank you for calling. Press 1 to speak with our AI specialist, or press 2 for human support.",
  timeout_seconds: 8,
  human_phone: "+91-9876543210",
  business_hours_enabled: false,
  start_time: "09:00",
  end_time: "19:00",
};

const DEFAULT_ECONOMY_IVR_CONFIG: EconomyIVRConfigState = {
  enabled: false,
  default_language: "en",
  greeting_recording_id: "",
  language_greetings: {},
  keypad_actions: {
    "1": { key: "1", action_type: "callback", label: "Request Callback" },
    "2": { key: "2", action_type: "appointment_request", label: "Book Appointment" },
    "3": { key: "3", action_type: "order_enquiry", label: "Order Status" },
    "4": { key: "4", action_type: "delivery_enquiry", label: "Delivery Status" },
    "5": { key: "5", action_type: "fee_enquiry", label: "Fee Structure" },
    "6": { key: "6", action_type: "general_enquiry", label: "General Enquiry" },
    "9": { key: "9", action_type: "hangup", label: "End Call" },
  },
  timeout_seconds: 8,
  max_invalid_retries: 2,
  auto_hangup_after_action: true,
  allow_barge_in: true,
};

// useSearchParams() needs a Suspense boundary in the App Router.
export default function TrainAgentPage() {
  return (
    <Suspense fallback={null}>
      <TrainAgentPageInner />
    </Suspense>
  );
}

function TrainAgentPageInner() {
  const { getAccessToken, loading: authLoading } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  // The workflow being edited is chosen explicitly via ?workflow_id=ID. When it
  // is absent the page edits the organization's default workflow, which is
  // labelled as such in the UI. An invalid id never falls back to the default.
  const rawWorkflowParam = searchParams.get("workflow_id");
  const selectedWorkflowId =
    rawWorkflowParam !== null && /^[0-9]+$/.test(rawWorkflowParam)
      ? Number(rawWorkflowParam)
      : null;
  const invalidWorkflowParam = rawWorkflowParam !== null && selectedWorkflowId === null;
  const wfQuery = selectedWorkflowId !== null ? `?workflow_id=${selectedWorkflowId}` : "";
  const wfBody = selectedWorkflowId !== null ? { workflow_id: selectedWorkflowId } : {};
  const [workflowName, setWorkflowName] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [switchId, setSwitchId] = useState("");
  // "starter_template" = this workflow has no saved Train My Agent settings.
  const [trainingSource, setTrainingSource] = useState<"saved" | "starter_template" | null>(null);
  const [presets, setPresets] = useState<IndustryPreset[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [saveMessage, setSaveMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Form State
  const [activePresetId, setActivePresetId] = useState<string>("real_estate");
  const [formData, setFormData] = useState<IndustryPreset | null>(null);
  const [initialFormData, setInitialFormData] = useState<string>("");
  const [workflowId, setWorkflowId] = useState<number | null>(null);
  const [releasedDefId, setReleasedDefId] = useState<number | null>(null);
  const [versions, setVersions] = useState<VersionItem[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  // Dirty state tracking (ISS-10)
  const isDirty = useMemo(() => {
    if (!initialFormData || !formData) return false;
    return JSON.stringify(formData) !== initialFormData;
  }, [formData, initialFormData]);

  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);
  const [ivrConfig, setIvrConfig] = useState<IVRConfig>(DEFAULT_IVR_CONFIG);
  const [ivrPreviewXml, setIvrPreviewXml] = useState<string | null>(null);
  const [previewingIvr, setPreviewingIvr] = useState(false);

  // Economy IVR State
  const [economyIvrConfig, setEconomyIvrConfig] =
    useState<EconomyIVRConfigState>(DEFAULT_ECONOMY_IVR_CONFIG);
  const [availableRecordings, setAvailableRecordings] = useState<{ id: string; name: string }[]>([]);
  const [savingEconomyIvr, setSavingEconomyIvr] = useState(false);
  const [newKeyDigit, setNewKeyDigit] = useState("7");
  const [newKeyActionType, setNewKeyActionType] = useState("custom_action");
  const [newKeyLabel, setNewKeyLabel] = useState("Custom Action");
  const [rates, setRates] = useState<Record<string, { rate_per_min_inr: number }> | null>(null);


  // Load Presets and Current Agent Training
  useEffect(() => {
    if (authLoading) return;

    // Ignore responses that arrive after the selected workflow changed.
    let cancelled = false;

    // Explicitly discard EVERYTHING belonging to the previously loaded
    // workflow before fetching the newly selected one (no reliance on remount).
    setLoading(true);
    setLoadError(null);
    setFormData(null);
    setInitialFormData("");
    setActivePresetId("real_estate");
    setWorkflowId(null);
    setWorkflowName(null);
    setTrainingSource(null);
    setReleasedDefId(null);
    setVersions([]);
    setHistoryOpen(false);
    setSaveMessage(null);
    setIvrConfig(DEFAULT_IVR_CONFIG);
    setIvrPreviewXml(null);
    setEconomyIvrConfig(DEFAULT_ECONOMY_IVR_CONFIG);
    setRates(null);

    async function loadData() {
      if (invalidWorkflowParam) {
        setLoadError(
          `"${rawWorkflowParam}" is not a valid workflow id. Nothing was loaded and no other workflow was substituted.`
        );
        setLoading(false);
        return;
      }
      try {
        const token = await getAccessToken();
        if (cancelled) return;
        const authHeader: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};

        // Fetch presets
        let loadedPresets: IndustryPreset[] = [];
        try {
          const presetsRes = await fetch("/api/v1/business/presets");
          if (presetsRes.ok) {
            loadedPresets = await presetsRes.json();
            setPresets(loadedPresets);
          }
        } catch (e) {
          console.error("Failed to load presets:", e);
        }

        // Fetch current agent
        try {
          const agentRes = await fetch(`/api/v1/business/agent${wfQuery}`, {
            headers: authHeader,
          });
          if (cancelled) return;
          if (!agentRes.ok && selectedWorkflowId !== null) {
            // Explicit selection that could not be loaded: never fall back to
            // the default workflow or a blank template.
            let detail = `HTTP ${agentRes.status}`;
            try {
              const errBody = await agentRes.json();
              if (typeof errBody?.detail === "string") detail = errBody.detail;
            } catch {}
            if (cancelled) return;
            setLoadError(
              `Workflow ${selectedWorkflowId} could not be loaded (${detail}). Nothing was changed and no other workflow was substituted.`
            );
            return;
          }
          if (agentRes.ok) {
            const agentData = await agentRes.json();
            if (cancelled) return;
            if (agentData.rates) {
              setRates(agentData.rates);
            }
            setWorkflowId(agentData.workflow_id);
            setWorkflowName(agentData.workflow_name ?? null);
            setTrainingSource(agentData.training_source === "starter_template" ? "starter_template" : "saved");
            setReleasedDefId(agentData.released_definition_id);
            setVersions(agentData.versions || []);
            if (agentData.training_data) {
              setFormData(agentData.training_data);
              setInitialFormData(JSON.stringify(agentData.training_data));
              setActivePresetId(agentData.training_data.id || "real_estate");
            } else if (loadedPresets.length > 0) {
              setFormData(loadedPresets[0]);
              setInitialFormData(JSON.stringify(loadedPresets[0]));
              setActivePresetId(loadedPresets[0].id || "real_estate");
            }
          } else if (loadedPresets.length > 0) {
            setFormData(loadedPresets[0]);
            setInitialFormData(JSON.stringify(loadedPresets[0]));
            setActivePresetId(loadedPresets[0].id || "real_estate");
          }

          // Fetch IVR settings
          try {
            const ivrRes = await fetch(`/api/v1/business/ivr${wfQuery}`, {
              headers: authHeader as HeadersInit,
            });

          // Fetch Economy IVR configuration
          try {
            const econRes = await fetch(`/api/v1/business/economy-ivr${wfQuery}`, {
              headers: authHeader as HeadersInit,
            });
            if (cancelled) return;
            if (econRes.ok) {
              const econData = await econRes.json();
              if (cancelled) return;
              if (econData) {
                setEconomyIvrConfig({
                  enabled: !!econData.enabled,
                  default_language: econData.default_language || "en",
                  greeting_recording_id: econData.greeting_recording_id || "",
                  language_greetings: econData.language_greetings || {},
                  keypad_actions: econData.keypad_actions || {},
                  timeout_seconds: econData.timeout_seconds || 8,
                  max_invalid_retries: econData.max_invalid_retries ?? 2,
                  invalid_input_recording_id: econData.invalid_input_recording_id || "",
                  timeout_recording_id: econData.timeout_recording_id || "",
                  auto_hangup_after_action: econData.auto_hangup_after_action ?? true,
                  allow_barge_in: econData.allow_barge_in ?? true,
                });
              }
            }
          } catch (e) {
            console.error("Failed to load Economy IVR config:", e);
          }

          // Fetch organization recordings
          try {
            const recRes = await fetch("/api/v1/workflow-recordings", {
              headers: authHeader as HeadersInit,
            });
            if (recRes.ok) {
              const recData = await recRes.json();
              const recs = (recData.recordings || []).map((r: any) => ({
                id: r.recording_id,
                name: r.name || r.recording_id,
              }));
              setAvailableRecordings(recs);
            }
          } catch (e) {
            console.error("Failed to load recordings:", e);
          }

            if (cancelled) return;
            if (ivrRes.ok) {
              const ivrData = await ivrRes.json();
              if (cancelled) return;
              setIvrConfig({
                enabled: !!ivrData.enabled,
                greeting:
                  ivrData.greeting ||
                  "Thank you for calling. Press 1 to speak with our AI specialist, or press 2 for human support.",
                timeout_seconds: ivrData.timeout_seconds || 8,
                human_phone:
                  ivrData.human_route?.phone_number || "+91-9876543210",
                business_hours_enabled: !!ivrData.business_hours?.enabled,
                start_time: ivrData.business_hours?.start_time || "09:00",
                end_time: ivrData.business_hours?.end_time || "19:00",
              });
            }
          } catch {}
        } catch (e) {
          console.error("Failed to load agent:", e);
          if (loadedPresets.length > 0) {
            setFormData(loadedPresets[0]);
            setActivePresetId(loadedPresets[0].id || "real_estate");
          }
        }
      } catch (err) {
        console.error("Failed to load agent training:", err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadData();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, getAccessToken, selectedWorkflowId, rawWorkflowParam]);

  // The loaded workflow must be exactly the one selected in the URL; otherwise
  // every save/publish/restore is refused (never silently use another workflow).
  const requireLoadedTarget = (): boolean => {
    const mismatch = selectedWorkflowId !== null && workflowId !== selectedWorkflowId;
    if (loading || !formData || loadError || invalidWorkflowParam || mismatch) {
      toast.error(
        "The selected workflow is not loaded. Reload the page before saving or publishing."
      );
      return false;
    }
    return true;
  };

  const handleSwitchWorkflow = () => {
    const id = switchId.trim();
    if (!/^[0-9]+$/.test(id)) {
      toast.error("Enter a numeric workflow ID.");
      return;
    }
    if (isDirty && !window.confirm("You have unsaved changes. Switch workflow and discard them?")) {
      return;
    }
    router.push(`/agent?workflow_id=${id}`);
  };

  const handleSelectPreset = (presetId: string) => {
    const selected = presets.find((p) => p.id === presetId);
    if (selected) {
      setActivePresetId(presetId);
      setFormData(JSON.parse(JSON.stringify(selected)));
      setSaveMessage({
        type: "success",
        text: `Loaded template for "${selected.name}". You can now customize and publish it.`,
      });
      toast.success(`Loaded "${selected.name}" template`);
      setTimeout(() => setSaveMessage(null), 4000);
    }
  };

  const handleSaveDraft = async () => {
    if (!formData || !requireLoadedTarget()) return;
    try {
      setSaving(true);
      setSaveMessage(null);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/agent/draft", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({ training_data: formData, ...wfBody }),
      });
      if (res.ok) {
        setInitialFormData(JSON.stringify(formData));
        setSaveMessage({
          type: "success",
          text: "Agent training draft saved successfully. Changes are preserved.",
        });
        toast.success("Agent draft saved successfully");
      } else {
        const err = await res.json();
        setSaveMessage({ type: "error", text: err.detail || "Failed to save draft." });
      }
    } catch {
      setSaveMessage({ type: "error", text: "Network error while saving draft." });
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMessage(null), 5000);
    }
  };

  const handlePublish = async () => {
    if (!formData || !requireLoadedTarget()) return;
    const targetLabel = `"${workflowName ?? "Unnamed workflow"}" (workflow #${workflowId})`;
    const confirmed = window.confirm(
      `Publish this agent to ${targetLabel}?\n\n` +
        (selectedWorkflowId === null
          ? "WARNING: no workflow was explicitly selected, so this is your DEFAULT workflow and it may be live.\n\n"
          : "") +
        (trainingSource === "starter_template"
          ? "NOTE: this workflow has no saved Train My Agent settings, so the form shows a generic starter template. Publishing REPLACES this workflow's current call flow with one generated from the form.\n\n"
          : "") +
        "This replaces the live version of THIS workflow only. Previous versions stay in its history."
    );
    if (!confirmed) return;
    try {
      setPublishing(true);
      setSaveMessage(null);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/agent/publish", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({ training_data: formData, ...wfBody }),
      });
      if (res.ok) {
        const data = await res.json();
        setReleasedDefId(data.released_definition_id);
        setInitialFormData(JSON.stringify(formData));
        toast.success(`Agent published successfully (v${data.version_number || "live"})`);

        // Save IVR settings concurrently
        try {
          await fetch(`/api/v1/business/ivr${wfQuery}`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: token ? `Bearer ${token}` : "",
            },
            body: JSON.stringify({
              enabled: ivrConfig.enabled,
              greeting: ivrConfig.greeting,
              timeout_seconds: ivrConfig.timeout_seconds,
              human_route: {
                enabled: true,
                phone_number: ivrConfig.human_phone,
              },
              business_hours: {
                enabled: ivrConfig.business_hours_enabled,
                start_time: ivrConfig.start_time,
                end_time: ivrConfig.end_time,
              },
            }),
          });
        } catch {}

        setSaveMessage({
          type: "success",
          text: `Agent published successfully! Now live in runtime (Version ${data.version_number || "Active"}).`,
        });
        // Refresh version list
        const agentRes = await fetch(`/api/v1/business/agent${wfQuery}`, {
          headers: (token ? { Authorization: `Bearer ${token}` } : {}) as HeadersInit,
        });
        if (agentRes.ok) {
          const refreshed = await agentRes.json();
          setVersions(refreshed.versions || []);
        }
      } else {
        const err = await res.json();
        setSaveMessage({ type: "error", text: err.detail || "Failed to publish agent." });
      }
    } catch {
      setSaveMessage({ type: "error", text: "Network error while publishing agent." });
    } finally {
      setPublishing(false);
      setTimeout(() => setSaveMessage(null), 6000);
    }
  };


  const handleSaveEconomyIvr = async () => {
    if (!requireLoadedTarget()) return;
    try {
      setSavingEconomyIvr(true);
      const token = await getAccessToken();

      const res = await fetch(`/api/v1/business/economy-ivr${wfQuery}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify(economyIvrConfig),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Failed to save Economy IVR config");
      }
      toast.success("Economy IVR configuration saved successfully.");
    } catch (e: any) {
      toast.error(e.message || "Failed to save Economy IVR configuration.");
    } finally {
      setSavingEconomyIvr(false);
    }
  };

  const handleAddKeypadAction = () => {
    if (!newKeyDigit || economyIvrConfig.keypad_actions[newKeyDigit]) {
      toast.error(`Key ${newKeyDigit} is already assigned.`);
      return;
    }
    const updated = {
      ...economyIvrConfig.keypad_actions,
      [newKeyDigit]: {
        key: newKeyDigit,
        action_type: newKeyActionType,
        label: newKeyLabel || `Key ${newKeyDigit} Action`,
      },
    };
    setEconomyIvrConfig({ ...economyIvrConfig, keypad_actions: updated });
    // Pick next unused digit
    const used = Object.keys(updated);
    const nextUnused = ["1", "2", "3", "4", "5", "6", "7", "8", "9"].find((d) => !used.includes(d));
    if (nextUnused) setNewKeyDigit(nextUnused);
  };

  const handleRemoveKeypadAction = (digit: string) => {
    const updated = { ...economyIvrConfig.keypad_actions };
    delete updated[digit];
    setEconomyIvrConfig({ ...economyIvrConfig, keypad_actions: updated });
  };

  const handlePreviewIvr = async () => {
    try {
      setPreviewingIvr(true);
      const token = await getAccessToken();
      const res = await fetch("/api/v1/business/ivr/preview", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: token ? `Bearer ${token}` : "",
        },
        body: JSON.stringify({
          enabled: ivrConfig.enabled,
          greeting: ivrConfig.greeting,
          timeout_seconds: ivrConfig.timeout_seconds,
          human_route: {
            enabled: true,
            phone_number: ivrConfig.human_phone,
          },
          business_hours: {
            enabled: ivrConfig.business_hours_enabled,
            start_time: ivrConfig.start_time,
            end_time: ivrConfig.end_time,
          },
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setIvrPreviewXml(data.xml);
      }
    } catch {
    } finally {
      setPreviewingIvr(false);
    }
  };

  const handleRestoreVersion = async (defId: number) => {
    if (!requireLoadedTarget()) return;
    if (
      !window.confirm(
        `Restore version ${defId} and make it LIVE for "${workflowName ?? "this workflow"}" (workflow #${workflowId})?`
      )
    ) {
      return;
    }
    try {
      setRestoring(true);
      const token = await getAccessToken();
      const res = await fetch(`/api/v1/business/agent/restore/${defId}${wfQuery}`, {
        method: "POST",
        headers: (token ? { Authorization: `Bearer ${token}` } : {}) as HeadersInit,
      });
      if (res.ok) {
        const data = await res.json();
        setSaveMessage({
          type: "success",
          text: `Restored to version ${data.version_number}. Reloading agent data...`,
        });
        setHistoryOpen(false);
        // Refresh agent data
        const agentRes = await fetch(`/api/v1/business/agent${wfQuery}`, {
          headers: (token ? { Authorization: `Bearer ${token}` } : {}) as HeadersInit,
        });
        if (agentRes.ok) {
          const refreshed = await agentRes.json();
          setFormData(refreshed.training_data);
          setInitialFormData(JSON.stringify(refreshed.training_data));
          setVersions(refreshed.versions || []);
          setReleasedDefId(refreshed.released_definition_id);
        }
      }
    } catch {
      setSaveMessage({ type: "error", text: "Failed to restore version." });
    } finally {
      setRestoring(false);
    }
  };

  // Field Array Helpers
  const addProduct = () => {
    if (!formData) return;
    const updated = {
      ...formData,
      products_and_pricing: [
        ...(formData.products_and_pricing || []),
        { name: "New Product / Offering", price: "₹0", description: "Details..." },
      ],
    };
    setFormData(updated);
  };

  const removeProduct = (idx: number) => {
    if (!formData) return;
    const items = [...formData.products_and_pricing];
    items.splice(idx, 1);
    setFormData({ ...formData, products_and_pricing: items });
  };

  const addQualificationField = () => {
    if (!formData) return;
    const existing = formData.qualification_fields || [];
    const nextIndex = existing.length + 1;
    const key = `custom_field_${nextIndex}`;
    const updated = {
      ...formData,
      qualification_fields: [
        ...existing,
        {
          key,
          label: `Custom Field ${nextIndex}`,
          type: "text",
          required: false,
          question: "",
        },
      ],
    };
    setFormData(updated);
    toast.success(`Custom qualification field ${nextIndex} added`);
  };

  const removeQualificationField = (idx: number) => {
    if (!formData) return;
    const items = [...formData.qualification_fields];
    items.splice(idx, 1);
    setFormData({ ...formData, qualification_fields: items });
  };

  const addObjection = () => {
    if (!formData) return;
    const existing = formData.objection_handling || [];
    // Prevent duplicate empty cards if the last objection is already blank
    if (existing.length > 0) {
      const last = existing[existing.length - 1];
      if (!last.objection?.trim() || last.objection === "Customer concern / question") {
        toast.info("Please fill in the existing objection card before adding another.");
        return;
      }
    }
    const updated = {
      ...formData,
      objection_handling: [
        ...existing,
        { objection: "", response: "" },
      ],
    };
    setFormData(updated);
    toast.success("New objection handling rule added");
  };

  const removeObjection = (idx: number) => {
    if (!formData) return;
    const items = [...formData.objection_handling];
    items.splice(idx, 1);
    setFormData({ ...formData, objection_handling: items });
  };

  if (loadError) {
    return (
      <div className="container mx-auto px-4 py-8 max-w-3xl space-y-4">
        <div className="p-4 rounded-xl bg-destructive/10 text-destructive border border-destructive/20 text-sm">
          <p className="font-semibold mb-1">Cannot open this workflow</p>
          <p>{loadError}</p>
        </div>
        <div className="flex items-center gap-3">
          <Link href="/workflow">
            <Button variant="outline" size="sm">Back to Voice Agents</Button>
          </Link>
          <Link href="/agent">
            <Button variant="ghost" size="sm">Open the default workflow</Button>
          </Link>
        </div>
      </div>
    );
  }

  if (loading || !formData) {
    return (
      <div className="container mx-auto px-4 py-8 space-y-6">
        <Skeleton className="h-12 w-64" />
        <Skeleton className="h-8 w-96" />
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((i) => (
            <Skeleton key={i} className="h-20 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-96 rounded-xl" />
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-8 space-y-8 max-w-6xl animate-fade-in-up">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Badge variant="outline" className="bg-primary/10 text-primary border-primary/20 gap-1.5 px-3 py-1 font-semibold">
              <Sparkles className="h-3.5 w-3.5" />
              Owner Studio
            </Badge>
            {releasedDefId && (
              <Badge variant="secondary" className="gap-1 font-mono text-xs">
                Live Version: #{releasedDefId}
              </Badge>
            )}
          </div>
          <h1 className="text-3xl font-bold tracking-tight">Train My AI Agent</h1>
          <p className="text-muted-foreground text-sm mt-1">
            Configure your AI receptionist, products, Telugu/Hindi/English instructions, qualification rules, and appointment booking without coding.
          </p>
          <div
            className={`mt-3 flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 text-sm ${
              selectedWorkflowId === null
                ? "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300"
                : "border-primary/30 bg-primary/5"
            }`}
          >
            <span className="font-semibold">
              Editing workflow: {workflowName ?? "Unnamed"} (#{workflowId})
            </span>
            {selectedWorkflowId === null && (
              <span className="text-xs">
                Default workflow — no workflow selected. Publishing changes this workflow only.
              </span>
            )}
            <span className="ml-auto flex items-center gap-2">
              <Input
                value={switchId}
                onChange={(e) => setSwitchId(e.target.value)}
                placeholder="Workflow ID"
                className="h-8 w-28"
                inputMode="numeric"
              />
              <Button type="button" variant="outline" size="sm" onClick={handleSwitchWorkflow}>
                Open
              </Button>
            </span>
          </div>
          {trainingSource === "starter_template" && (
            <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
              This workflow has no saved Train My Agent settings yet. The form below is a generic
              starter template, not this workflow&apos;s current call flow. Publishing will replace
              the workflow&apos;s call flow with one generated from this form.
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-3">
          <Dialog open={historyOpen} onOpenChange={setHistoryOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <History className="h-4 w-4" />
                Version History
              </Button>
            </DialogTrigger>
            <DialogContent className="max-w-md">
              <DialogHeader>
                <DialogTitle>Version History</DialogTitle>
                <DialogDescription>
                  Published versions of your AI agent. You can restore any previous version at any time.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3 mt-4 max-h-80 overflow-y-auto pr-1">
                {versions.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No previous versions saved yet.</p>
                ) : (
                  versions.map((v) => (
                    <div
                      key={v.id}
                      className="flex items-center justify-between p-3 rounded-lg border bg-card/50 text-sm"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-semibold">Version #{v.version_number}</span>
                          {v.is_current && (
                            <Badge variant="default" className="text-[10px] px-1.5 py-0 h-4">
                              Active Live
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {v.published_at
                            ? new Date(v.published_at).toLocaleString()
                            : "Draft / Initial"}
                        </p>
                      </div>
                      {!v.is_current && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={restoring}
                          onClick={() => handleRestoreVersion(v.id)}
                          className="h-8 gap-1.5 text-xs text-primary"
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                          Restore
                        </Button>
                      )}
                    </div>
                  ))
                )}
              </div>
            </DialogContent>
          </Dialog>

          {workflowId && (
            <Link href={`/workflow/${workflowId}`} target="_blank">
              <Button variant="secondary" size="sm" className="gap-2">
                <Bot className="h-4 w-4" />
                Browser Test
              </Button>
            </Link>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={handleSaveDraft}
            disabled={saving || publishing || loading}
            className="gap-2"
          >
            <Save className="h-4 w-4" />
            {saving ? "Saving..." : "Save Draft"}
          </Button>

          <Button
            size="sm"
            onClick={handlePublish}
            disabled={publishing || saving || loading}
            className="gap-2 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 shadow-md shadow-blue-500/20 text-white font-medium"
          >
            <Send className="h-4 w-4" />
            {publishing ? "Publishing..." : "Publish Agent"}
          </Button>
        </div>
      </div>

      {/* Alert / Notification */}
      {saveMessage && (
        <div
          className={`p-4 rounded-xl flex items-center gap-3 text-sm animate-fade-in ${
            saveMessage.type === "success"
              ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20"
              : "bg-destructive/10 text-destructive border border-destructive/20"
          }`}
        >
          {saveMessage.type === "success" ? (
            <CheckCircle2 className="h-5 w-5 shrink-0" />
          ) : (
            <AlertCircle className="h-5 w-5 shrink-0" />
          )}
          <span>{saveMessage.text}</span>
        </div>
      )}

      {/* Unsaved Changes Banner (ISS-10) */}
      {isDirty && !saveMessage && (
        <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-800 dark:text-amber-300 flex items-center justify-between text-xs animate-fade-in">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-amber-500 animate-pulse" />
            <span className="font-semibold">Unsaved Changes:</span>
            <span>You have unsaved changes to your agent prompt and qualification rules.</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={handleSaveDraft}
              disabled={saving}
              className="h-7 text-xs gap-1 border-amber-500/30 text-amber-900 dark:text-amber-100 hover:bg-amber-500/20"
            >
              <Save className="h-3 w-3" />
              {saving ? "Saving..." : "Save Draft"}
            </Button>
          </div>
        </div>
      )}

      {/* Active Industry Profile Banner */}
      <div className="p-4 rounded-xl border bg-card/60 backdrop-blur-sm shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10 text-primary border border-primary/20 shrink-0">
            <Building2 className="h-5 w-5" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs uppercase font-bold tracking-wider text-muted-foreground">
                Active Industry Profile:
              </span>
              <Badge variant="outline" className="text-xs bg-primary/5 text-primary border-primary/20 font-semibold px-2.5 py-0.5">
                {formData?.name || "Business Assistant"}
              </Badge>
              {formData?.industry && (
                <Badge variant="secondary" className="text-[10px] font-medium">
                  {formData.industry}
                </Badge>
              )}
            </div>
            <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
              {formData?.business_name ? `${formData.business_name} • ` : ""}
              Agent greeting, conversation objectives, Telugu/Hindi/English instructions, and lead qualification criteria are tailored for this industry profile.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Link href="/settings">
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs">
              <Settings className="h-3.5 w-3.5" />
              Change Industry in Settings
            </Button>
          </Link>
        </div>
      </div>

      {/* Tabbed Settings Editor */}
      <Tabs defaultValue="profile" className="space-y-6">
        <TabsList className="grid grid-cols-2 sm:grid-cols-7 h-auto p-1 bg-muted/60 rounded-xl gap-1">
          <TabsTrigger value="profile" className="text-xs py-2 gap-1.5">
            <Building2 className="h-3.5 w-3.5" />
            Profile
          </TabsTrigger>
          <TabsTrigger value="products" className="text-xs py-2 gap-1.5">
            <Tag className="h-3.5 w-3.5" />
            Products & Pricing
          </TabsTrigger>
          <TabsTrigger value="persona" className="text-xs py-2 gap-1.5">
            <Languages className="h-3.5 w-3.5" />
            Voice & Mode
          </TabsTrigger>
          <TabsTrigger value="ivr" className="text-xs py-2 gap-1.5">
            <PhoneCall className="h-3.5 w-3.5" />
            Call Menu (IVR)
          </TabsTrigger>
          <TabsTrigger value="qualification" className="text-xs py-2 gap-1.5">
            <Users className="h-3.5 w-3.5" />
            Lead Qualification
          </TabsTrigger>
          <TabsTrigger value="objections" className="text-xs py-2 gap-1.5">
            <HelpCircle className="h-3.5 w-3.5" />
            Objections & Bookings
          </TabsTrigger>
          <TabsTrigger value="knowledge" className="text-xs py-2 gap-1.5">
            <Globe className="h-3.5 w-3.5" />
            FAQs & Policies
          </TabsTrigger>
        </TabsList>

        {/* TAB 1: Profile */}
        <TabsContent value="profile" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Business & Operational Details</CardTitle>
              <CardDescription>Tell your AI agent about your company and business hours.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="p-3 bg-muted/40 rounded-lg border flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-primary" />
                    <Label className="text-sm font-semibold">Change Industry Template</Label>
                  </div>
                  <p className="text-xs text-muted-foreground">Load pre-configured industry conversation models and pricing templates</p>
                </div>
                <Select
                  value={activePresetId}
                  onValueChange={(val) => handleSelectPreset(val)}
                >
                  <SelectTrigger className="w-full sm:w-64 h-9 text-xs">
                    <SelectValue placeholder="Select industry template..." />
                  </SelectTrigger>
                  <SelectContent>
                    {presets.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Business Name</Label>
                  <Input
                    value={formData.business_name}
                    onChange={(e) => setFormData({ ...formData, business_name: e.target.value })}
                    placeholder="e.g. Sri Krishna Developers"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Industry / Category</Label>
                  <Input
                    value={formData.industry}
                    onChange={(e) => setFormData({ ...formData, industry: e.target.value })}
                    placeholder="e.g. Real Estate"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Location / Address</Label>
                  <Input
                    value={formData.location}
                    onChange={(e) => setFormData({ ...formData, location: e.target.value })}
                    placeholder="e.g. Hyderabad, Telangana"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Working Hours</Label>
                  <Input
                    value={formData.working_hours}
                    onChange={(e) => setFormData({ ...formData, working_hours: e.target.value })}
                    placeholder="e.g. 9:00 AM to 7:00 PM (Mon-Sat)"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label>Maximum Call Duration (Seconds)</Label>
                <Input
                  type="number"
                  value={formData.max_call_duration_seconds}
                  onChange={(e) => { const v = parseInt(e.target.value, 10); setFormData({ ...formData, max_call_duration_seconds: Number.isNaN(v) ? formData.max_call_duration_seconds : v }); }}
                  className="w-48"
                />
                <p className="text-xs text-muted-foreground">Default 600s (10 minutes) protects against runaway call costs.</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 2: Products & Pricing */}
        <TabsContent value="products" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-lg">Offerings & Pricing Catalog</CardTitle>
                <CardDescription>Your AI agent will quote these exact products and prices to callers.</CardDescription>
              </div>
              <Button size="sm" onClick={addProduct} variant="outline" className="gap-1 text-xs">
                <Plus className="h-3.5 w-3.5" />
                Add Item
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {(formData.products_and_pricing || []).map((p, idx) => (
                <div key={idx} className="p-3 border rounded-xl space-y-3 bg-card/60">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className="sm:col-span-2 space-y-1">
                      <Label className="text-xs">Product / Service Name</Label>
                      <Input
                        value={p.name}
                        onChange={(e) => {
                          const updated = [...formData.products_and_pricing];
                          updated[idx].name = e.target.value;
                          setFormData({ ...formData, products_and_pricing: updated });
                        }}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Price / Rate</Label>
                      <Input
                        value={p.price}
                        onChange={(e) => {
                          const updated = [...formData.products_and_pricing];
                          updated[idx].price = e.target.value;
                          setFormData({ ...formData, products_and_pricing: updated });
                        }}
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex-1 space-y-1">
                      <Label className="text-xs">Key Highlights / Description</Label>
                      <Input
                        value={p.description}
                        onChange={(e) => {
                          const updated = [...formData.products_and_pricing];
                          updated[idx].description = e.target.value;
                          setFormData({ ...formData, products_and_pricing: updated });
                        }}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:bg-destructive/10 mt-5 shrink-0"
                      onClick={() => removeProduct(idx)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 3: Persona & Languages */}
        <TabsContent value="persona" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Agent Persona & Indian Languages</CardTitle>
              <CardDescription>Tailor greeting, tone, and multi-language handling for Telugu, Hindi, and English.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Agent Name</Label>
                  <Input
                    value={formData.agent_name}
                    onChange={(e) => setFormData({ ...formData, agent_name: e.target.value })}
                    placeholder="e.g. Priya"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Personality & Tone</Label>
                  <Input
                    value={formData.agent_personality}
                    onChange={(e) => setFormData({ ...formData, agent_personality: e.target.value })}
                    placeholder="e.g. Warm, polite, professional, consultative"
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label>Opening Greeting Script (Spoken on Answer)</Label>
                <Textarea
                  rows={2}
                  value={formData.greeting}
                  onChange={(e) => setFormData({ ...formData, greeting: e.target.value })}
                  placeholder="Namaste! Welcome to..."
                />
              </div>

              <div className="space-y-2">
                <Label>Closing Goodbye Script</Label>
                <Input
                  value={formData.closing_script}
                  onChange={(e) => setFormData({ ...formData, closing_script: e.target.value })}
                  placeholder="Thank you for calling..."
                />
              </div>

              <div className="space-y-2">
                <Label>Language & Code-Switching Instructions (Telugu / Hindi / English)</Label>
                <Textarea
                  rows={3}
                  value={formData.language_instructions}
                  onChange={(e) => setFormData({ ...formData, language_instructions: e.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  Controls how the AI model responds when callers speak Telugu, Hindi, or conversational Hinglish/Tenglish.
                </p>
              </div>

              {/* Calling & AI Engine Mode */}
              <div className="space-y-3 pt-3 border-t">
                <Label className="text-sm font-semibold">Voice Engine & Product Mode</Label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div
                    onClick={() => setFormData({ ...formData, product_mode: "standard" })}
                    className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                      (formData.product_mode || "standard") === "standard"
                        ? "border-primary bg-primary/5 ring-2 ring-primary/20"
                        : "border-border hover:bg-muted/40"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-semibold text-xs text-foreground">Standard (Gemini Live)</span>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        ₹{rates?.standard?.rate_per_min_inr !== undefined ? rates.standard.rate_per_min_inr.toFixed(2) : "1.80"}/min
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      Native speech-to-speech with natural cadence and live Indian English/Hindi turn taking.
                    </p>
                  </div>

                  <div
                    onClick={() => setFormData({ ...formData, product_mode: "premium" })}
                    className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                      formData.product_mode === "premium"
                        ? "border-primary bg-primary/5 ring-2 ring-primary/20"
                        : "border-border hover:bg-muted/40"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-semibold text-xs text-foreground">Premium (DeepSeek + Cartesia)</span>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        ₹{rates?.premium?.rate_per_min_inr !== undefined ? rates.premium.rate_per_min_inr.toFixed(2) : "3.20"}/min
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      DeepSeek Flash reasoning with Cartesia neural voices & Deepgram multilingual STT.
                    </p>
                  </div>

                  <div
                    onClick={() => setFormData({ ...formData, product_mode: "budget" })}
                    className={`p-3.5 rounded-xl border cursor-pointer transition-all ${
                      formData.product_mode === "budget"
                        ? "border-primary bg-primary/5 ring-2 ring-primary/20"
                        : "border-border hover:bg-muted/40"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="font-semibold text-xs text-foreground">Budget (Automated Menu)</span>
                      <Badge variant="outline" className="text-[10px] font-mono">
                        ₹{rates?.budget?.rate_per_min_inr !== undefined ? rates.budget.rate_per_min_inr.toFixed(2) : "0.85"}/min
                      </Badge>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">
                      Prerecorded greeting & menu, activating AI only when caller requests specialist.
                    </p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        
        {/* TAB: Call Menu (IVR) */}
        <TabsContent value="ivr" className="space-y-6">
          {/* Economy IVR (Prerecorded Audio + 1-9 Keypad Actions) */}
          <Card className="border-primary/20 shadow-xs">
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <div className="flex items-center gap-2">
                  <CardTitle className="text-lg">Economy IVR Menu (1–9 Keypad & Audio)</CardTitle>
                  <Badge variant="outline" className="text-xs bg-emerald-500/10 text-emerald-600 border-emerald-500/30">Zero AI Cost</Badge>
                </div>
                <CardDescription>
                  Prerecorded owner greetings with RFC 4733 DTMF keypad options. Zero LLM, STT, or TTS fees incurred.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant={economyIvrConfig.enabled ? "default" : "outline"}
                  onClick={() => setEconomyIvrConfig({ ...economyIvrConfig, enabled: !economyIvrConfig.enabled })}
                  className="text-xs gap-1.5"
                >
                  <KeyRound className="h-3.5 w-3.5" />
                  {economyIvrConfig.enabled ? "Economy IVR Active" : "Economy IVR Inactive"}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              {/* Language & Primary Audio */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label className="text-xs font-semibold">Default Language</Label>
                  <Select
                    value={economyIvrConfig.default_language}
                    onValueChange={(val) => setEconomyIvrConfig({ ...economyIvrConfig, default_language: val })}
                  >
                    <SelectTrigger className="w-full text-xs">
                      <SelectValue placeholder="Select language" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="en">English (Default)</SelectItem>
                      <SelectItem value="te">Telugu (తెలుగు)</SelectItem>
                      <SelectItem value="hi">Hindi (हिन्दी)</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-[11px] text-muted-foreground">Language used when incoming caller context has no language tag.</p>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs font-semibold">Default Greeting Recording</Label>
                  <div className="flex gap-2">
                    <Input
                      value={economyIvrConfig.greeting_recording_id}
                      onChange={(e) => setEconomyIvrConfig({ ...economyIvrConfig, greeting_recording_id: e.target.value })}
                      placeholder="e.g. rec_greeting_en"
                      className="text-xs"
                    />
                    {availableRecordings.length > 0 && (
                      <Select
                        value=""
                        onValueChange={(val) => setEconomyIvrConfig({ ...economyIvrConfig, greeting_recording_id: val })}
                      >
                        <SelectTrigger className="w-[140px] text-xs">
                          <SelectValue placeholder="Pick recording" />
                        </SelectTrigger>
                        <SelectContent>
                          {availableRecordings.map((r) => (
                            <SelectItem key={r.id} value={r.id}>
                              {r.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground">Owner prerecorded audio played upon answer.</p>
                </div>
              </div>

              {/* Language Specific Audio Overrides */}
              <div className="p-3.5 bg-muted/40 rounded-xl border space-y-3">
                <div className="flex items-center gap-2">
                  <Languages className="h-4 w-4 text-primary" />
                  <Label className="text-xs font-semibold">Multi-Language Audio Greetings (Optional)</Label>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <Label className="text-[11px]">Telugu Greeting (తెలుగు)</Label>
                    <Input
                      value={economyIvrConfig.language_greetings["te"] || ""}
                      onChange={(e) =>
                        setEconomyIvrConfig({
                          ...economyIvrConfig,
                          language_greetings: { ...economyIvrConfig.language_greetings, te: e.target.value },
                        })
                      }
                      placeholder="rec_greeting_te"
                      className="text-xs h-8"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px]">Hindi Greeting (हिन्दी)</Label>
                    <Input
                      value={economyIvrConfig.language_greetings["hi"] || ""}
                      onChange={(e) =>
                        setEconomyIvrConfig({
                          ...economyIvrConfig,
                          language_greetings: { ...economyIvrConfig.language_greetings, hi: e.target.value },
                        })
                      }
                      placeholder="rec_greeting_hi"
                      className="text-xs h-8"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-[11px]">English Greeting</Label>
                    <Input
                      value={economyIvrConfig.language_greetings["en"] || ""}
                      onChange={(e) =>
                        setEconomyIvrConfig({
                          ...economyIvrConfig,
                          language_greetings: { ...economyIvrConfig.language_greetings, en: e.target.value },
                        })
                      }
                      placeholder="rec_greeting_en"
                      className="text-xs h-8"
                    />
                  </div>
                </div>
              </div>

              {/* 1-9 Keypad Menu Actions */}
              <div className="space-y-3 pt-2 border-t">
                <div className="flex items-center justify-between">
                  <div>
                    <Label className="text-xs font-semibold">Keypad Action Routing (Digits 1–9)</Label>
                    <p className="text-xs text-muted-foreground">Maps each telephone digit to an automated business action.</p>
                  </div>
                  <Badge variant="outline" className="text-xs font-mono">{Object.keys(economyIvrConfig.keypad_actions).length} Keypad Actions</Badge>
                </div>

                <div className="space-y-2">
                  {Object.entries(economyIvrConfig.keypad_actions).map(([digit, action]) => (
                    <div key={digit} className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-3 rounded-lg border bg-card/60">
                      <div className="flex items-center gap-2.5">
                        <div className="h-7 w-7 rounded-md bg-primary text-primary-foreground font-mono font-bold flex items-center justify-center text-xs">
                          {digit}
                        </div>
                        <div>
                          <p className="text-xs font-semibold">{action.label}</p>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            <Badge variant="secondary" className="text-[10px] uppercase font-mono">{action.action_type.replace(/_/g, " ")}</Badge>
                            {action.confirmation_recording_id && (
                              <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                                <Volume2 className="h-3 w-3" /> {action.confirmation_recording_id}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 self-end sm:self-center">
                        <Input
                          value={action.confirmation_recording_id || ""}
                          onChange={(e) => {
                            const updated = { ...economyIvrConfig.keypad_actions };
                            updated[digit] = { ...action, confirmation_recording_id: e.target.value };
                            setEconomyIvrConfig({ ...economyIvrConfig, keypad_actions: updated });
                          }}
                          placeholder="Confirmation Audio ID"
                          className="text-xs h-8 w-44"
                        />
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => handleRemoveKeypadAction(digit)}
                          className="h-8 w-8 text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Add Keypad Action Bar */}
                <div className="p-3 bg-muted/30 rounded-xl border border-dashed flex flex-wrap items-center gap-2.5">
                  <div className="w-16">
                    <Label className="text-[10px] text-muted-foreground block mb-1">Key (1-9)</Label>
                    <Select value={newKeyDigit} onValueChange={setNewKeyDigit}>
                      <SelectTrigger className="h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
                          <SelectItem key={d} value={d} disabled={!!economyIvrConfig.keypad_actions[d]}>
                            Key {d}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="w-48">
                    <Label className="text-[10px] text-muted-foreground block mb-1">Action Type</Label>
                    <Select value={newKeyActionType} onValueChange={(val) => {
                      setNewKeyActionType(val);
                      if (val === "callback") setNewKeyLabel("Request Callback");
                      else if (val === "appointment_request") setNewKeyLabel("Book Appointment");
                      else if (val === "order_enquiry") setNewKeyLabel("Order Status");
                      else if (val === "delivery_enquiry") setNewKeyLabel("Delivery Status");
                      else if (val === "fee_enquiry") setNewKeyLabel("Fee Structure");
                      else if (val === "general_enquiry") setNewKeyLabel("General Enquiry");
                      else if (val === "hangup") setNewKeyLabel("End Call");
                      else if (val === "human_transfer") setNewKeyLabel("Transfer to Staff");
                    }}>
                      <SelectTrigger className="h-8 text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="callback">Request Callback</SelectItem>
                        <SelectItem value="appointment_request">Book Appointment</SelectItem>
                        <SelectItem value="order_enquiry">Order Status Enquiry</SelectItem>
                        <SelectItem value="delivery_enquiry">Delivery Status Enquiry</SelectItem>
                        <SelectItem value="fee_enquiry">Fee / Pricing Enquiry</SelectItem>
                        <SelectItem value="general_enquiry">General Enquiry</SelectItem>
                        <SelectItem value="human_transfer">Transfer to Staff Phone</SelectItem>
                        <SelectItem value="custom_action">Custom Webhook / Action</SelectItem>
                        <SelectItem value="hangup">End Call (Hang Up)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="flex-1 min-w-[160px]">
                    <Label className="text-[10px] text-muted-foreground block mb-1">Menu Label</Label>
                    <Input
                      value={newKeyLabel}
                      onChange={(e) => setNewKeyLabel(e.target.value)}
                      placeholder="e.g. Speak to Sales"
                      className="h-8 text-xs"
                    />
                  </div>

                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={handleAddKeypadAction}
                    className="h-8 mt-4 text-xs gap-1"
                  >
                    <Plus className="h-3.5 w-3.5" /> Add Key Action
                  </Button>
                </div>
              </div>

              {/* Safety & Timeouts */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-2 border-t">
                <div className="space-y-1">
                  <Label className="text-xs font-semibold">DTMF Timeout (sec)</Label>
                  <Input
                    type="number"
                    min={3}
                    max={30}
                    value={economyIvrConfig.timeout_seconds}
                    onChange={(e) => setEconomyIvrConfig({ ...economyIvrConfig, timeout_seconds: parseInt(e.target.value) || 5 })}
                    className="text-xs h-8"
                  />
                  <p className="text-[10px] text-muted-foreground">Wait time before timeout action</p>
                </div>

                <div className="space-y-1">
                  <Label className="text-xs font-semibold">Max Invalid Retries</Label>
                  <Input
                    type="number"
                    min={0}
                    max={5}
                    value={economyIvrConfig.max_invalid_retries}
                    onChange={(e) => setEconomyIvrConfig({ ...economyIvrConfig, max_invalid_retries: parseInt(e.target.value) || 0 })}
                    className="text-xs h-8"
                  />
                  <p className="text-[10px] text-muted-foreground">Attempts before call terminates</p>
                </div>

                <div className="space-y-1">
                  <Label className="text-xs font-semibold">Prompt Barge-in</Label>
                  <div className="flex items-center justify-between pt-1">
                    <span className="text-xs text-muted-foreground">Stop audio on keypress</span>
                    <Switch
                      checked={economyIvrConfig.allow_barge_in}
                      onCheckedChange={(val) => setEconomyIvrConfig({ ...economyIvrConfig, allow_barge_in: val })}
                    />
                  </div>
                </div>

                <div className="space-y-1">
                  <Label className="text-xs font-semibold">Auto Hang-up</Label>
                  <div className="flex items-center justify-between pt-1">
                    <span className="text-xs text-muted-foreground">Hang up after action</span>
                    <Switch
                      checked={economyIvrConfig.auto_hangup_after_action}
                      onCheckedChange={(val) => setEconomyIvrConfig({ ...economyIvrConfig, auto_hangup_after_action: val })}
                    />
                  </div>
                </div>
              </div>

              {/* Save Economy IVR Button */}
              <div className="pt-3 border-t flex items-center justify-between">
                <Button
                  size="sm"
                  onClick={handleSaveEconomyIvr}
                  disabled={savingEconomyIvr}
                  className="gap-1.5 text-xs"
                >
                  <Save className="h-3.5 w-3.5" />
                  {savingEconomyIvr ? "Saving..." : "Save Economy IVR Menu"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Durable across inbound & outbound Asterisk calls
                </span>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-lg">Inbound Call Menu (IVR)</CardTitle>
                <CardDescription>
                  Gives callers an automated greeting and menu before connecting to AI or human staff. Zero AI costs are incurred while the caller listens to this menu.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant={ivrConfig.enabled ? "default" : "outline"}
                  onClick={() => setIvrConfig({ ...ivrConfig, enabled: !ivrConfig.enabled })}
                  className="text-xs"
                >
                  {ivrConfig.enabled ? "Menu Enabled" : "Menu Disabled"}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>Menu Greeting & Options Announcement</Label>
                <Textarea
                  rows={2}
                  value={ivrConfig.greeting}
                  onChange={(e) => setIvrConfig({ ...ivrConfig, greeting: e.target.value })}
                  placeholder="Thank you for calling. Press 1 to speak with our AI specialist, or press 2 for human support."
                />
                <p className="text-xs text-muted-foreground">
                  Spoken to callers upon answering. Keep this concise so callers can select their option promptly.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t">
                <div className="p-3.5 rounded-xl border bg-card/60 space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-semibold">Key 1: AI Voice Agent</Label>
                    <Badge variant="secondary" className="text-[10px]">Automated</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Connects directly to your trained AI voice specialist with published FAQs and qualification rules.
                  </p>
                </div>

                <div className="p-3.5 rounded-xl border bg-card/60 space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-semibold">Key 2: Human Support Transfer</Label>
                    <Badge variant="secondary" className="text-[10px]">Phone Transfer</Badge>
                  </div>
                  <Input
                    value={ivrConfig.human_phone}
                    onChange={(e) => setIvrConfig({ ...ivrConfig, human_phone: e.target.value })}
                    placeholder="+91-9876543210"
                  />
                  <p className="text-[11px] text-muted-foreground">
                    Phone number to forward the call to when caller presses 2 or requests escalation.
                  </p>
                </div>
              </div>

              <div className="space-y-3 pt-2 border-t">
                <div className="flex items-center justify-between">
                  <div>
                    <Label className="text-xs font-semibold">Operating Hours Schedule</Label>
                    <p className="text-xs text-muted-foreground">After hours calls can be routed automatically to 24/7 AI</p>
                  </div>
                  <Button
                    size="sm"
                    variant={ivrConfig.business_hours_enabled ? "secondary" : "ghost"}
                    onClick={() => setIvrConfig({ ...ivrConfig, business_hours_enabled: !ivrConfig.business_hours_enabled })}
                    className="text-xs"
                  >
                    {ivrConfig.business_hours_enabled ? "Hours Active" : "All Day (24/7)"}
                  </Button>
                </div>
                {ivrConfig.business_hours_enabled && (
                  <div className="grid grid-cols-2 gap-4 max-w-sm">
                    <div className="space-y-1">
                      <Label className="text-xs">Start Time</Label>
                      <Input
                        value={ivrConfig.start_time}
                        onChange={(e) => setIvrConfig({ ...ivrConfig, start_time: e.target.value })}
                        placeholder="09:00"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">End Time</Label>
                      <Input
                        value={ivrConfig.end_time}
                        onChange={(e) => setIvrConfig({ ...ivrConfig, end_time: e.target.value })}
                        placeholder="19:00"
                      />
                    </div>
                  </div>
                )}
              </div>

              <div className="pt-2 border-t flex items-center justify-between">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handlePreviewIvr}
                  disabled={previewingIvr}
                  className="gap-1.5 text-xs"
                >
                  <Eye className="h-3.5 w-3.5" />
                  {previewingIvr ? "Generating..." : "Preview Carrier Telephony XML"}
                </Button>
                <span className="text-xs text-muted-foreground">
                  Saved automatically with agent configuration
                </span>
              </div>

              {ivrPreviewXml && (
                <div className="p-3 bg-muted/60 rounded-xl border space-y-1.5 font-mono text-xs overflow-x-auto">
                  <p className="text-[11px] font-semibold text-muted-foreground font-sans uppercase">Carrier Response XML:</p>
                  <pre className="text-xs text-foreground whitespace-pre-wrap">{ivrPreviewXml}</pre>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 4: Lead Qualification */}
        <TabsContent value="qualification" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-lg">Customer Qualification Questions</CardTitle>
                <CardDescription>Custom fields and questions the agent will ask during the conversation.</CardDescription>
              </div>
              <Button size="sm" onClick={addQualificationField} variant="outline" className="gap-1 text-xs">
                <Plus className="h-3.5 w-3.5" />
                Add Field
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {(formData.qualification_fields || []).map((f, idx) => (
                <div key={idx} className="p-3 border rounded-xl space-y-3 bg-card/60">
                  <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">Field Key</Label>
                      <Input
                        value={f.key}
                        onChange={(e) => {
                          const updated = [...formData.qualification_fields];
                          updated[idx].key = e.target.value;
                          setFormData({ ...formData, qualification_fields: updated });
                        }}
                      />
                    </div>
                    <div className="sm:col-span-2 space-y-1">
                      <Label className="text-xs">Display Label</Label>
                      <Input
                        value={f.label}
                        onChange={(e) => {
                          const updated = [...formData.qualification_fields];
                          updated[idx].label = e.target.value;
                          setFormData({ ...formData, qualification_fields: updated });
                        }}
                      />
                    </div>
                    <div className="flex items-center gap-3 pt-6">
                      <Label className="text-xs">Required?</Label>
                      <Switch
                        checked={f.required}
                        onCheckedChange={(val) => {
                          const updated = [...formData.qualification_fields];
                          updated[idx].required = val;
                          setFormData({ ...formData, qualification_fields: updated });
                        }}
                      />
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex-1 space-y-1">
                      <Label className="text-xs">Spoken Question Prompt</Label>
                      <Input
                        value={f.question}
                        onChange={(e) => {
                          const updated = [...formData.qualification_fields];
                          updated[idx].question = e.target.value;
                          setFormData({ ...formData, qualification_fields: updated });
                        }}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:bg-destructive/10 mt-5 shrink-0"
                      onClick={() => removeQualificationField(idx)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 5: Objections & Bookings */}
        <TabsContent value="objections" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between pb-3">
              <div>
                <CardTitle className="text-lg">Objection Handling</CardTitle>
                <CardDescription>Coach your agent on how to handle price resistance or customer doubts.</CardDescription>
              </div>
              <Button size="sm" onClick={addObjection} variant="outline" className="gap-1 text-xs">
                <Plus className="h-3.5 w-3.5" />
                Add Objection
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {(formData.objection_handling || []).map((o, idx) => (
                <div key={idx} className="p-3 border rounded-xl space-y-2 bg-card/60">
                  <div className="space-y-1">
                    <Label className="text-xs text-amber-600 font-semibold">Caller Objection</Label>
                    <Input
                      value={o.objection}
                      onChange={(e) => {
                        const updated = [...formData.objection_handling];
                        updated[idx].objection = e.target.value;
                        setFormData({ ...formData, objection_handling: updated });
                      }}
                    />
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="flex-1 space-y-1">
                      <Label className="text-xs text-emerald-600 font-semibold">Agent Reassuring Response</Label>
                      <Input
                        value={o.response}
                        onChange={(e) => {
                          const updated = [...formData.objection_handling];
                          updated[idx].response = e.target.value;
                          setFormData({ ...formData, objection_handling: updated });
                        }}
                      />
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:bg-destructive/10 mt-5 shrink-0"
                      onClick={() => removeObjection(idx)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Appointment & Escalation Rules</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2 border p-3 rounded-xl">
                  <div className="flex items-center justify-between">
                    <Label className="font-semibold text-sm">Appointment Booking</Label>
                    <Switch
                      checked={formData.appointment_rules?.enabled ?? true}
                      onCheckedChange={(val) =>
                        setFormData({
                          ...formData,
                          appointment_rules: { ...(formData.appointment_rules || { title: "", slots_description: "", requires_confirmation: true }), enabled: val },
                        })
                      }
                    />
                  </div>
                  <Input
                    placeholder="Appointment Title (e.g. Site Visit)"
                    value={formData.appointment_rules?.title || ""}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        appointment_rules: { ...(formData.appointment_rules || { enabled: true, slots_description: "", requires_confirmation: true }), title: e.target.value },
                      })
                    }
                  />
                  <Input
                    placeholder="Slot description (e.g. 10 AM to 6 PM daily)"
                    value={formData.appointment_rules?.slots_description || ""}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        appointment_rules: { ...(formData.appointment_rules || { enabled: true, title: "", requires_confirmation: true }), slots_description: e.target.value },
                      })
                    }
                  />
                </div>

                <div className="space-y-2 border p-3 rounded-xl">
                  <div className="flex items-center justify-between">
                    <Label className="font-semibold text-sm">Human Escalation</Label>
                    <Switch
                      checked={formData.escalation_rules?.enabled ?? true}
                      onCheckedChange={(val) =>
                        setFormData({
                          ...formData,
                          escalation_rules: { ...(formData.escalation_rules || { trigger_words: [], action: "", staff_contact: "" }), enabled: val },
                        })
                      }
                    />
                  </div>
                  <Input
                    placeholder="Staff Phone (e.g. +91-9876543210)"
                    value={formData.escalation_rules?.staff_contact || ""}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        escalation_rules: { ...(formData.escalation_rules || { enabled: true, trigger_words: [], action: "transfer" }), staff_contact: e.target.value },
                      })
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    Trigger keywords: manager, speak to staff, real person, complaint.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* TAB 6: Knowledge & Policies */}
        <TabsContent value="knowledge" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Business Knowledge, FAQs & Policies</CardTitle>
              <CardDescription>
                Provide detailed facts, bank tie-ups, delivery guarantees, or warranty information.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Textarea
                rows={10}
                value={formData.knowledge_text}
                onChange={(e) => setFormData({ ...formData, knowledge_text: e.target.value })}
                placeholder="Enter company policies, FAQs, bank approvals, RERA details, return policies..."
                className="font-mono text-xs leading-relaxed"
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

