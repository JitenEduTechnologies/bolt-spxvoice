"use client";

import { Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";

import RecordingsList from "./RecordingsList";
import { RecordingsUploadDialog } from "./RecordingsUploadDialog";

export default function RecordingsPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const [isUploadOpen, setIsUploadOpen] = useState(false);
    const [refreshKey, setRefreshKey] = useState(0);

    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    if (loading || !user) {
        return (
            <div className="container mx-auto px-4 py-8">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-full" />
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-8">
                <div className="flex items-center gap-3 mb-2">
                    <h1 className="text-3xl font-bold">Audio Library & Prompt Assets</h1>
                    <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                        Prompt Audio Assets
                    </span>
                </div>
                <p className="text-muted-foreground text-sm">
                    Manage reusable voice audio assets (greetings, prompts, disclaimers, voicemails) shared across your organization.
                    Insert them directly into workflow nodes using <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">@recording_id</code>.
                </p>
                <div className="mt-3 text-xs text-muted-foreground flex items-center gap-2 bg-muted/40 p-2.5 rounded-lg border border-border">
                    <span className="font-semibold text-foreground">Call Session Recordings:</span>
                    <span>Customer telephone and web conversation recordings are attached to specific runs. To listen to or download call recordings, visit <a href="/usage" className="underline hover:text-foreground">Agent Runs</a> or <a href="/reports" className="underline hover:text-foreground">Reports</a>.</span>
                </div>
            </div>

            <Card>
                <CardHeader>
                    <div className="flex justify-between items-center">
                        <div>
                            <CardTitle>Prompt Audio Library</CardTitle>
                            <CardDescription>
                                Reusable audio recordings available across all voice agents in your organization
                            </CardDescription>
                        </div>
                        <Button onClick={() => setIsUploadOpen(true)}>
                            <Upload className="w-4 h-4 mr-2" />
                            Upload Recording
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <RecordingsList refreshKey={refreshKey} />
                </CardContent>
            </Card>

            <RecordingsUploadDialog
                open={isUploadOpen}
                onOpenChange={setIsUploadOpen}
                onUploadComplete={() => setRefreshKey((k) => k + 1)}
            />
        </div>
    );
}
