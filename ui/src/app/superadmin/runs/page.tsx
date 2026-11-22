"use client";

import { Activity, AlertTriangle, ArrowDown, ArrowUp, ArrowUpDown, CheckCircle, ChevronLeft, ChevronRight, Cpu, Database, ExternalLink, FileText, HardDrive, Info, Loader2, Network, PhoneCall, PhoneOff, Receipt, RefreshCw, Server, ShieldAlert } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import React, { useCallback, useEffect, useState } from "react";

import { getWorkflowRunsApiV1SuperuserWorkflowRunsGet } from '@/client/sdk.gen';
import { FilterBuilder } from "@/components/filters/FilterBuilder";
import { MediaPreviewButton, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAuth, type LocalUser } from '@/lib/auth';
import{ superadminFilterAttributes } from "@/lib/filterAttributes";
import { decodeFiltersFromURL, encodeFiltersToURL } from '@/lib/filters';
import { impersonateAsSuperadmin } from '@/lib/utils';
import { ActiveFilter } from '@/types/filters';

interface WorkflowRun {
    id: number;
    name: string;
    workflow_id: number;
    workflow_name?: string;
    user_id?: number;
    organization_id?: number;
    organization_name?: string;
    mode: string;
    is_completed: boolean;
    recording_url?: string;
    transcript_url?: string;
    usage_info?: Record<string, unknown>;
    cost_info?: Record<string, unknown>;
    initial_context?: Record<string, unknown>;
    gathered_context?: Record<string, unknown>;
    created_at: string;
}

interface WorkflowRunsResponse {
    workflow_runs: WorkflowRun[];
    total_count: number;
    page: number;
    limit: number;
    total_pages: number;
}

interface ActualCostComponent {
    service?: string;
    provider?: string;
    model?: string;
    label?: string;
    currency?: string;
    cost_usd?: number;
    cost_inr?: number;
    estimated?: boolean;
    priced?: boolean;
    usage?: Record<string, unknown>;
    pricing?: Record<string, unknown>;
    note?: string;
    warning?: string;
    source_url?: string;
}

interface ActualCostInfo {
    total_usd?: number;
    ai_total_usd?: number;
    telephony_total_usd?: number;
    total_inr?: number;
    ai_total_inr?: number;
    telephony_total_inr?: number;
    estimated?: boolean;
    components?: ActualCostComponent[];
    warnings?: string[];
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
    typeof value === 'object' && value !== null && !Array.isArray(value)
);

const numberValue = (value: unknown): number | null => {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() !== '') {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }
    return null;
};

const formatUsd = (value: number | null | undefined) => (
    typeof value === 'number' && Number.isFinite(value)
        ? new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            minimumFractionDigits: value < 1 ? 4 : 2,
            maximumFractionDigits: value < 1 ? 6 : 2,
        }).format(value)
        : '-'
);

const formatInr = (value: number | null | undefined) => (
    typeof value === 'number' && Number.isFinite(value)
        ? new Intl.NumberFormat('en-IN', {
            style: 'currency',
            currency: 'INR',
            minimumFractionDigits: value < 1 ? 4 : 2,
            maximumFractionDigits: value < 1 ? 4 : 2,
        }).format(value)
        : '-'
);

const formatInrPrimary = (inr: number | null | undefined, usd?: number | null | undefined): string => {
    const inrLabel = formatInr(inr);
    return inrLabel !== '-' ? inrLabel : formatUsd(usd);
};

const getActualCost = (costInfo?: Record<string, unknown>): ActualCostInfo | null => {
    if (!costInfo) return null;
    const actualCost = costInfo.actual_cost;
    if (isRecord(actualCost)) {
        return normalizeActualCost(actualCost as ActualCostInfo);
    }
    const legacyTotal = numberValue(costInfo.total_cost_usd);
    if (legacyTotal !== null) {
        return {
            total_usd: legacyTotal,
            ai_total_usd: legacyTotal,
            components: [],
        };
    }
    return null;
};

const normalizeActualCost = (actualCost: ActualCostInfo): ActualCostInfo => {
    const components = Array.isArray(actualCost.components) ? actualCost.components : [];
    if (components.length === 0) return actualCost;

    const sum = (predicate: (component: ActualCostComponent) => boolean, field: 'cost_inr' | 'cost_usd') => (
        components.reduce((total, component) => {
            if (!predicate(component)) return total;
            return total + (numberValue(component[field]) ?? 0);
        }, 0)
    );
    const isTelephony = (component: ActualCostComponent) => component.service === 'telephony';
    const nonTelephony = (component: ActualCostComponent) => !isTelephony(component);

    return {
        ...actualCost,
        total_inr: numberValue(actualCost.total_inr) ?? sum(() => true, 'cost_inr'),
        ai_total_inr: numberValue(actualCost.ai_total_inr) ?? sum(nonTelephony, 'cost_inr'),
        telephony_total_inr: numberValue(actualCost.telephony_total_inr) ?? sum(isTelephony, 'cost_inr'),
        total_usd: numberValue(actualCost.total_usd) ?? sum(() => true, 'cost_usd'),
        ai_total_usd: numberValue(actualCost.ai_total_usd) ?? sum(nonTelephony, 'cost_usd'),
        telephony_total_usd: numberValue(actualCost.telephony_total_usd) ?? sum(isTelephony, 'cost_usd'),
    };
};

const componentName = (component: ActualCostComponent): string => {
    if (component.label) return component.label;
    return [component.service, component.provider, component.model]
        .filter(Boolean)
        .join(' / ') || 'Cost component';
};

const componentUsageText = (component: ActualCostComponent): string => {
    if (!component.usage) return '';
    const entries = Object.entries(component.usage)
        .filter(([, value]) => value !== undefined && value !== null && value !== '')
        .slice(0, 4);
    return entries.map(([key, value]) => `${key}: ${String(value)}`).join(', ');
};

const componentCostLabel = (component: ActualCostComponent): string => (
    formatInrPrimary(numberValue(component.cost_inr), numberValue(component.cost_usd))
);

const getRunDurationSeconds = (run: WorkflowRun): number | null => (
    numberValue(run.cost_info?.call_duration_seconds)
    ?? numberValue(run.usage_info?.call_duration_seconds)
);

const formatDurationSeconds = (seconds: number | null): string => {
    if (seconds === null) return '-';
    if (seconds < 60) return `${seconds.toFixed(2)}s`;
    return `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`;
};

const getProviderCostPerMinuteLabel = (
    actualCost: ActualCostInfo | null,
    durationSeconds: number | null,
): string => {
    if (!actualCost || durationSeconds === null || durationSeconds <= 0) return '-';
    const minutes = durationSeconds / 60;
    const totalInr = numberValue(actualCost.total_inr);
    const totalUsd = numberValue(actualCost.total_usd);
    return `${formatInrPrimary(
        totalInr === null ? null : totalInr / minutes,
        totalUsd === null ? null : totalUsd / minutes,
    )}/min`;
};


export default function RunsPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const [runs, setRuns] = useState<WorkflowRun[]>([]);
    const [currentPage, setCurrentPage] = useState(() => {
        const pageParam = searchParams.get('page');
        return pageParam ? parseInt(pageParam, 10) : 1;
    });
    const [totalPages, setTotalPages] = useState(1);
    const [totalCount, setTotalCount] = useState(0);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState("");
    const [isExecutingFilters, setIsExecutingFilters] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(false);
    const [isAutoRefreshing, setIsAutoRefreshing] = useState(false);
    const limit = 50;

    // Initialize filters from URL
    const [activeFilters, setActiveFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, superadminFilterAttributes);
    });

    // Applied filters are the ones actually used for fetching (only updated on Apply click)
    const [appliedFilters, setAppliedFilters] = useState<ActiveFilter[]>(() => {
        return decodeFiltersFromURL(searchParams, superadminFilterAttributes);
    });

    // Sort state (initialized from URL)
    const [sortBy, setSortBy] = useState<string | null>(() => {
        return searchParams.get('sort_by') || null;
    });
    const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>(() => {
        const order = searchParams.get('sort_order');
        return order === 'asc' ? 'asc' : 'desc';
    });

    const [selectedRowId, setSelectedRowId] = useState<number | null>(null);

    const auth = useAuth();
    const isSuperuser = Boolean((auth.user as LocalUser | undefined)?.is_superuser);

    const isAxiomConfigured = Boolean(
        process.env.NEXT_PUBLIC_AXIOM_ORG_ID &&
        process.env.NEXT_PUBLIC_AXIOM_LOG_DATASET
    );
    const isLangfuseConfigured = Boolean(
        process.env.NEXT_PUBLIC_LANGFUSE_ENDPOINT &&
        process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID
    );
    const isStackAuthConfigured = Boolean(
        process.env.NEXT_PUBLIC_STACK_PROJECT_ID
    );

    // Media preview dialog
    const mediaPreview = MediaPreviewDialog();

    // Native Run Logs Modal state
    const [logsModalRun, setLogsModalRun] = useState<WorkflowRun | null>(null);
    const [logsLoading, setLogsLoading] = useState(false);
    const [logsEvents, setLogsEvents] = useState<any[]>([]);
    const [logsError, setLogsError] = useState<string | null>(null);
    const [logsSeverity, setLogsSeverity] = useState<string>("");
    const [expandedLogId, setExpandedLogId] = useState<string | null>(null);

    // Native Call Diagnostics Timeline Modal state
    const [traceModalRun, setTraceModalRun] = useState<WorkflowRun | null>(null);
    const [traceLoading, setTraceLoading] = useState(false);
    const [timelineData, setTimelineData] = useState<any>(null);
    const [traceError, setTraceError] = useState<string | null>(null);

    const fetchRunLogs = useCallback(async (run: WorkflowRun, sev?: string) => {
        setLogsLoading(true);
        setLogsError(null);
        try {
            const token = await auth.getAccessToken();
            const params = new URLSearchParams();
            params.set("run_id", String(run.id));
            const callId = (run.initial_context?.call_id as string) || run.name || "";
            if (callId) params.set("call_id", String(callId));
            if (sev) params.set("severity", sev);
            params.set("limit", "100");

            const res = await fetch(`/api/v1/health/logs?${params.toString()}`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });
            if (!res.ok) {
                throw new Error(`Failed to load logs (HTTP ${res.status})`);
            }
            const data = await res.json();
            setLogsEvents(data.events || []);
        } catch (err: any) {
            console.error("Run logs fetch error:", err);
            setLogsError(err.message || "Failed to load run logs");
        } finally {
            setLogsLoading(false);
        }
    }, [auth]);

    const fetchRunTrace = useCallback(async (run: WorkflowRun) => {
        setTraceLoading(true);
        setTraceError(null);
        setTimelineData(null);
        try {
            const token = await auth.getAccessToken();
            const callIdentifier = (run.initial_context?.call_id as string) || run.name || run.id;
            const res = await fetch(`/api/v1/health/calls/${encodeURIComponent(callIdentifier)}/timeline`, {
                headers: {
                    Authorization: `Bearer ${token}`,
                },
            });
            if (!res.ok) {
                throw new Error(`Failed to load diagnostic timeline (HTTP ${res.status})`);
            }
            const data = await res.json();
            setTimelineData(data);
        } catch (err: any) {
            console.error("Run trace timeline fetch error:", err);
            setTraceError(err.message || "Failed to load call diagnostic timeline");
        } finally {
            setTraceLoading(false);
        }
    }, [auth]);

    const getMilestoneIcon = (milestone: string) => {
        switch (milestone) {
            case 'call_initiated':
                return <PhoneCall className="h-4 w-4 text-blue-500" />;
            case 'carrier_sip_session':
                return <Network className="h-4 w-4 text-indigo-500" />;
            case 'livekit_worker_lifecycle':
                return <Server className="h-4 w-4 text-purple-500" />;
            case 'voice_engine_runtime':
                return <Cpu className="h-4 w-4 text-cyan-500" />;
            case 'crm_business_context':
                return <Database className="h-4 w-4 text-emerald-500" />;
            case 'recording_storage':
                return <HardDrive className="h-4 w-4 text-amber-500" />;
            case 'call_disconnect_outcome':
                return <PhoneOff className="h-4 w-4 text-orange-500" />;
            case 'billing_and_reconciliation':
                return <Receipt className="h-4 w-4 text-green-500" />;
            default:
                return <Info className="h-4 w-4 text-muted-foreground" />;
        }
    };

    const getStatusBadge = (status: string) => {
        const s = String(status || '').toUpperCase();
        if (['HEALTHY', 'COMPLETED', 'STORED', 'RECONCILED', 'CAPTURED', 'CONNECTED'].includes(s)) {
            return <Badge className="bg-emerald-600/20 text-emerald-400 border-emerald-500/30 text-xs">{s}</Badge>;
        }
        if (['WARNING', 'DEGRADED', 'ESTIMATED'].includes(s)) {
            return <Badge className="bg-amber-600/20 text-amber-400 border-amber-500/30 text-xs">{s}</Badge>;
        }
        if (['FAILED', 'CRITICAL', 'ERROR'].includes(s)) {
            return <Badge className="bg-red-600/20 text-red-400 border-red-500/30 text-xs">{s}</Badge>;
        }
        return <Badge variant="outline" className="text-muted-foreground text-xs">{s || 'NOT RECORDED'}</Badge>;
    };

    const fetchRuns = useCallback(async (
        page: number,
        filters?: ActiveFilter[],
        isAutoRefresh = false,
        sortByParam?: string | null,
        sortOrderParam?: 'asc' | 'desc'
    ) => {
        if (!auth.isAuthenticated) return;

        // Don't show loading state for auto-refresh to prevent UI flicker
        if (!isAutoRefresh) {
            setIsLoading(true);
        } else {
            setIsAutoRefreshing(true);
        }
        setError("");

        try {
            let filterParam = undefined;
            if (filters && filters.length > 0) {
                const filterData = filters.map(filter => ({
                    attribute: filter.attribute.id,
                    type: filter.attribute.type,
                    value: filter.value,
                }));
                filterParam = JSON.stringify(filterData);
            }

            const response = await getWorkflowRunsApiV1SuperuserWorkflowRunsGet({
                query: {
                    page,
                    limit,
                    ...(filterParam && { filters: filterParam }),
                    ...(sortByParam && { sort_by: sortByParam }),
                    ...(sortOrderParam && { sort_order: sortOrderParam }),
                },
            });

            if (response.data) {
                const data = response.data as WorkflowRunsResponse;
                setRuns(data.workflow_runs);
                setCurrentPage(data.page);
                setTotalPages(data.total_pages);
                setTotalCount(data.total_count);
            }
        } catch (err) {
            setError("Failed to fetch workflow runs. Please try again.");
            console.error("Fetch runs error:", err);
        } finally {
            if (!isAutoRefresh) {
                setIsLoading(false);
            } else {
                setIsAutoRefreshing(false);
            }
        }
    }, [limit, auth.isAuthenticated]);

    const updatePageInUrl = useCallback((page: number, filters?: ActiveFilter[], sortByParam?: string | null, sortOrderParam?: 'asc' | 'desc') => {
        const params = new URLSearchParams();
        params.set('page', page.toString());

        // Add filters to URL if present
        if (filters && filters.length > 0) {
            const filterString = encodeFiltersToURL(filters);
            if (filterString) {
                const filterParams = new URLSearchParams(filterString);
                filterParams.forEach((value, key) => params.set(key, value));
            }
        }

        // Add sort to URL if present
        if (sortByParam) {
            params.set('sort_by', sortByParam);
            params.set('sort_order', sortOrderParam || 'desc');
        }

        router.push(`/superadmin/runs?${params.toString()}`);
    }, [router]);

    useEffect(() => {
        // Fetch runs when auth is available and when page/sort changes
        if (auth.isAuthenticated) {
            fetchRuns(currentPage, appliedFilters, false, sortBy, sortOrder);
        }
    }, [currentPage, auth.isAuthenticated, appliedFilters, fetchRuns, sortBy, sortOrder]);

    // Auto-refresh every 5 seconds when enabled and filters are active
    useEffect(() => {
        // Only set up interval if auto-refresh is enabled and there are applied filters
        if (!autoRefresh || appliedFilters.length === 0) {
            return;
        }

        const intervalId = setInterval(() => {
            // Pass true to indicate this is an auto-refresh
            fetchRuns(currentPage, appliedFilters, true, sortBy, sortOrder);
        }, 5000);

        // Cleanup interval on unmount or when dependencies change
        return () => clearInterval(intervalId);
    }, [currentPage, appliedFilters, fetchRuns, autoRefresh, sortBy, sortOrder]);

    const handlePageChange = (page: number) => {
        setCurrentPage(page);
        updatePageInUrl(page, appliedFilters, sortBy, sortOrder);
        fetchRuns(page, appliedFilters, false, sortBy, sortOrder);
    };

    const handleApplyFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1); // Reset to first page when applying filters
        setAppliedFilters(activeFilters); // Update applied filters
        updatePageInUrl(1, activeFilters, sortBy, sortOrder);
        await fetchRuns(1, activeFilters, false, sortBy, sortOrder);
        setIsExecutingFilters(false);
    }, [activeFilters, fetchRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleFiltersChange = useCallback((filters: ActiveFilter[]) => {
        setActiveFilters(filters);
    }, []);

    const handleClearFilters = useCallback(async () => {
        setIsExecutingFilters(true);
        setCurrentPage(1);
        setAppliedFilters([]); // Clear applied filters
        updatePageInUrl(1, [], sortBy, sortOrder); // Clear filters from URL
        await fetchRuns(1, [], false, sortBy, sortOrder); // Fetch all runs without filters
        setIsExecutingFilters(false);
    }, [fetchRuns, updatePageInUrl, sortBy, sortOrder]);

    const handleSort = useCallback((field: string) => {
        // Reset to first page when sort changes
        setCurrentPage(1);

        const newSortBy = field;
        let newSortOrder: 'asc' | 'desc' = 'desc';
        if (sortBy === field) {
            newSortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
        }

        setSortBy(newSortBy);
        setSortOrder(newSortOrder);
        updatePageInUrl(1, appliedFilters, newSortBy, newSortOrder);
    }, [sortBy, sortOrder, updatePageInUrl, appliedFilters]);

    /**
     * ----------------------------------------------------------------------------------
     * Helpers
     * ----------------------------------------------------------------------------------
     */

    const formatDate = (dateString: string) => new Date(dateString).toLocaleString();

    const calculateDuration = (run: WorkflowRun) => (
        run.is_completed ? formatDurationSeconds(getRunDurationSeconds(run)) : '-'
    );


    /**
     * Wrapper around shared impersonation util – we only need to fetch the
     * current superadmin token and then delegate the heavy lifting.
     */
    const impersonateAndMaybeRedirect = useCallback(
        async (targetUserId: number | undefined, redirectPath?: string) => {
            if (!targetUserId || !auth.isAuthenticated) return;
            try {
                const token = await auth.getAccessToken();
                await impersonateAsSuperadmin({
                    accessToken: token,
                    userId: targetUserId,
                    redirectPath,
                    openInNewTab: true,
                });
            } catch (err: any) {
                console.error('Failed to impersonate user', err);
                alert(err?.message || 'Failed to impersonate the user. Please try again.');
            }
        },
        [auth],
    );

    if (isLoading && runs.length === 0) {
        return (
            <div className="container mx-auto p-6 flex items-center justify-center min-h-[400px]">
                <div className="flex items-center space-x-2">
                    <Loader2 className="h-6 w-6 animate-spin" />
                    <span>Loading workflow runs...</span>
                </div>
            </div>
        );
    }

    if (!auth.loading && (!auth.isAuthenticated || !isSuperuser)) {
        return (
            <div className="container mx-auto p-6 max-w-lg min-h-[60vh] flex items-center justify-center">
                <Card className="w-full p-8 text-center space-y-4 border-amber-500/30 shadow-lg">
                    <div className="w-12 h-12 rounded-full bg-amber-500/10 text-amber-600 flex items-center justify-center mx-auto">
                        <ShieldAlert className="h-6 w-6" />
                    </div>
                    <CardTitle className="text-xl">Superadministrator Access Required</CardTitle>
                    <CardDescription className="text-sm text-muted-foreground">
                        This all-runs technical diagnostics page is restricted to platform superadministrators.
                        Business owners can review conversations and call transcripts in{" "}
                        <Link href="/conversations" className="text-primary font-medium underline">
                            Conversations
                        </Link>.
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
        <div className="container mx-auto p-6 space-y-6 max-w-full">
            <div>
                <h1 className="text-3xl font-bold mb-2">Workflow Runs</h1>
                <p className="text-muted-foreground">View and manage all workflow runs across organizations</p>
            </div>

            {error && (
                    <div className="mb-6 bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-lg">
                        {error}
                    </div>
                )}

                <FilterBuilder
                    availableAttributes={superadminFilterAttributes}
                    activeFilters={activeFilters}
                    onFiltersChange={handleFiltersChange}
                    onApplyFilters={handleApplyFilters}
                    onClearFilters={handleClearFilters}
                    isExecuting={isExecutingFilters}
                    autoRefresh={autoRefresh}
                    onAutoRefreshChange={setAutoRefresh}
                    hasAppliedFilters={appliedFilters.length > 0}
                />

                <Card>
                    <CardHeader>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle>All Workflow Runs</CardTitle>
                                <CardDescription>
                                    Showing {runs.length} of {totalCount} total runs
                                </CardDescription>
                            </div>
                            {isAutoRefreshing && (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <RefreshCw className="h-4 w-4 animate-spin" />
                                    <span>Refreshing...</span>
                                </div>
                            )}
                        </div>
                    </CardHeader>
                    <CardContent>
                        {runs.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground">
                                No workflow runs found.
                            </div>
                        ) : (
                            <>
                                <div className="bg-card border rounded-lg overflow-hidden shadow-sm">
                                    <Table>
                                        <TableHeader>
                                            <TableRow className="bg-muted">
                                                <TableHead className="font-semibold">ID</TableHead>
                                                <TableHead className="font-semibold">Workflow</TableHead>
                                                <TableHead className="font-semibold">Status</TableHead>
                                                <TableHead className="font-semibold">Disposition</TableHead>
                                                <TableHead className="font-semibold">Tags</TableHead>
                                                <TableHead
                                                    className="font-semibold cursor-pointer hover:bg-muted/50 select-none"
                                                    onClick={() => handleSort('duration')}
                                                >
                                                    <div className="flex items-center gap-1">
                                                        Duration
                                                        {sortBy === 'duration' ? (
                                                            sortOrder === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />
                                                        ) : (
                                                            <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
                                                        )}
                                                    </div>
                                                </TableHead>
                                                <TableHead className="font-semibold">Actual Spend</TableHead>
                                                <TableHead className="font-semibold">Actual / Min</TableHead>
                                                <TableHead className="font-semibold">Details</TableHead>
                                                <TableHead
                                                    className="font-semibold cursor-pointer hover:bg-muted/50 select-none"
                                                    onClick={() => handleSort('created_at')}
                                                >
                                                    <div className="flex items-center gap-1">
                                                        Created At
                                                        {sortBy === 'created_at' ? (
                                                            sortOrder === 'asc' ? <ArrowUp className="h-4 w-4" /> : <ArrowDown className="h-4 w-4" />
                                                        ) : (
                                                            <ArrowUpDown className="h-4 w-4 text-muted-foreground" />
                                                        )}
                                                    </div>
                                                </TableHead>
                                                <TableHead className="font-semibold">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {runs.map((run) => {
                                                const actualCost = getActualCost(run.cost_info);
                                                const components = actualCost?.components ?? [];
                                                const durationSeconds = getRunDurationSeconds(run);
                                                return (
                                                <TableRow
                                                    key={run.id}
                                                    className={selectedRowId === run.id ? "bg-primary/20 ring-1 ring-primary/50" : ""}>
                                                    <TableCell className="font-mono text-sm">
                                                        <Link
                                                            href={`/workflow/${run.workflow_id}/run/${run.id}`}
                                                            className="text-primary hover:underline font-semibold inline-flex items-center gap-1"
                                                            title={`View details for run #${run.id}`}
                                                        >
                                                            #{run.id}
                                                            <ExternalLink className="h-3 w-3 opacity-60" />
                                                        </Link>
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex flex-col">
                                                            <span className="font-medium text-sm">
                                                                {run.workflow_name ? (
                                                                    run.workflow_name.length > 15
                                                                        ? `${run.workflow_name.substring(0, 15)}...`
                                                                        : run.workflow_name
                                                                ) : 'Unknown Workflow'}
                                                            </span>
                                                            <span className="text-xs text-muted-foreground font-mono">
                                                                ID: {String(run.workflow_id).length > 12
                                                                    ? `${String(run.workflow_id).substring(0, 12)}...`
                                                                    : run.workflow_id}
                                                            </span>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-center">
                                                        {run.is_completed ? (
                                                            <CheckCircle className="h-5 w-5 text-green-600" />
                                                        ) : (
                                                            <AlertTriangle className="h-5 w-5 text-yellow-500" />
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        {run.gathered_context?.mapped_call_disposition ? (
                                                            <Badge variant="default">
                                                                {run.gathered_context.mapped_call_disposition as string}
                                                            </Badge>
                                                        ) : (
                                                            <span className="text-sm text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell>
                                                        {Array.isArray(run.gathered_context?.call_tags) && run.gathered_context.call_tags.length > 0 ? (
                                                            <div className="flex flex-wrap gap-1">
                                                                {run.gathered_context.call_tags.map((tag: string) => (
                                                                    <Badge key={tag} variant="default">
                                                                        {tag}
                                                                    </Badge>
                                                                ))}
                                                            </div>
                                                        ) : (
                                                            <span className="text-sm text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-sm whitespace-pre-wrap break-words">
                                                        <span className={!run.is_completed ? "font-semibold text-blue-600" : ""}>
                                                            {calculateDuration(run)}
                                                        </span>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        {actualCost ? (
                                                            <Tooltip>
                                                                <TooltipTrigger asChild>
                                                                    <button type="button" className="flex items-center gap-1 text-left">
                                                                        <span className="font-medium">
                                                                            {formatInrPrimary(numberValue(actualCost.total_inr), numberValue(actualCost.total_usd))}
                                                                        </span>
                                                                        {actualCost.estimated && (
                                                                            <Badge variant="outline" className="text-[10px]">est</Badge>
                                                                        )}
                                                                        <Info className="h-3.5 w-3.5 text-muted-foreground" />
                                                                    </button>
                                                                </TooltipTrigger>
                                                                <TooltipContent sideOffset={4} className="max-w-lg whitespace-pre-wrap break-words">
                                                                    <div className="space-y-2 text-xs">
                                                                        <div>
                                                                            <p className="font-semibold">Actual provider spend</p>
                                                                        <p>Total: {formatInrPrimary(numberValue(actualCost.total_inr), numberValue(actualCost.total_usd))}</p>
                                                                        <p>AI: {formatInrPrimary(numberValue(actualCost.ai_total_inr), numberValue(actualCost.ai_total_usd))}</p>
                                                                        <p>Telephony: {formatInrPrimary(numberValue(actualCost.telephony_total_inr), numberValue(actualCost.telephony_total_usd))}</p>
                                                                        </div>
                                                                        {components.length > 0 && (
                                                                            <div className="space-y-1">
                                                                                {components.map((component, index) => (
                                                                                    <div key={`${component.service ?? 'cost'}-${component.provider ?? 'provider'}-${component.model ?? 'model'}-${index}`} className="border-t pt-1">
                                                                                        <div className="flex items-center justify-between gap-3">
                                                                                            <span className="font-medium">{componentName(component)}</span>
                                                                                            <span>{componentCostLabel(component)}</span>
                                                                                        </div>
                                                                                        <p className="text-muted-foreground">{component.provider}{component.model ? ` / ${component.model}` : ''}</p>
                                                                                        {componentUsageText(component) && (
                                                                                            <p className="text-muted-foreground">{componentUsageText(component)}</p>
                                                                                        )}
                                                                                        {component.estimated && <p className="text-amber-600">Estimated</p>}
                                                                                        {component.warning && <p className="text-destructive">{component.warning}</p>}
                                                                                        {component.note && <p className="text-muted-foreground">{component.note}</p>}
                                                                                        {component.source_url && <p className="text-muted-foreground">Source: {component.source_url}</p>}
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        )}
                                                                        {actualCost.warnings && actualCost.warnings.length > 0 && (
                                                                            <div className="border-t pt-1 text-destructive">
                                                                                {actualCost.warnings.map((warning, index) => (
                                                                                    <p key={`${warning}-${index}`}>{warning}</p>
                                                                                ))}
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                </TooltipContent>
                                                            </Tooltip>
                                                        ) : (
                                                            <span className="text-muted-foreground">-</span>
                                                        )}
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        {getProviderCostPerMinuteLabel(actualCost, durationSeconds)}
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        <div className="flex items-center space-x-1">
                                                            {run.gathered_context && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Info className="h-4 w-4 text-blue-500 cursor-pointer" />
                                                                    </TooltipTrigger>
                                                                    <TooltipContent sideOffset={4} className="max-w-sm whitespace-pre-wrap break-words">
                                                                        <p className="font-semibold text-xs mb-1">Gathered Context</p>
                                                                        <pre className="max-w-sm whitespace-pre-wrap break-words text-xs">
                                                                            {JSON.stringify(run.gathered_context, null, 2)}
                                                                        </pre>
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                            {run.usage_info && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Info className="h-4 w-4 text-muted-foreground cursor-pointer" />
                                                                    </TooltipTrigger>
                                                                    <TooltipContent sideOffset={4} className="max-w-sm whitespace-pre-wrap break-words">
                                                                        <p className="font-semibold text-xs mb-1">Usage Info</p>
                                                                        <pre className="max-w-sm whitespace-pre-wrap break-words text-xs">
                                                                            {JSON.stringify(run.usage_info, null, 2)}
                                                                        </pre>
                                                                    </TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                            {!run.gathered_context && !run.usage_info && (
                                                                <span className="text-muted-foreground">-</span>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                    <TableCell className="text-sm">
                                                        {formatDate(run.created_at)}
                                                    </TableCell>
                                                    <TableCell>
                                                        <div className="flex space-x-2">
                                                            <MediaPreviewButton
                                                                recordingUrl={run.recording_url}
                                                                transcriptUrl={run.transcript_url}
                                                                runId={run.id}
                                                                onOpenPreview={mediaPreview.openPreview}
                                                                onSelect={setSelectedRowId}
                                                            />
                                                            {/* Action 1: Native Run Logs Explorer + Axiom Integration */}
                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                title="View run execution logs & Axiom stream"
                                                                onClick={() => {
                                                                    setLogsModalRun(run);
                                                                    setLogsSeverity("");
                                                                    fetchRunLogs(run, "");
                                                                }}
                                                            >
                                                                <Image
                                                                    src="/axiom_icon.svg"
                                                                    alt="Run Logs"
                                                                    width={16}
                                                                    height={16}
                                                                    className="h-4 w-4"
                                                                />
                                                            </Button>

                                                            {/* Action 2: Native Call Diagnostic Timeline + Langfuse Integration */}
                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                title="View call diagnostic timeline & Langfuse traces"
                                                                onClick={() => {
                                                                    setTraceModalRun(run);
                                                                    fetchRunTrace(run);
                                                                }}
                                                            >
                                                                <Image
                                                                    src="/langfuse_icon.svg"
                                                                    alt="Call Timeline & Traces"
                                                                    width={16}
                                                                    height={16}
                                                                    className="h-4 w-4"
                                                                />
                                                            </Button>

                                                            {/* Action 3: Open Workflow (Direct in OSS, Impersonate in Stack Auth) */}
                                                            <Button
                                                                variant="outline"
                                                                size="icon"
                                                                title={
                                                                    isStackAuthConfigured
                                                                        ? "Open workflow as user (impersonate)"
                                                                        : "Open workflow in editor"
                                                                }
                                                                onClick={() => {
                                                                    if (isStackAuthConfigured) {
                                                                        const appBaseUrl = window.location.origin.includes('superadmin.')
                                                                            ? window.location.origin.replace('superadmin.', 'app.')
                                                                            : window.location.origin;
                                                                        impersonateAndMaybeRedirect(
                                                                            run.user_id,
                                                                            `${appBaseUrl}/workflow/${run.workflow_id}`,
                                                                        );
                                                                    } else {
                                                                        // In OSS mode: superadmin has direct authorization to view all workflows
                                                                        window.open(`/workflow/${run.workflow_id}`, '_blank');
                                                                    }
                                                                }}
                                                            >
                                                                <ExternalLink className="h-4 w-4" />
                                                            </Button>

                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                                );
                                            })}
                                        </TableBody>
                                    </Table>
                                </div>

                                {/* Pagination */}
                                {totalPages > 1 && (
                                    <div className="flex items-center justify-between mt-6">
                                        <div className="text-sm text-muted-foreground">
                                            Page {currentPage} of {totalPages} ({totalCount} total runs)
                                        </div>
                                        <div className="flex space-x-2">
                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage - 1)}
                                                disabled={currentPage === 1 || isLoading}
                                            >
                                                <ChevronLeft className="h-4 w-4 mr-1" />
                                                Previous
                                            </Button>

                                            {/* Page numbers */}
                                            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                                                let pageNum;
                                                if (totalPages <= 5) {
                                                    pageNum = i + 1;
                                                } else if (currentPage <= 3) {
                                                    pageNum = i + 1;
                                                } else if (currentPage >= totalPages - 2) {
                                                    pageNum = totalPages - 4 + i;
                                                } else {
                                                    pageNum = currentPage - 2 + i;
                                                }

                                                return (
                                                    <Button
                                                        key={pageNum}
                                                        variant={currentPage === pageNum ? "default" : "outline"}
                                                        size="sm"
                                                        onClick={() => handlePageChange(pageNum)}
                                                        disabled={isLoading}
                                                    >
                                                        {pageNum}
                                                    </Button>
                                                );
                                            })}

                                            <Button
                                                variant="outline"
                                                size="sm"
                                                onClick={() => handlePageChange(currentPage + 1)}
                                                disabled={currentPage === totalPages || isLoading}
                                            >
                                                Next
                                                <ChevronRight className="h-4 w-4 ml-1" />
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                    </CardContent>
                </Card>

                {/* Media Preview Dialog */}
                {mediaPreview.dialog}

                {/* Dialog 1: Native Per-Run Log Explorer Dialog */}
                <Dialog open={Boolean(logsModalRun)} onOpenChange={(open) => !open && setLogsModalRun(null)}>
                    <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col p-6 overflow-hidden">
                        <DialogHeader className="pb-2 border-b">
                            <div className="flex items-center justify-between">
                                <div>
                                    <DialogTitle className="text-lg font-bold flex items-center gap-2">
                                        <FileText className="h-5 w-5 text-blue-500" />
                                        Run Execution Logs — Run #{logsModalRun?.id}
                                    </DialogTitle>
                                    <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                                        Workflow: {logsModalRun?.workflow_name || logsModalRun?.workflow_id} | Call ID: {(logsModalRun?.initial_context?.call_id as string) || logsModalRun?.name || 'N/A'} | Mode: {logsModalRun?.mode || 'telephony'}
                                    </DialogDescription>
                                </div>
                            </div>
                        </DialogHeader>

                        {/* Axiom Cloud Telemetry Status Banner */}
                        {(() => {
                            const axiomOrg = process.env.NEXT_PUBLIC_AXIOM_ORG_ID;
                            const axiomDataset = process.env.NEXT_PUBLIC_AXIOM_LOG_DATASET;
                            const axiomQuery = logsModalRun ? encodeURIComponent(
                                JSON.stringify({
                                    children: [
                                        {
                                            field: 'extra.run_id',
                                            op: '==',
                                            value: logsModalRun.id,
                                        },
                                    ],
                                    field: '',
                                    op: 'and',
                                }),
                            ) : '';
                            const axiomUrl = (isAxiomConfigured && axiomOrg && axiomDataset && logsModalRun)
                                ? `https://app.axiom.co/${axiomOrg}/stream/${axiomDataset}?q=${axiomQuery}`
                                : null;

                            return (
                                <div className="flex items-center justify-between p-3 rounded-lg border bg-muted/40 my-1">
                                    <div className="flex items-center space-x-3">
                                        <Image src="/axiom_icon.svg" alt="Axiom" width={20} height={20} />
                                        <div>
                                            <div className="flex items-center space-x-2">
                                                <span className="font-semibold text-xs">Axiom Cloud Telemetry:</span>
                                                <Badge
                                                    variant={isAxiomConfigured ? "default" : "outline"}
                                                    className={isAxiomConfigured ? "bg-emerald-600/20 text-emerald-400 border-emerald-500/30 text-xs" : "text-muted-foreground border-dashed text-xs"}
                                                >
                                                    {isAxiomConfigured ? "CONNECTED" : "NOT CONFIGURED"}
                                                </Badge>
                                            </div>
                                            <p className="text-[11px] text-muted-foreground">
                                                {isAxiomConfigured
                                                    ? `Streaming active for dataset '${axiomDataset}'`
                                                    : "External log streaming requires NEXT_PUBLIC_AXIOM_ORG_ID and NEXT_PUBLIC_AXIOM_LOG_DATASET"}
                                            </p>
                                        </div>
                                    </div>
                                    {isAxiomConfigured && axiomUrl && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="text-xs h-7 gap-1"
                                            onClick={() => window.open(axiomUrl, "_blank")}
                                        >
                                            <span>Open in Axiom</span>
                                            <ExternalLink className="h-3 w-3" />
                                        </Button>
                                    )}
                                </div>
                            );
                        })()}

                        {/* Filters and Controls */}
                        <div className="flex items-center justify-between pt-1">
                            <div className="flex items-center space-x-2">
                                <span className="text-xs font-medium text-muted-foreground">Filter Severity:</span>
                                <select
                                    value={logsSeverity}
                                    onChange={(e) => {
                                        setLogsSeverity(e.target.value);
                                        if (logsModalRun) fetchRunLogs(logsModalRun, e.target.value);
                                    }}
                                    className="text-xs h-7 rounded-md border border-input bg-background px-2"
                                >
                                    <option value="">All Severities</option>
                                    <option value="INFO">INFO</option>
                                    <option value="WARNING">WARNING</option>
                                    <option value="ERROR">ERROR</option>
                                </select>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 w-7 p-0"
                                    title="Refresh logs"
                                    onClick={() => {
                                        if (logsModalRun) fetchRunLogs(logsModalRun, logsSeverity);
                                    }}
                                >
                                    <RefreshCw className={`h-3.5 w-3.5 ${logsLoading ? 'animate-spin' : ''}`} />
                                </Button>
                            </div>
                            <div className="text-xs text-muted-foreground">
                                {logsEvents.length} event{logsEvents.length === 1 ? '' : 's'} recorded
                            </div>
                        </div>

                        {/* Log Entries Container */}
                        <div className="flex-1 overflow-y-auto min-h-[280px] max-h-[460px] border rounded-lg bg-card/50">
                            {logsLoading ? (
                                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                                    <Loader2 className="h-6 w-6 animate-spin mb-2" />
                                    <span className="text-sm">Loading run execution logs...</span>
                                </div>
                            ) : logsError ? (
                                <div className="p-4 text-center">
                                    <div className="p-3 bg-red-500/10 border border-red-500/30 text-red-500 rounded-lg text-sm mb-3">
                                        {logsError}
                                    </div>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => logsModalRun && fetchRunLogs(logsModalRun, logsSeverity)}
                                    >
                                        Retry
                                    </Button>
                                </div>
                            ) : logsEvents.length === 0 ? (
                                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                                    <Info className="h-8 w-8 mb-2 opacity-50" />
                                    <span className="text-sm font-medium">No operational log entries recorded for this run.</span>
                                    <span className="text-xs text-muted-foreground mt-1">Events will appear here as the worker processes calls.</span>
                                </div>
                            ) : (
                                <Table>
                                    <TableHeader className="bg-muted/50 sticky top-0">
                                        <TableRow>
                                            <TableHead className="w-[170px] text-xs">Timestamp</TableHead>
                                            <TableHead className="w-[90px] text-xs">Severity</TableHead>
                                            <TableHead className="w-[110px] text-xs">Component</TableHead>
                                            <TableHead className="text-xs">Message</TableHead>
                                            <TableHead className="w-[80px] text-xs text-right">Details</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {logsEvents.map((ev, idx) => (
                                            <React.Fragment key={ev.id || idx}>
                                                <TableRow className="text-xs hover:bg-muted/30">
                                                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                                                        {ev.timestamp ? formatDate(ev.timestamp) : 'N/A'}
                                                    </TableCell>
                                                    <TableCell>
                                                        <Badge
                                                            variant="outline"
                                                            className={
                                                                ev.severity === 'ERROR' || ev.severity === 'CRITICAL'
                                                                    ? 'bg-red-500/15 text-red-400 border-red-500/30 text-[10px]'
                                                                    : ev.severity === 'WARNING'
                                                                    ? 'bg-amber-500/15 text-amber-400 border-amber-500/30 text-[10px]'
                                                                    : 'bg-blue-500/15 text-blue-400 border-blue-500/30 text-[10px]'
                                                            }
                                                        >
                                                            {ev.severity || 'INFO'}
                                                        </Badge>
                                                    </TableCell>
                                                    <TableCell className="font-mono text-[11px] font-semibold text-muted-foreground">
                                                        {ev.component || 'general'}
                                                    </TableCell>
                                                    <TableCell className="font-medium text-foreground">
                                                        {ev.message}
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        {ev.details && Object.keys(ev.details).length > 0 && (
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                className="h-6 px-1.5 text-[10px]"
                                                                onClick={() => setExpandedLogId(expandedLogId === ev.id ? null : ev.id)}
                                                            >
                                                                {expandedLogId === ev.id ? 'Hide' : 'View'}
                                                            </Button>
                                                        )}
                                                    </TableCell>
                                                </TableRow>
                                                {expandedLogId === ev.id && ev.details && (
                                                    <TableRow className="bg-muted/20">
                                                        <TableCell colSpan={5} className="p-3">
                                                            <pre className="text-[11px] font-mono bg-background p-2.5 rounded border overflow-x-auto max-h-48 text-muted-foreground">
                                                                {JSON.stringify(ev.details, null, 2)}
                                                            </pre>
                                                        </TableCell>
                                                    </TableRow>
                                                )}
                                            </React.Fragment>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </div>
                    </DialogContent>
                </Dialog>

                {/* Dialog 2: Native Per-Run Diagnostic Timeline Dialog */}
                <Dialog open={Boolean(traceModalRun)} onOpenChange={(open) => !open && setTraceModalRun(null)}>
                    <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col p-6 overflow-hidden">
                        <DialogHeader className="pb-2 border-b">
                            <div className="flex items-center justify-between">
                                <div>
                                    <DialogTitle className="text-lg font-bold flex items-center gap-2">
                                        <Activity className="h-5 w-5 text-indigo-500" />
                                        Call Diagnostics Timeline — Run #{traceModalRun?.id}
                                    </DialogTitle>
                                    <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                                        Call ID: {(traceModalRun?.initial_context?.call_id as string) || traceModalRun?.name || traceModalRun?.id} | Status: {traceModalRun?.is_completed ? 'Completed' : 'Active / Processing'}
                                    </DialogDescription>
                                </div>
                            </div>
                        </DialogHeader>

                        {/* Langfuse Tracing Status Banner */}
                        {(() => {
                            const hasDirectTraceUrl = Boolean(traceModalRun?.gathered_context?.trace_url);
                            const isLangfuseActive = hasDirectTraceUrl || isLangfuseConfigured;
                            const langfuseTraceUrl = traceModalRun
                                ? hasDirectTraceUrl
                                    ? String(traceModalRun.gathered_context?.trace_url)
                                    : isLangfuseConfigured
                                        ? `${process.env.NEXT_PUBLIC_LANGFUSE_ENDPOINT}/project/${process.env.NEXT_PUBLIC_LANGFUSE_PROJECT_ID}/traces?search=&filter=${encodeURIComponent(
                                            `metadata;stringObject;attributes;contains;conversation.id,metadata;stringObject;attributes;contains;${traceModalRun.id}`,
                                        )}&dateRange=All+time`
                                        : null
                                : null;

                            return (
                                <div className="flex items-center justify-between p-3 rounded-lg border bg-muted/40 my-1">
                                    <div className="flex items-center space-x-3">
                                        <Image src="/langfuse_icon.svg" alt="Langfuse" width={20} height={20} />
                                        <div>
                                            <div className="flex items-center space-x-2">
                                                <span className="font-semibold text-xs">Langfuse OpenTelemetry Tracing:</span>
                                                <Badge
                                                    variant={isLangfuseActive ? "default" : "outline"}
                                                    className={isLangfuseActive ? "bg-emerald-600/20 text-emerald-400 border-emerald-500/30 text-xs" : "text-muted-foreground border-dashed text-xs"}
                                                >
                                                    {isLangfuseActive ? "CONNECTED" : "NOT CONFIGURED"}
                                                </Badge>
                                            </div>
                                            <p className="text-[11px] text-muted-foreground">
                                                {isLangfuseActive
                                                    ? hasDirectTraceUrl
                                                        ? "Direct Langfuse trace linked for this call"
                                                        : "Tracing enabled via OpenTelemetry endpoint"
                                                    : "Distributed trace spans require NEXT_PUBLIC_LANGFUSE_ENDPOINT and NEXT_PUBLIC_LANGFUSE_PROJECT_ID"}
                                            </p>
                                        </div>
                                    </div>
                                    {isLangfuseActive && langfuseTraceUrl && (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            className="text-xs h-7 gap-1"
                                            onClick={() => window.open(langfuseTraceUrl, "_blank")}
                                        >
                                            <span>Open in Langfuse</span>
                                            <ExternalLink className="h-3 w-3" />
                                        </Button>
                                    )}
                                </div>
                            );
                        })()}

                        {/* Content Container */}
                        <div className="flex-1 overflow-y-auto min-h-[280px] max-h-[460px] space-y-4 pr-1">
                            {traceLoading ? (
                                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                                    <Loader2 className="h-6 w-6 animate-spin mb-2" />
                                    <span className="text-sm">Correlating call timeline milestones...</span>
                                </div>
                            ) : traceError ? (
                                <div className="p-4 text-center">
                                    <div className="p-3 bg-red-500/10 border border-red-500/30 text-red-500 rounded-lg text-sm mb-3">
                                        {traceError}
                                    </div>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => traceModalRun && fetchRunTrace(traceModalRun)}
                                    >
                                        Retry
                                    </Button>
                                </div>
                            ) : !timelineData ? (
                                <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                                    <Info className="h-8 w-8 mb-2 opacity-50" />
                                    <span className="text-sm font-medium">No call timeline recorded for this run.</span>
                                </div>
                            ) : (
                                <div className="space-y-4">
                                    {/* Summary Bar */}
                                    <div className="grid grid-cols-3 gap-3 p-3 rounded-lg border bg-muted/20">
                                        <div>
                                            <span className="text-[11px] text-muted-foreground">Call Status</span>
                                            <div className="mt-0.5">
                                                {getStatusBadge(timelineData.overall_status)}
                                            </div>
                                        </div>
                                        <div>
                                            <span className="text-[11px] text-muted-foreground">Carrier Billing</span>
                                            <div className="mt-0.5">
                                                {getStatusBadge(timelineData.billing_reconciliation_status)}
                                            </div>
                                        </div>
                                        <div>
                                            <span className="text-[11px] text-muted-foreground">Milestones Correlated</span>
                                            <div className="mt-0.5 font-semibold text-sm">
                                                {timelineData.timeline?.length || 0} stages
                                            </div>
                                        </div>
                                    </div>

                                    {/* Milestones List */}
                                    <div className="space-y-3">
                                        {timelineData.timeline?.map((ev: any, idx: number) => (
                                            <div key={idx} className="p-3 rounded-lg border bg-card/60 space-y-2 hover:border-primary/30 transition-colors">
                                                <div className="flex items-center justify-between">
                                                    <div className="flex items-center space-x-2">
                                                        {getMilestoneIcon(ev.milestone)}
                                                        <span className="font-semibold text-sm">{ev.title}</span>
                                                    </div>
                                                    <div className="flex items-center space-x-2">
                                                        {getStatusBadge(ev.status)}
                                                        <span className="text-[11px] font-mono text-muted-foreground">
                                                            {ev.timestamp ? formatDate(ev.timestamp) : 'N/A'}
                                                        </span>
                                                    </div>
                                                </div>
                                                <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-3 text-xs pt-1 border-t border-muted/50">
                                                    {Object.entries(ev.details || {}).map(([k, v]: [string, any]) => (
                                                        <div key={k} className="space-y-0.5">
                                                            <span className="text-[11px] text-muted-foreground capitalize">
                                                                {k.replace(/_/g, " ")}:
                                                            </span>
                                                            <div className="font-mono text-xs text-foreground truncate" title={typeof v === "object" ? JSON.stringify(v) : String(v)}>
                                                                {v === null || v === undefined || v === "" || v === "NOT_RECORDED"
                                                                    ? <span className="text-muted-foreground italic">NOT RECORDED</span>
                                                                    : typeof v === "object"
                                                                    ? JSON.stringify(v)
                                                                    : String(v)}
                                                            </div>
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </DialogContent>
                </Dialog>

        </div>
    );
}
