'use client';

import {
    Archive,
    ArrowRight,
    Bot,
    ChevronLeft,
    ChevronRight,
    Lock,
    Pencil,
    RotateCcw,
    Search,
    Shield,
    X,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useMemo, useState, useTransition } from 'react';
import { toast } from 'sonner';

import { updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut } from '@/client/sdk.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";

interface Workflow {
    id: number;
    name: string;
    status: string;
    created_at: string;
    total_runs?: number | null;
}

interface WorkflowTableProps {
    workflows: Workflow[];
    showArchived?: boolean;
}

type TabType = 'active' | 'archived' | 'all';

export function WorkflowTable({ workflows, showArchived = false }: WorkflowTableProps) {
    const router = useRouter();
    const [isPending, startTransition] = useTransition();
    const [loadingWorkflowId, setLoadingWorkflowId] = useState<number | null>(null);
    const [bulkLoading, setBulkLoading] = useState(false);

    // Filters & Pagination state
    const [currentTab, setCurrentTab] = useState<TabType>(showArchived ? 'archived' : 'active');
    const [searchQuery, setSearchQuery] = useState('');
    const [currentPage, setCurrentPage] = useState(1);
    const [pageSize, setPageSize] = useState(10);
    const [selectedIds, setSelectedIds] = useState<number[]>([]);

    const handleEdit = (id: number) => {
        router.push(`/workflow/${id}`);
    };

    // Calculate tab counts
    const activeCount = useMemo(() => workflows.filter((w) => w.status === 'active').length, [workflows]);
    const archivedCount = useMemo(() => workflows.filter((w) => w.status === 'archived').length, [workflows]);
    const totalCount = workflows.length;

    // Filter workflows by tab and search query
    const filteredWorkflows = useMemo(() => {
        return workflows
            .filter((w) => {
                // Tab filter
                if (currentTab === 'active' && w.status !== 'active') return false;
                if (currentTab === 'archived' && w.status !== 'archived') return false;

                // Search filter (name or #id)
                if (searchQuery.trim()) {
                    const q = searchQuery.trim().toLowerCase();
                    const idMatch = String(w.id) === q.replace(/^#/, '');
                    const nameMatch = w.name.toLowerCase().includes(q);
                    return idMatch || nameMatch;
                }
                return true;
            })
            .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
    }, [workflows, currentTab, searchQuery]);

    // Pagination calculations
    const totalPages = Math.max(1, Math.ceil(filteredWorkflows.length / pageSize));
    const paginatedWorkflows = useMemo(() => {
        const start = (currentPage - 1) * pageSize;
        return filteredWorkflows.slice(start, start + pageSize);
    }, [filteredWorkflows, currentPage, pageSize]);

    // Bulk selection helpers
    const selectableWorkflowsOnPage = useMemo(() => {
        // Protect Workflow #1 from archiving
        return paginatedWorkflows.filter((w) => !(w.id === 1 && w.status === 'active'));
    }, [paginatedWorkflows]);

    const isAllPageSelected =
        selectableWorkflowsOnPage.length > 0 &&
        selectableWorkflowsOnPage.every((w) => selectedIds.includes(w.id));

    const handleSelectAll = (checked: boolean) => {
        if (checked) {
            const newIds = Array.from(new Set([...selectedIds, ...selectableWorkflowsOnPage.map((w) => w.id)]));
            setSelectedIds(newIds);
        } else {
            const pageIds = new Set(selectableWorkflowsOnPage.map((w) => w.id));
            setSelectedIds(selectedIds.filter((id) => !pageIds.has(id)));
        }
    };

    const handleSelectOne = (id: number, checked: boolean) => {
        if (checked) {
            setSelectedIds((prev) => [...prev, id]);
        } else {
            setSelectedIds((prev) => prev.filter((item) => item !== id));
        }
    };

    const handleArchiveToggle = async (id: number, currentStatus: string) => {
        if (id === 1 && currentStatus === 'active') {
            toast.error("Workflow #1 ('Default Voice Assistant') is baseline protected and cannot be archived.");
            return;
        }

        const newStatus = currentStatus === 'active' ? 'archived' : 'active';
        const action = currentStatus === 'active' ? 'Archive' : 'Restore';

        setLoadingWorkflowId(id);

        try {
            const response = await updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut({
                path: { workflow_id: id },
                body: { status: newStatus },
            });

            if (response.data) {
                toast.success(`Workflow ${action.toLowerCase()}d successfully`);
                setSelectedIds((prev) => prev.filter((item) => item !== id));
                startTransition(() => {
                    router.refresh();
                });
            }
        } catch (error) {
            console.error(`Error ${action.toLowerCase()}ing workflow:`, error);
            toast.error(`Failed to ${action.toLowerCase()} workflow`);
        } finally {
            setLoadingWorkflowId(null);
        }
    };

    const handleBulkAction = async (targetStatus: 'active' | 'archived') => {
        const actionLabel = targetStatus === 'active' ? 'Restore' : 'Archive';
        const toProcess = selectedIds.filter((id) => !(id === 1 && targetStatus === 'archived'));

        if (toProcess.length === 0) {
            toast.info("No eligible workflows selected.");
            return;
        }

        setBulkLoading(true);
        let successCount = 0;
        let failCount = 0;

        for (const id of toProcess) {
            try {
                const res = await updateWorkflowStatusApiV1WorkflowWorkflowIdStatusPut({
                    path: { workflow_id: id },
                    body: { status: targetStatus },
                });
                if (res.data) successCount++;
                else failCount++;
            } catch {
                failCount++;
            }
        }

        setBulkLoading(false);
        setSelectedIds([]);
        if (successCount > 0) {
            toast.success(`Successfully ${actionLabel.toLowerCase()}d ${successCount} workflow(s)`);
            startTransition(() => {
                router.refresh();
            });
        }
        if (failCount > 0) {
            toast.error(`Failed to ${actionLabel.toLowerCase()} ${failCount} workflow(s)`);
        }
    };

    return (
        <div className="space-y-4">
            {/* Top Toolbar: Tabs, Search, Rows Per Page */}
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-card p-3 rounded-xl border shadow-sm">
                {/* Tabs */}
                <div className="flex items-center gap-1 bg-muted/60 p-1 rounded-lg">
                    <button
                        type="button"
                        onClick={() => {
                            setCurrentTab('active');
                            setCurrentPage(1);
                        }}
                        className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-all ${
                            currentTab === 'active'
                                ? 'bg-background text-foreground shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        Active ({activeCount})
                    </button>
                    <button
                        type="button"
                        onClick={() => {
                            setCurrentTab('archived');
                            setCurrentPage(1);
                        }}
                        className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-all ${
                            currentTab === 'archived'
                                ? 'bg-background text-foreground shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        Archived ({archivedCount})
                    </button>
                    <button
                        type="button"
                        onClick={() => {
                            setCurrentTab('all');
                            setCurrentPage(1);
                        }}
                        className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-all ${
                            currentTab === 'all'
                                ? 'bg-background text-foreground shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                        }`}
                    >
                        All ({totalCount})
                    </button>
                </div>

                {/* Search & Page Size */}
                <div className="flex items-center gap-2 flex-1 sm:max-w-md justify-end">
                    <div className="relative flex-1">
                        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                        <Input
                            placeholder="Search agents by name or #ID..."
                            value={searchQuery}
                            onChange={(e) => {
                                setSearchQuery(e.target.value);
                                setCurrentPage(1);
                            }}
                            className="pl-8 pr-8 h-9 text-xs"
                        />
                        {searchQuery && (
                            <button
                                type="button"
                                onClick={() => {
                                    setSearchQuery('');
                                    setCurrentPage(1);
                                }}
                                className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground"
                            >
                                <X className="h-4 w-4" />
                            </button>
                        )}
                    </div>

                    <select
                        aria-label="Rows per page"
                        value={pageSize}
                        onChange={(e) => {
                            setPageSize(Number(e.target.value));
                            setCurrentPage(1);
                        }}
                        className="h-9 px-2 text-xs border rounded-md bg-background text-foreground focus:ring-1 focus:ring-primary"
                    >
                        <option value={10}>10 / page</option>
                        <option value={25}>25 / page</option>
                        <option value={50}>50 / page</option>
                    </select>
                </div>
            </div>

            {/* Bulk Actions Banner */}
            {selectedIds.length > 0 && (
                <div className="flex items-center justify-between bg-primary/10 border border-primary/20 p-3 rounded-xl animate-fade-in">
                    <div className="flex items-center gap-2">
                        <Badge variant="default" className="font-mono">
                            {selectedIds.length}
                        </Badge>
                        <span className="text-xs font-medium text-foreground">
                            agent{selectedIds.length > 1 ? 's' : ''} selected
                        </span>
                    </div>
                    <div className="flex items-center gap-2">
                        {currentTab !== 'archived' && (
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={bulkLoading || isPending}
                                onClick={() => handleBulkAction('archived')}
                                className="h-8 text-xs gap-1.5 text-destructive hover:bg-destructive/10"
                            >
                                <Archive className="h-3.5 w-3.5" />
                                {bulkLoading ? 'Archiving...' : 'Archive Selected'}
                            </Button>
                        )}
                        {currentTab !== 'active' && (
                            <Button
                                size="sm"
                                variant="outline"
                                disabled={bulkLoading || isPending}
                                onClick={() => handleBulkAction('active')}
                                className="h-8 text-xs gap-1.5 text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-950/20"
                            >
                                <RotateCcw className="h-3.5 w-3.5" />
                                {bulkLoading ? 'Restoring...' : 'Restore Selected'}
                            </Button>
                        )}
                        <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setSelectedIds([])}
                            className="h-8 text-xs"
                        >
                            Deselect
                        </Button>
                    </div>
                </div>
            )}

            {/* Table */}
            <div className="bg-card border rounded-xl overflow-hidden shadow-sm">
                <Table>
                    <TableHeader>
                        <TableRow className="hover:bg-transparent border-b">
                            <TableHead className="w-10">
                                <Checkbox
                                    checked={isAllPageSelected}
                                    onCheckedChange={(checked) => handleSelectAll(Boolean(checked))}
                                    aria-label="Select all on page"
                                />
                            </TableHead>
                            <TableHead className="w-20 font-semibold text-muted-foreground text-xs uppercase tracking-wider">
                                ID
                            </TableHead>
                            <TableHead className="font-semibold text-muted-foreground text-xs uppercase tracking-wider">
                                Agent Name
                            </TableHead>
                            <TableHead className="font-semibold text-muted-foreground text-xs uppercase tracking-wider">
                                Status
                            </TableHead>
                            <TableHead className="font-semibold text-muted-foreground text-xs uppercase tracking-wider">
                                Created At
                            </TableHead>
                            <TableHead className="font-semibold text-muted-foreground text-xs uppercase tracking-wider text-center">
                                Total Runs
                            </TableHead>
                            <TableHead className="font-semibold text-muted-foreground text-xs uppercase tracking-wider text-right">
                                Actions
                            </TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {paginatedWorkflows.length === 0 ? (
                            <TableRow>
                                <TableCell colSpan={7} className="text-center py-12 text-muted-foreground">
                                    {searchQuery ? (
                                        <div className="space-y-2">
                                            <p className="font-medium text-foreground">No agents matching &quot;{searchQuery}&quot;</p>
                                            <p className="text-xs">Try adjusting your search query or tab filter.</p>
                                        </div>
                                    ) : (
                                        <div className="space-y-2">
                                            <p className="font-medium text-foreground">No {currentTab} voice agents found.</p>
                                            <p className="text-xs">Create your first agent to get started.</p>
                                        </div>
                                    )}
                                </TableCell>
                            </TableRow>
                        ) : (
                            paginatedWorkflows.map((workflow, index) => {
                                const isArchived = workflow.status === 'archived';
                                const isBaseline = workflow.id === 1;
                                const isSelected = selectedIds.includes(workflow.id);

                                return (
                                    <TableRow
                                        key={workflow.id}
                                        className={`group border-b-0 last:border-0 hover:bg-accent/30 transition-all duration-200 ${
                                            isArchived ? 'opacity-60 bg-muted/20' : ''
                                        } ${isSelected ? 'bg-primary/5' : ''}`}
                                        style={{ animationDelay: `${index * 40}ms` }}
                                    >
                                        <TableCell>
                                            <Checkbox
                                                checked={isSelected}
                                                disabled={isBaseline && !isArchived}
                                                onCheckedChange={(checked) => handleSelectOne(workflow.id, Boolean(checked))}
                                                aria-label={`Select workflow ${workflow.id}`}
                                            />
                                        </TableCell>
                                        <TableCell className="text-muted-foreground font-mono text-sm">
                                            #{workflow.id}
                                        </TableCell>
                                        <TableCell className="font-medium">
                                            <div className="flex items-center gap-3">
                                                <div className="bg-gradient-to-br from-blue-500 to-blue-600 p-2 rounded-lg shadow-sm group-hover:scale-105 transition-transform">
                                                    <Bot className="h-4 w-4 text-white" />
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <span className="group-hover:text-primary transition-colors font-medium">
                                                        {workflow.name}
                                                    </span>
                                                    {isBaseline && (
                                                        <Badge
                                                            variant="secondary"
                                                            className="text-[10px] gap-1 px-1.5 py-0 border-blue-500/30 text-blue-600 dark:text-blue-400 font-semibold"
                                                            title="Baseline Protected Agent"
                                                        >
                                                            <Shield className="h-3 w-3" />
                                                            Protected
                                                        </Badge>
                                                    )}
                                                </div>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            {isArchived ? (
                                                <Badge variant="secondary" className="text-xs">
                                                    Archived
                                                </Badge>
                                            ) : (
                                                <Badge variant="outline" className="text-xs border-emerald-500/30 text-emerald-600 dark:text-emerald-400">
                                                    Active
                                                </Badge>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">
                                            {new Date(workflow.created_at).toLocaleDateString('en-US', {
                                                year: 'numeric',
                                                month: 'short',
                                                day: 'numeric',
                                            })}
                                        </TableCell>
                                        <TableCell className="text-center">
                                            <span className="inline-flex items-center justify-center min-w-[2.5rem] px-3 py-1.5 text-sm font-bold bg-gradient-to-r from-blue-500/10 to-cyan-500/10 text-foreground rounded-full border border-blue-500/20">
                                                {workflow.total_runs || 0}
                                            </span>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => handleEdit(workflow.id)}
                                                    className="gap-2 hover:gap-3 transition-all group/btn"
                                                >
                                                    <Pencil size={15} />
                                                    <span>Edit</span>
                                                    <ArrowRight size={15} className="opacity-0 group-hover/btn:opacity-100 -ml-1 transition-all" />
                                                </Button>
                                                {isBaseline && !isArchived ? (
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        disabled
                                                        className="gap-1 text-muted-foreground opacity-50 cursor-not-allowed"
                                                        title="Protected from archiving"
                                                    >
                                                        <Lock size={14} />
                                                        Protected
                                                    </Button>
                                                ) : (
                                                    <Button
                                                        variant={isArchived ? "default" : "outline"}
                                                        size="sm"
                                                        onClick={() => handleArchiveToggle(workflow.id, workflow.status)}
                                                        disabled={loadingWorkflowId === workflow.id || isPending}
                                                        className="gap-2 transition-all"
                                                    >
                                                        {loadingWorkflowId === workflow.id ? (
                                                            <>
                                                                <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
                                                                {isArchived ? 'Restoring...' : 'Archiving...'}
                                                            </>
                                                        ) : (
                                                            <>
                                                                {isArchived ? (
                                                                    <>
                                                                        <RotateCcw size={15} />
                                                                        Restore
                                                                    </>
                                                                ) : (
                                                                    <>
                                                                        <Archive size={15} />
                                                                        Archive
                                                                    </>
                                                                )}
                                                            </>
                                                        )}
                                                    </Button>
                                                )}
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                );
                            })
                        )}
                    </TableBody>
                </Table>
            </div>

            {/* Pagination Controls */}
            {filteredWorkflows.length > 0 && (
                <div className="flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-muted-foreground px-1">
                    <div>
                        Showing{' '}
                        <span className="font-semibold text-foreground">
                            {Math.min(filteredWorkflows.length, (currentPage - 1) * pageSize + 1)}
                        </span>{' '}
                        to{' '}
                        <span className="font-semibold text-foreground">
                            {Math.min(filteredWorkflows.length, currentPage * pageSize)}
                        </span>{' '}
                        of{' '}
                        <span className="font-semibold text-foreground">{filteredWorkflows.length}</span>{' '}
                        agents
                    </div>

                    <div className="flex items-center gap-1.5">
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={currentPage <= 1}
                            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                            className="h-8 px-2.5 text-xs gap-1"
                        >
                            <ChevronLeft className="h-3.5 w-3.5" />
                            Previous
                        </Button>
                        <div className="px-3 py-1 bg-muted rounded-md font-mono text-xs">
                            {currentPage} / {totalPages}
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            disabled={currentPage >= totalPages}
                            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                            className="h-8 px-2.5 text-xs gap-1"
                        >
                            Next
                            <ChevronRight className="h-3.5 w-3.5" />
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}
