"use client";

import {
    AlertCircle,
    Archive,
    ArchiveRestore,
    ArrowUpDown,
    CheckCircle2,
    ChevronLeft,
    ChevronRight,
    Filter,
    Megaphone,
    Plus,
    RefreshCw,
    Search,
    X,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { getCampaignsApiV1CampaignGet } from '@/client/sdk.gen';
import type { CampaignResponse, CampaignsResponse } from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/lib/auth';

export default function CampaignsPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    const [campaignsData, setCampaignsData] = useState<CampaignsResponse | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Filtering, Search, Sorting, and Pagination state
    const [searchQuery, setSearchQuery] = useState('');
    const [activeTab, setActiveTab] = useState<'active' | 'archived'>('active');
    const [statusFilter, setStatusFilter] = useState<string>('all');
    const [workflowFilter, setWorkflowFilter] = useState<string>('all');
    const [sortBy, setSortBy] = useState<'created_at' | 'name' | 'id' | 'progress'>('created_at');
    const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc');
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [actionLoadingId, setActionLoadingId] = useState<number | null>(null);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const fetchCampaigns = useCallback(async () => {
        if (!user) return;
        setIsLoading(true);
        setError(null);
        try {
            const accessToken = await getAccessToken();
            // Fetch with include_archived=true so we can switch tabs instantly without extra network trips
            const response = await getCampaignsApiV1CampaignGet({
                query: {
                    include_archived: true,
                } as any,
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.error) {
                const errorDetail = (response.error as { detail?: string })?.detail || 'Failed to load campaigns';
                setError(errorDetail);
            } else if (response.data) {
                setCampaignsData(response.data);
                setError(null);
            }
        } catch (err: unknown) {
            console.error('Failed to fetch campaigns:', err);
            setError('An unexpected error occurred while loading campaigns.');
        } finally {
            setIsLoading(false);
        }
    }, [user, getAccessToken]);

    // Fetch campaigns on load
    useEffect(() => {
        if (loading || !user) {
            return;
        }
        fetchCampaigns();
    }, [loading, user, fetchCampaigns]);

    // Handle Archive / Unarchive
    const handleArchiveToggle = async (campaign: CampaignResponse, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!user) return;

        const isCurrentlyArchived = !!campaign.is_archived;
        const targetAction = isCurrentlyArchived ? 'unarchive' : 'archive';

        setActionLoadingId(campaign.id);
        try {
            const accessToken = await getAccessToken();
            const res = await fetch(`/api/v1/campaign/${campaign.id}/${targetAction}`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                    'Content-Type': 'application/json',
                },
            });

            if (res.ok) {
                toast.success(
                    isCurrentlyArchived
                        ? `Campaign #${campaign.id} restored to Active`
                        : `Campaign #${campaign.id} moved to Archive`
                );
                // Refresh list
                await fetchCampaigns();
            } else {
                const errData = await res.json().catch(() => ({}));
                toast.error(errData.detail || `Failed to ${targetAction} campaign`);
            }
        } catch (err) {
            console.error(`Failed to ${targetAction} campaign:`, err);
            toast.error(`Failed to ${targetAction} campaign`);
        } finally {
            setActionLoadingId(null);
        }
    };

    const handleRowClick = (campaignId: number) => {
        router.push(`/campaigns/${campaignId}`);
    };

    const handleCreateCampaign = () => {
        router.push('/campaigns/new');
    };

    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric',
        });
    };

    const getStateBadgeVariant = (state: string, isArchived?: boolean) => {
        if (isArchived) return 'outline';
        switch (state) {
            case 'created':
                return 'secondary';
            case 'running':
                return 'default';
            case 'paused':
                return 'outline';
            case 'completed':
                return 'secondary';
            case 'failed':
            case 'cancelled':
                return 'destructive';
            default:
                return 'secondary';
        }
    };

    const getStateLabel = (state: string, isArchived?: boolean) => {
        if (isArchived) return 'Archived';
        switch (state) {
            case 'created':
                return 'Draft';
            case 'running':
                return 'Running';
            case 'paused':
                return 'Paused';
            case 'completed':
                return 'Completed';
            case 'cancelled':
                return 'Cancelled';
            case 'failed':
                return 'Failed';
            default:
                return state;
        }
    };

    // Extract unique workflows for filter
    const uniqueWorkflows = useMemo(() => {
        if (!campaignsData?.campaigns) return [];
        const map = new Map<number, string>();
        for (const c of campaignsData.campaigns) {
            if (c.workflow_id && c.workflow_name) {
                map.set(c.workflow_id, c.workflow_name);
            }
        }
        return Array.from(map.entries()).map(([id, name]) => ({ id, name }));
    }, [campaignsData]);

    // Counts for tabs
    const { activeCount, archivedCount } = useMemo(() => {
        if (!campaignsData?.campaigns) return { activeCount: 0, archivedCount: 0 };
        let active = 0;
        let archived = 0;
        for (const c of campaignsData.campaigns) {
            if (c.is_archived) archived++;
            else active++;
        }
        return { activeCount: active, archivedCount: archived };
    }, [campaignsData]);

    // Filter, search, and sort campaigns
    const filteredCampaigns = useMemo(() => {
        if (!campaignsData?.campaigns) return [];

        let result = campaignsData.campaigns.filter((c) => {
            // Tab filter: Active vs Archived
            if (activeTab === 'active' && c.is_archived) return false;
            if (activeTab === 'archived' && !c.is_archived) return false;

            // Status filter
            if (statusFilter !== 'all') {
                if (statusFilter === 'archived' && !c.is_archived) return false;
                if (statusFilter !== 'archived' && c.state !== statusFilter) return false;
            }

            // Workflow filter
            if (workflowFilter !== 'all') {
                if (c.workflow_id !== parseInt(workflowFilter, 10)) return false;
            }

            // Search query filter (matches name, ID, or workflow)
            if (searchQuery.trim()) {
                const term = searchQuery.trim().toLowerCase();
                const nameMatch = c.name?.toLowerCase().includes(term);
                const idMatch = String(c.id).includes(term);
                const wfMatch = c.workflow_name?.toLowerCase().includes(term);
                if (!nameMatch && !idMatch && !wfMatch) return false;
            }

            return true;
        });

        // Sorting
        result.sort((a, b) => {
            let comp = 0;
            if (sortBy === 'name') {
                comp = (a.name || '').localeCompare(b.name || '');
            } else if (sortBy === 'id') {
                comp = a.id - b.id;
            } else if (sortBy === 'progress') {
                const aProg = (a.executed_count || 0) / Math.max(1, a.total_queued_count || 1);
                const bProg = (b.executed_count || 0) / Math.max(1, b.total_queued_count || 1);
                comp = aProg - bProg;
            } else {
                // Default: created_at
                comp = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
            }
            return sortOrder === 'desc' ? -comp : comp;
        });

        return result;
    }, [campaignsData, activeTab, statusFilter, workflowFilter, searchQuery, sortBy, sortOrder]);

    // Pagination calculations
    const totalFiltered = filteredCampaigns.length;
    const totalPages = Math.max(1, Math.ceil(totalFiltered / pageSize));
    const paginatedCampaigns = useMemo(() => {
        const start = (currentPage - 1) * pageSize;
        return filteredCampaigns.slice(start, start + pageSize);
    }, [filteredCampaigns, currentPage, pageSize]);

    // Reset current page when filters change
    useEffect(() => {
        setCurrentPage(1);
    }, [searchQuery, activeTab, statusFilter, workflowFilter, pageSize]);

    const clearFilters = () => {
        setSearchQuery('');
        setStatusFilter('all');
        setWorkflowFilter('all');
        setSortBy('created_at');
        setSortOrder('desc');
        setCurrentPage(1);
    };

    const hasActiveFilters = searchQuery !== '' || statusFilter !== 'all' || workflowFilter !== 'all';

    return (
        <div className="container mx-auto px-4 py-8 space-y-6">
            {/* Header */}
            <div className="relative animate-fade-in-up">
                <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
                    <div>
                        <div className="flex items-center gap-2 mb-2">
                            <div className="h-1.5 w-8 bg-gradient-to-r from-purple-500 to-pink-500 rounded-full" />
                            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Outreach</span>
                        </div>
                        <h1 className="text-3xl font-bold tracking-tight">Campaigns</h1>
                        <p className="text-muted-foreground mt-1">
                            Configure, dispatch, and monitor bulk conversational voice campaigns
                        </p>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            variant="outline"
                            size="icon"
                            onClick={() => fetchCampaigns()}
                            disabled={isLoading}
                            title="Refresh campaigns list"
                        >
                            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
                        </Button>
                        <Button onClick={handleCreateCampaign} className="gap-2 group">
                            <Plus className="h-4 w-4" />
                            Create Campaign
                        </Button>
                    </div>
                </div>
            </div>

            {/* Main Content Card */}
            <Card className="overflow-hidden border shadow-sm">
                <CardHeader className="pb-3 border-b bg-muted/10">
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                        {/* Tabs: Active vs Archived */}
                        <Tabs value={activeTab} onValueChange={(val) => setActiveTab(val as 'active' | 'archived')}>
                            <TabsList>
                                <TabsTrigger value="active" className="gap-2">
                                    <Megaphone className="h-4 w-4" />
                                    Active Campaigns ({activeCount})
                                </TabsTrigger>
                                <TabsTrigger value="archived" className="gap-2">
                                    <Archive className="h-4 w-4" />
                                    Archived ({archivedCount})
                                </TabsTrigger>
                            </TabsList>
                        </Tabs>

                        {/* Search Input */}
                        <div className="relative w-full md:w-80">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search by name, ID, or workflow..."
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                className="pl-9 pr-8"
                            />
                            {searchQuery && (
                                <button
                                    onClick={() => setSearchQuery('')}
                                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                >
                                    <X className="h-4 w-4" />
                                </button>
                            )}
                        </div>
                    </div>

                    {/* Filter & Sort Controls Row */}
                    <div className="flex flex-wrap items-center gap-3 pt-3">
                        <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
                            <Filter className="h-3.5 w-3.5" />
                            Filters:
                        </div>

                        {/* Status Filter */}
                        <Select value={statusFilter} onValueChange={setStatusFilter}>
                            <SelectTrigger className="w-[140px] h-8 text-xs">
                                <SelectValue placeholder="Status" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Statuses</SelectItem>
                                <SelectItem value="created">Draft / Created</SelectItem>
                                <SelectItem value="running">Running</SelectItem>
                                <SelectItem value="paused">Paused</SelectItem>
                                <SelectItem value="completed">Completed</SelectItem>
                                <SelectItem value="cancelled">Cancelled</SelectItem>
                            </SelectContent>
                        </Select>

                        {/* Workflow Filter */}
                        <Select value={workflowFilter} onValueChange={setWorkflowFilter}>
                            <SelectTrigger className="w-[170px] h-8 text-xs">
                                <SelectValue placeholder="Workflow" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Workflows</SelectItem>
                                {uniqueWorkflows.map((wf) => (
                                    <SelectItem key={wf.id} value={String(wf.id)}>
                                        {wf.name}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        {/* Sort Selector */}
                        <div className="ml-auto flex items-center gap-2">
                            <span className="text-xs text-muted-foreground flex items-center gap-1">
                                <ArrowUpDown className="h-3.5 w-3.5" /> Sort:
                            </span>
                            <Select value={sortBy} onValueChange={(v) => setSortBy(v as any)}>
                                <SelectTrigger className="w-[130px] h-8 text-xs">
                                    <SelectValue placeholder="Sort by" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="created_at">Date Created</SelectItem>
                                    <SelectItem value="name">Name</SelectItem>
                                    <SelectItem value="id">Campaign ID</SelectItem>
                                    <SelectItem value="progress">Progress</SelectItem>
                                </SelectContent>
                            </Select>
                            <Button
                                variant="outline"
                                size="sm"
                                className="h-8 px-2 text-xs"
                                onClick={() => setSortOrder(prev => prev === 'desc' ? 'asc' : 'desc')}
                                title={sortOrder === 'desc' ? 'Descending (Newest / High)' : 'Ascending (Oldest / Low)'}
                            >
                                {sortOrder === 'desc' ? 'Desc' : 'Asc'}
                            </Button>

                            {hasActiveFilters && (
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={clearFilters}
                                    className="h-8 px-2 text-xs text-muted-foreground hover:text-foreground"
                                >
                                    Reset
                                </Button>
                            )}
                        </div>
                    </div>
                </CardHeader>

                <CardContent className="p-0">
                    {isLoading ? (
                        <div className="p-6 space-y-4">
                            {[...Array(5)].map((_, i) => (
                                <div key={i} className="h-16 bg-muted/40 animate-pulse rounded-lg" />
                            ))}
                        </div>
                    ) : error ? (
                        <div className="text-center py-16 px-4">
                            <AlertCircle className="h-12 w-12 mx-auto text-destructive mb-3" />
                            <p className="text-lg font-medium text-destructive mb-1">Failed to load campaigns</p>
                            <p className="text-sm text-muted-foreground mb-4 max-w-md mx-auto">{error}</p>
                            <Button onClick={() => fetchCampaigns()} variant="outline" className="gap-2">
                                <RefreshCw className="h-4 w-4" />
                                Try Again
                            </Button>
                        </div>
                    ) : paginatedCampaigns.length > 0 ? (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader>
                                    <TableRow className="bg-muted/20 hover:bg-muted/20">
                                        <TableHead className="w-[80px] font-mono text-xs uppercase text-muted-foreground">ID</TableHead>
                                        <TableHead className="font-semibold text-xs uppercase text-muted-foreground">Campaign Name</TableHead>
                                        <TableHead className="font-semibold text-xs uppercase text-muted-foreground">Workflow</TableHead>
                                        <TableHead className="font-semibold text-xs uppercase text-muted-foreground">State</TableHead>
                                        <TableHead className="w-[200px] font-semibold text-xs uppercase text-muted-foreground">
                                            Outreach Progress
                                        </TableHead>
                                        <TableHead className="font-semibold text-xs uppercase text-muted-foreground">Created</TableHead>
                                        <TableHead className="text-right font-semibold text-xs uppercase text-muted-foreground">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {paginatedCampaigns.map((camp) => {
                                        const totalQueued = camp.total_queued_count || camp.total_rows || 0;
                                        const attempted = camp.executed_count || 0;
                                        const pct = totalQueued > 0 ? Math.min(100, Math.round((attempted / totalQueued) * 100)) : 0;
                                        const isActionLoading = actionLoadingId === camp.id;

                                        return (
                                            <TableRow
                                                key={camp.id}
                                                className="group cursor-pointer hover:bg-muted/30 transition-colors"
                                                onClick={() => handleRowClick(camp.id)}
                                            >
                                                {/* ID */}
                                                <TableCell className="font-mono text-xs text-muted-foreground">
                                                    #{camp.id}
                                                </TableCell>

                                                {/* Name */}
                                                <TableCell>
                                                    <div className="font-medium text-foreground group-hover:text-primary transition-colors">
                                                        {camp.name}
                                                    </div>
                                                    {camp.parent_campaign_id && (
                                                        <span className="inline-block text-[11px] text-muted-foreground">
                                                            Follow-up of #{camp.parent_campaign_id}
                                                        </span>
                                                    )}
                                                </TableCell>

                                                {/* Workflow */}
                                                <TableCell className="text-sm text-muted-foreground">
                                                    {camp.workflow_name || 'Workflow'}
                                                </TableCell>

                                                {/* State */}
                                                <TableCell>
                                                    <Badge
                                                        variant={getStateBadgeVariant(camp.state, camp.is_archived)}
                                                        className="font-medium capitalize text-xs"
                                                    >
                                                        {getStateLabel(camp.state, camp.is_archived)}
                                                    </Badge>
                                                </TableCell>

                                                {/* Outreach Progress */}
                                                <TableCell>
                                                    <div className="space-y-1">
                                                        <div className="flex justify-between text-xs text-muted-foreground">
                                                            <span>{attempted} / {totalQueued} attempted</span>
                                                            <span className="font-medium text-foreground">{pct}%</span>
                                                        </div>
                                                        <Progress value={pct} className="h-1.5" />
                                                    </div>
                                                </TableCell>

                                                {/* Created Date */}
                                                <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                                                    {formatDate(camp.created_at)}
                                                </TableCell>

                                                {/* Actions */}
                                                <TableCell className="text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                                                    <div className="flex items-center justify-end gap-1.5">
                                                        <Button
                                                            variant="ghost"
                                                            size="sm"
                                                            className="h-8 px-2.5 text-xs"
                                                            onClick={() => handleRowClick(camp.id)}
                                                        >
                                                            View
                                                        </Button>
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="h-8 w-8 text-muted-foreground hover:text-foreground"
                                                            disabled={isActionLoading || ['running', 'paused'].includes(camp.state)}
                                                            onClick={(e) => handleArchiveToggle(camp, e)}
                                                            title={
                                                                ['running', 'paused'].includes(camp.state)
                                                                    ? 'Pause/cancel campaign before archiving'
                                                                    : camp.is_archived
                                                                    ? 'Restore to Active Campaigns'
                                                                    : 'Move to Archive'
                                                            }
                                                        >
                                                            {isActionLoading ? (
                                                                <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                                                            ) : camp.is_archived ? (
                                                                <ArchiveRestore className="h-3.5 w-3.5" />
                                                            ) : (
                                                                <Archive className="h-3.5 w-3.5" />
                                                            )}
                                                        </Button>
                                                    </div>
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })}
                                </TableBody>
                            </Table>
                        </div>
                    ) : (
                        <div className="text-center py-16 px-4">
                            <Megaphone className="h-12 w-12 mx-auto text-muted-foreground/40 mb-3" />
                            {hasActiveFilters ? (
                                <>
                                    <p className="text-base font-medium mb-1">No matching campaigns</p>
                                    <p className="text-sm text-muted-foreground mb-4">
                                        No campaigns matched your current search and filter settings.
                                    </p>
                                    <Button onClick={clearFilters} variant="outline" size="sm">
                                        Clear All Filters
                                    </Button>
                                </>
                            ) : activeTab === 'archived' ? (
                                <>
                                    <p className="text-base font-medium mb-1">No archived campaigns</p>
                                    <p className="text-sm text-muted-foreground">
                                        Completed or inactive campaigns you archive will appear here.
                                    </p>
                                </>
                            ) : (
                                <>
                                    <p className="text-base font-medium mb-1">No campaigns yet</p>
                                    <p className="text-sm text-muted-foreground mb-4">
                                        Create your first bulk conversational voice campaign to start outreach.
                                    </p>
                                    <Button onClick={handleCreateCampaign} className="gap-2">
                                        <Plus className="h-4 w-4" />
                                        Create your first campaign
                                    </Button>
                                </>
                            )}
                        </div>
                    )}

                    {/* Pagination Bar */}
                    {!isLoading && !error && totalFiltered > 0 && (
                        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-6 py-4 border-t bg-muted/5 text-xs text-muted-foreground">
                            <div>
                                Showing <span className="font-semibold text-foreground">{(currentPage - 1) * pageSize + 1}</span> to{' '}
                                <span className="font-semibold text-foreground">{Math.min(currentPage * pageSize, totalFiltered)}</span> of{' '}
                                <span className="font-semibold text-foreground">{totalFiltered}</span> campaigns
                            </div>

                            <div className="flex items-center gap-4">
                                {/* Page size selector */}
                                <div className="flex items-center gap-1.5">
                                    <span>Rows per page:</span>
                                    <Select value={String(pageSize)} onValueChange={(v) => setPageSize(parseInt(v, 10))}>
                                        <SelectTrigger className="w-[70px] h-7 text-xs">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="10">10</SelectItem>
                                            <SelectItem value="25">25</SelectItem>
                                            <SelectItem value="50">50</SelectItem>
                                            <SelectItem value="100">100</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                {/* Page Navigation */}
                                <div className="flex items-center gap-1">
                                    <Button
                                        variant="outline"
                                        size="icon"
                                        className="h-7 w-7"
                                        disabled={currentPage <= 1}
                                        onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                                        title="Previous page"
                                    >
                                        <ChevronLeft className="h-3.5 w-3.5" />
                                    </Button>
                                    <span className="px-2 font-medium text-foreground">
                                        Page {currentPage} of {totalPages}
                                    </span>
                                    <Button
                                        variant="outline"
                                        size="icon"
                                        className="h-7 w-7"
                                        disabled={currentPage >= totalPages}
                                        onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                                        title="Next page"
                                    >
                                        <ChevronRight className="h-3.5 w-3.5" />
                                    </Button>
                                </div>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
