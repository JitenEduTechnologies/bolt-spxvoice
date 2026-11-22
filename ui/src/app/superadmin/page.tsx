"use client";

import { Activity, ArrowRight, Calculator, List, Loader2, ShieldAlert } from 'lucide-react';
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth, type LocalUser } from '@/lib/auth';
import { impersonateAsSuperadmin } from "@/lib/utils";

export default function SuperadminPage() {
    const [userId, setUserId] = useState("");
    const [error, setError] = useState("");
    const [isLoading, setIsLoading] = useState(false);
    const { user, getAccessToken } = useAuth();

    const handleImpersonate = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setIsLoading(true);

        try {
            if (!user) {
                setError("User not authenticated. Please log in and try again.");
                setIsLoading(false);
                return;
            }
            const accessToken = await getAccessToken();
            if (!accessToken) {
                throw new Error('Missing admin access token');
            }

            await impersonateAsSuperadmin({
                accessToken: accessToken,
                providerUserId: userId,
                redirectPath: '/workflow',
                openInNewTab: true,
            });
        } catch (err) {
            setError("Failed to impersonate user. Please try again.");
            console.error("Impersonation error:", err);
        } finally {
            setIsLoading(false);
        }
    };

    const isSuperuser = Boolean((user as LocalUser | undefined)?.is_superuser);

    if (!user || !isSuperuser) {
        return (
            <div className="container mx-auto p-6 max-w-lg min-h-[60vh] flex items-center justify-center">
                <Card className="w-full p-8 text-center space-y-4 border-amber-500/30 shadow-lg">
                    <div className="w-12 h-12 rounded-full bg-amber-500/10 text-amber-600 flex items-center justify-center mx-auto">
                        <ShieldAlert className="h-6 w-6" />
                    </div>
                    <CardTitle className="text-xl">Superadministrator Access Required</CardTitle>
                    <CardDescription className="text-sm text-muted-foreground">
                        This administrative page requires elevated platform privileges.
                        Business owners can manage voice agents and review analytics from the main navigation.
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

    return (
        <>
            <main className="container mx-auto p-6 space-y-6 max-w-5xl">
                <div className="text-center">
                    <h1 className="text-3xl font-bold mb-2">Superadmin Dashboard</h1>
                    <p className="text-sm text-muted-foreground">Manage users, monitor platform health, and view system-wide data</p>
                </div>

                <div className="grid gap-6 md:grid-cols-2">
                    {/* Health & Audit Center Card */}
                    <Card className="border-primary/40 bg-card shadow-sm hover:border-primary/60 transition-all">
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Activity className="h-5 w-5 text-primary" />
                                Health & Audit Center
                            </CardTitle>
                            <CardDescription>
                                Real-time diagnostics, host telemetry, carrier SIP connectivity, and durable audit logs
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <p className="text-sm text-muted-foreground">
                                    Full observability into live dependencies, LiveKit voice worker runtime, Call Diagnostic Timeline, and centralized audit events.
                                </p>
                                <Link href="/superadmin/health-audit">
                                    <Button className="w-full">
                                        <Activity className="mr-2 h-4 w-4" />
                                        Open Health & Audit Center
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Cost Analytics Card */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <Calculator className="h-5 w-5 text-emerald-600" />
                                Cost Analytics
                            </CardTitle>
                            <CardDescription>
                                View provider spend, telephony CDR costs, and usage trends
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <p className="text-sm text-muted-foreground">
                                    Review per-conversation API costs, realtime and LLM usage, and CDR charges.
                                </p>
                                <Link href="/superadmin/costs">
                                    <Button className="w-full" variant="outline">
                                        <Calculator className="mr-2 h-4 w-4" />
                                        View Costs
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Workflow Runs Card */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <List className="h-5 w-5" />
                                Workflow Runs
                            </CardTitle>
                            <CardDescription>
                                View and manage all workflow runs across organizations
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <div className="space-y-4">
                                <p className="text-sm text-muted-foreground">
                                    Access detailed information about all workflow runs, including status,
                                    recordings, transcripts, and usage data.
                                </p>
                                <Link href="/superadmin/runs">
                                    <Button className="w-full" variant="outline">
                                        <List className="mr-2 h-4 w-4" />
                                        View All Runs
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </Link>
                            </div>
                        </CardContent>
                    </Card>

                    {/* User Impersonation Card */}
                    <Card>
                        <CardHeader>
                            <CardTitle>User Impersonation</CardTitle>
                            <CardDescription>
                                Impersonate a user account for debugging or support purposes
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <form onSubmit={handleImpersonate} className="space-y-4">
                                <div className="space-y-2">
                                    <Label htmlFor="userId">Provider User ID</Label>
                                    <Input
                                        id="userId"
                                        value={userId}
                                        onChange={(e) => setUserId(e.target.value)}
                                        placeholder="Enter provider user ID"
                                        required
                                    />
                                </div>

                                {error && (
                                    <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-lg text-sm">
                                        {error}
                                    </div>
                                )}

                                <Button
                                    type="submit"
                                    disabled={isLoading}
                                    className="w-full"
                                    variant="outline"
                                >
                                    {isLoading ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Processing...
                                        </>
                                    ) : (
                                        'Impersonate User'
                                    )}
                                </Button>
                            </form>
                        </CardContent>
                    </Card>
                </div>
            </main>
        </>
    );
}
