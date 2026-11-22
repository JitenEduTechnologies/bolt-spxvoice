"use client";

import { ArrowRight, ExternalLink, Sparkles } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import { Badge } from "@/components/ui/badge";
import { useUserConfig } from "@/context/UserConfigContext";

interface ServiceConfigurationProps {
    docsUrl?: string;
}

export default function ServiceConfiguration({ docsUrl }: ServiceConfigurationProps) {
    const { saveUserConfig } = useUserConfig();

    return (
        <div className="w-full max-w-5xl mx-auto space-y-6">
            <div className="space-y-1">
                <div className="flex items-center gap-2">
                    <h1 className="text-3xl font-bold tracking-tight">AI Models & Providers</h1>
                    <Badge variant="secondary" className="text-xs font-semibold px-2 py-0.5 bg-muted text-muted-foreground border border-border/60">
                        Administrator
                    </Badge>
                </div>
                <p className="text-muted-foreground text-sm sm:text-base">
                    Configure low-level provider API keys, custom model endpoints, and voice/transcription engines across the platform.{" "}
                    {docsUrl && (
                        <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">
                            Documentation <ExternalLink className="h-3 w-3" />
                        </a>
                    )}
                </p>
            </div>

            {/* Explanatory Banner: Owner Modes vs Administrator Settings */}
            <div className="rounded-xl border border-border/70 bg-card p-4 sm:p-5 shadow-xs transition-colors">
                <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-2">
                            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
                                <Sparkles className="h-4 w-4" />
                            </span>
                            <h3 className="font-semibold text-sm sm:text-base text-foreground">
                                Looking for Standard, Premium, or Budget Voice Modes?
                            </h3>
                        </div>
                        <p className="text-xs sm:text-sm text-muted-foreground max-w-2xl leading-relaxed">
                            <strong className="text-foreground font-medium">Business owners:</strong> Switch conversational tiers, adjust speed, volume, and latency presets in <span className="font-medium text-foreground">Train My Agent → Voice & Mode</span>. This page is strictly for technical API keys and direct provider connections.
                        </p>
                    </div>
                    <Link
                        href="/agent"
                        className="inline-flex items-center gap-1.5 shrink-0 rounded-lg bg-primary/10 hover:bg-primary/15 text-primary px-3.5 py-2 text-xs font-medium border border-primary/20 transition-colors"
                    >
                        <span>Train My Agent</span>
                        <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                </div>
            </div>

            <ServiceConfigurationForm
                mode="global"
                onSave={async (config) => {
                    try {
                        await saveUserConfig(config);
                    } catch (error) {
                        // The form shows the message inline; the toast makes a
                        // failed save visible even if the form is scrolled away.
                        toast.error(
                            error instanceof Error && error.message
                                ? error.message
                                : "Failed to save configuration"
                        );
                        throw error;
                    }
                    toast.success("Configuration saved successfully");
                }}
            />
        </div>
    );
}
