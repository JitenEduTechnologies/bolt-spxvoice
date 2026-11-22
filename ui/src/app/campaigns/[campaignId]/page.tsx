"use client";

import { format } from 'date-fns';
import {
    Activity,
    AlertCircle,
    AlertTriangle,
    Archive,
    ArchiveRestore,
    ArrowLeft,
    CalendarIcon,
    Check,
    ChevronDown,
    ChevronUp,
    Clock,
    DollarSign,
    Download,
    ExternalLink,
    Flame,
    Info,
    Pause,
    Pencil,
    Phone,
    PhoneCall,
    PhoneOff,
    Play,
    RefreshCw,
    ShieldAlert,
    ShieldCheck,
    Sparkles,
    Target,
    Users,
    X,
} from 'lucide-react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';

import {
    downloadCampaignReportApiV1CampaignCampaignIdReportGet,
    getCampaignApiV1CampaignCampaignIdGet,
    getCampaignSourceDownloadUrlApiV1CampaignCampaignIdSourceDownloadUrlGet,
    pauseCampaignApiV1CampaignCampaignIdPausePost,
    redialCampaignApiV1CampaignCampaignIdRedialPost,
    resumeCampaignApiV1CampaignCampaignIdResumePost,
    startCampaignApiV1CampaignCampaignIdStartPost,
} from '@/client/sdk.gen';
import type {
    CampaignOwnerSummaryResponse,
    CampaignResponse,
    RedialPreviewResponse,
} from '@/client/types.gen';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Calendar } from '@/components/ui/calendar';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Separator } from '@/components/ui/separator';
import { CampaignRuns } from '@/components/workflow-runs';
import { useAuth } from '@/lib/auth';

export default function CampaignDetailPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();
    const params = useParams();
    const searchParams = useSearchParams();
    const campaignId = parseInt(params.campaignId as string);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Campaign state
    const [campaign, setCampaign] = useState<CampaignResponse | null>(null);
    const [ownerSummary, setOwnerSummary] = useState<CampaignOwnerSummaryResponse | null>(null);
    const [isLoadingCampaign, setIsLoadingCampaign] = useState(true);
    const [isLoadingSummary, setIsLoadingSummary] = useState(false);

    // Action state
    const [isExecutingAction, setIsExecutingAction] = useState(false);
    const [isDownloadingReport, setIsDownloadingReport] = useState(false);
    const [isArchiving, setIsArchiving] = useState(false);

    // Report date range state
    const [reportStartDate, setReportStartDate] = useState<Date | undefined>(undefined);
    const [reportStartTime, setReportStartTime] = useState('00:00');
    const [reportEndDate, setReportEndDate] = useState<Date | undefined>(undefined);
    const [reportEndTime, setReportEndTime] = useState('23:59');
    const [isReportPopoverOpen, setIsReportPopoverOpen] = useState(false);

    // Redial dialog state
    const [isRedialDialogOpen, setIsRedialDialogOpen] = useState(false);
    const [redialName, setRedialName] = useState('');
    const [redialOnVoicemail, setRedialOnVoicemail] = useState(true);
    const [redialOnNoAnswer, setRedialOnNoAnswer] = useState(true);
    const [redialOnBusy, setRedialOnBusy] = useState(true);
    const [isRedialing, setIsRedialing] = useState(false);
    const [redialPreview, setRedialPreview] = useState<RedialPreviewResponse | null>(null);
    const [isLoadingPreview, setIsLoadingPreview] = useState(false);

    // Cancel dialog state
    const [isCancelDialogOpen, setIsCancelDialogOpen] = useState(false);
    const [isCancelling, setIsCancelling] = useState(false);

    // Collapsible technical drilldown state
    const [isRunsCollapsed, setIsRunsCollapsed] = useState(true);

    // Activity Log pagination & filter state
    const [logPage, setLogPage] = useState(1);
    const [logsPerPage, setLogsPerPage] = useState(10);
    const [logEventFilter, setLogEventFilter] = useState('all');

    // Fetch campaign details
    const fetchCampaign = useCallback(async () => {
        if (!user) return;
        setIsLoadingCampaign(true);
        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignApiV1CampaignCampaignIdGet({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setCampaign(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch campaign:', error);
            toast.error('Failed to load campaign details');
        } finally {
            setIsLoadingCampaign(false);
        }
    }, [user, getAccessToken, campaignId]);

    // Fetch owner business summary (KPIs)
    const fetchOwnerSummary = useCallback(async () => {
        if (!user) return;
        setIsLoadingSummary(true);
        try {
            const accessToken = await getAccessToken();
            const res = await fetch(`/api/v1/campaign/${campaignId}/owner-summary`, {
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });
            if (res.ok) {
                const data = await res.json();
                setOwnerSummary(data);
            }
        } catch (error) {
            console.error('Failed to fetch owner summary:', error);
        } finally {
            setIsLoadingSummary(false);
        }
    }, [user, getAccessToken, campaignId]);

    // Fetch redial preview
    const fetchRedialPreview = useCallback(async (voicemail: boolean, noAnswer: boolean, busy: boolean) => {
        if (!user) return;
        setIsLoadingPreview(true);
        try {
            const accessToken = await getAccessToken();
            const q = new URLSearchParams({
                retry_on_voicemail: String(voicemail),
                retry_on_no_answer: String(noAnswer),
                retry_on_busy: String(busy),
            });
            const res = await fetch(`/api/v1/campaign/${campaignId}/redial-preview?${q.toString()}`, {
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });
            if (res.ok) {
                const data = await res.json();
                setRedialPreview(data);
            }
        } catch (error) {
            console.error('Failed to load redial preview:', error);
        } finally {
            setIsLoadingPreview(false);
        }
    }, [user, getAccessToken, campaignId]);

    // Initial load
    useEffect(() => {
        fetchCampaign();
        fetchOwnerSummary();
    }, [fetchCampaign, fetchOwnerSummary]);

    // Handle back navigation
    const handleBack = () => {
        router.push('/campaigns');
    };

    // Handle workflow link click
    const handleWorkflowClick = () => {
        if (campaign) {
            router.push(`/workflow/${campaign.workflow_id}`);
        }
    };

    // Handle CSV download
    const handleDownloadCsv = async () => {
        if (!user || !campaign || campaign.source_type !== 'csv') return;

        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignSourceDownloadUrlApiV1CampaignCampaignIdSourceDownloadUrlGet({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data?.download_url) {
                window.open(response.data.download_url, '_blank');
            } else {
                toast.error('Failed to get download URL');
            }
        } catch (error) {
            console.error('Failed to download CSV:', error);
            toast.error('Failed to download CSV file');
        }
    };

    // Build ISO datetime string from date + time
    const buildDateTime = (date: Date | undefined, time: string): string | undefined => {
        if (!date) return undefined;
        const [hours, minutes] = time.split(':').map(Number);
        const combined = new Date(date);
        combined.setHours(hours, minutes, 0, 0);
        return combined.toISOString();
    };

    // Handle download report
    const handleDownloadReport = async () => {
        if (!user) return;
        setIsDownloadingReport(true);
        setIsReportPopoverOpen(false);
        try {
            const accessToken = await getAccessToken();
            const startDate = buildDateTime(reportStartDate, reportStartTime);
            const endDate = buildDateTime(reportEndDate, reportEndTime);

            const response = await downloadCampaignReportApiV1CampaignCampaignIdReportGet({
                path: {
                    campaign_id: campaignId,
                },
                query: {
                    start_date: startDate,
                    end_date: endDate,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
                parseAs: 'blob',
            });

            if (response.data) {
                const blob = response.data as Blob;
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `campaign_${campaignId}_report.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
            } else {
                toast.error('Failed to download report');
            }
        } catch (error) {
            console.error('Failed to download report:', error);
            toast.error('Failed to download report');
        } finally {
            setIsDownloadingReport(false);
        }
    };

    const handleClearDateRange = () => {
        setReportStartDate(undefined);
        setReportStartTime('00:00');
        setReportEndDate(undefined);
        setReportEndTime('23:59');
    };

    // Handle start campaign
    const handleStart = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await startCampaignApiV1CampaignCampaignIdStartPost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign started');
                fetchOwnerSummary();
            } else if (response.error) {
                let errorMsg = 'Failed to start campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to start campaign:', error);
            toast.error('Failed to start campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Handle cancel campaign
    const handleCancelCampaign = async () => {
        if (!user || !campaign) return;
        setIsCancelling(true);
        try {
            const accessToken = await getAccessToken();
            const res = await fetch(`/api/v1/campaign/${campaignId}/cancel`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                    'Content-Type': 'application/json',
                },
            });
            if (res.ok) {
                const data = await res.json();
                setCampaign(data);
                toast.success('Campaign cancelled');
                setIsCancelDialogOpen(false);
                fetchOwnerSummary();
            } else {
                const err = await res.json().catch(() => ({}));
                toast.error(err.detail || 'Failed to cancel campaign');
            }
        } catch (error) {
            console.error('Failed to cancel campaign:', error);
            toast.error('Failed to cancel campaign');
        } finally {
            setIsCancelling(false);
        }
    };

    // Handle resume campaign
    const handleResume = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await resumeCampaignApiV1CampaignCampaignIdResumePost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign resumed');
                fetchOwnerSummary();
            } else if (response.error) {
                let errorMsg = 'Failed to resume campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to resume campaign:', error);
            toast.error('Failed to resume campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Handle pause campaign
    const handlePause = async () => {
        if (!user) return;
        setIsExecutingAction(true);
        try {
            const accessToken = await getAccessToken();
            const response = await pauseCampaignApiV1CampaignCampaignIdPausePost({
                path: {
                    campaign_id: campaignId,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                setCampaign(response.data);
                toast.success('Campaign paused');
                fetchOwnerSummary();
            }
        } catch (error) {
            console.error('Failed to pause campaign:', error);
            toast.error('Failed to pause campaign');
        } finally {
            setIsExecutingAction(false);
        }
    };

    // Handle Archive / Unarchive
    const handleArchive = async () => {
        if (!user || !campaign) return;
        setIsArchiving(true);
        try {
            const accessToken = await getAccessToken();
            const res = await fetch(`/api/v1/campaign/${campaignId}/archive`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });
            if (res.ok) {
                const data = await res.json();
                setCampaign(data);
                toast.success('Campaign archived successfully');
                fetchOwnerSummary();
            } else {
                const err = await res.json().catch(() => ({}));
                toast.error(err.detail || 'Failed to archive campaign');
            }
        } catch (error) {
            console.error('Failed to archive campaign:', error);
            toast.error('Failed to archive campaign');
        } finally {
            setIsArchiving(false);
        }
    };

    const handleUnarchive = async () => {
        if (!user || !campaign) return;
        setIsArchiving(true);
        try {
            const accessToken = await getAccessToken();
            const res = await fetch(`/api/v1/campaign/${campaignId}/unarchive`, {
                method: 'POST',
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });
            if (res.ok) {
                const data = await res.json();
                setCampaign(data);
                toast.success('Campaign unarchived successfully');
                fetchOwnerSummary();
            } else {
                const err = await res.json().catch(() => ({}));
                toast.error(err.detail || 'Failed to unarchive campaign');
            }
        } catch (error) {
            console.error('Failed to unarchive campaign:', error);
            toast.error('Failed to unarchive campaign');
        } finally {
            setIsArchiving(false);
        }
    };

    // Open redial dialog with default name and fetch preview
    const openRedialDialog = () => {
        if (!campaign) return;
        setRedialName(`${campaign.name} (Follow-up)`);
        setRedialOnVoicemail(true);
        setRedialOnNoAnswer(true);
        setRedialOnBusy(true);
        setIsRedialDialogOpen(true);
        fetchRedialPreview(true, true, true);
    };

    // Handle redial campaign
    const handleRedial = async () => {
        if (!user || !campaign) return;
        if (!redialOnVoicemail && !redialOnNoAnswer && !redialOnBusy) {
            toast.error('Select at least one reason to redial');
            return;
        }
        setIsRedialing(true);
        try {
            const accessToken = await getAccessToken();
            const response = await redialCampaignApiV1CampaignCampaignIdRedialPost({
                path: {
                    campaign_id: campaignId,
                },
                body: {
                    name: redialName || null,
                    retry_on_voicemail: redialOnVoicemail,
                    retry_on_no_answer: redialOnNoAnswer,
                    retry_on_busy: redialOnBusy,
                },
                headers: {
                    Authorization: `Bearer ${accessToken}`,
                },
            });

            if (response.data) {
                toast.success('Follow-up campaign draft created');
                setIsRedialDialogOpen(false);
                router.push(`/campaigns/${response.data.id}`);
            } else if (response.error) {
                let errorMsg = 'Failed to create redial campaign';
                if (typeof response.error === 'string') {
                    errorMsg = response.error;
                } else if (response.error && typeof response.error === 'object') {
                    errorMsg = (response.error as unknown as { detail?: string }).detail || JSON.stringify(response.error);
                }
                toast.error(errorMsg);
            }
        } catch (error) {
            console.error('Failed to redial campaign:', error);
            toast.error('Failed to create redial campaign');
        } finally {
            setIsRedialing(false);
        }
    };

    // Format date for display
    const formatDate = (dateString: string) => {
        return new Date(dateString).toLocaleDateString();
    };

    const formatDateTime = (dateString: string) => {
        return new Date(dateString).toLocaleString();
    };

    // Get badge variant for state
    const getStateBadgeVariant = (state: string) => {
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

    const canEdit = campaign && ['created', 'running', 'paused'].includes(campaign.state);

    const sortedLogs = (campaign?.logs ?? []).slice().reverse();

    const getLogIcon = (level: string) => {
        switch (level) {
            case 'error':
                return <AlertCircle className="h-4 w-4 text-destructive" />;
            case 'warning':
                return <AlertTriangle className="h-4 w-4 text-amber-500" />;
            default:
                return <Info className="h-4 w-4 text-blue-500" />;
        }
    };

    const getLogBadgeVariant = (level: string): 'destructive' | 'secondary' | 'outline' => {
        switch (level) {
            case 'error':
                return 'destructive';
            case 'warning':
                return 'outline';
            default:
                return 'secondary';
        }
    };

    const formatLogTimestamp = (ts: string) => {
        const d = new Date(ts);
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleString();
    };

    const formatEventLabel = (event: string) => {
        switch (event) {
            case 'campaign_unarchived':
                return 'Campaign unarchived';
            case 'campaign_archived':
                return 'Campaign archived';
            case 'campaign_started':
                return 'Campaign started';
            case 'campaign_paused':
                return 'Campaign paused';
            case 'campaign_resumed':
                return 'Campaign resumed';
            case 'campaign_cancelled':
                return 'Campaign cancelled';
            default:
                return event.replace(/_/g, ' ');
        }
    };

    const availableEventTypes = Array.from(new Set(sortedLogs.map(l => l.event)));
    const filteredLogs = sortedLogs.filter(entry => {
        if (logEventFilter === 'all') return true;
        return entry.event === logEventFilter;
    });
    const totalLogPages = Math.max(1, Math.ceil(filteredLogs.length / logsPerPage));
    const paginatedLogs = filteredLogs.slice((logPage - 1) * logsPerPage, logPage * logsPerPage);

    // Render action button based on state
    const renderActionButton = () => {
        if (!campaign || isExecutingAction) return null;

        const editButton = canEdit ? (
            <Button variant="outline" onClick={() => router.push(`/campaigns/${campaignId}/edit`)}>
                <Pencil className="h-4 w-4 mr-2" />
                Edit
            </Button>
        ) : null;

        const archiveButton = campaign.is_archived ? (
            <Button
                variant="outline"
                size="sm"
                onClick={handleUnarchive}
                disabled={isArchiving}
                className="text-amber-700 border-amber-300 hover:bg-amber-50"
            >
                <ArchiveRestore className="h-4 w-4 mr-1.5" />
                Unarchive
            </Button>
        ) : campaign.state !== 'running' && campaign.state !== 'paused' ? (
            <Button
                variant="outline"
                size="sm"
                onClick={handleArchive}
                disabled={isArchiving}
                className="text-muted-foreground hover:text-foreground"
            >
                <Archive className="h-4 w-4 mr-1.5" />
                Archive
            </Button>
        ) : null;

        switch (campaign.state) {
            case 'created':
                const isTelephonyMissing = !campaign.telephony_configuration_id;
                const isEmergencyStopActive = !!ownerSummary?.emergency_stop_active;
                const isStartDisabled = isExecutingAction || isTelephonyMissing || isEmergencyStopActive;

                let disabledReason = '';
                if (isEmergencyStopActive) {
                    disabledReason = 'Emergency Stop is active. All outbound dialing is halted.';
                } else if (isTelephonyMissing) {
                    disabledReason = 'Telephony configuration not assigned. Edit campaign to assign a telephony provider & caller ID.';
                }

                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        {archiveButton}
                        <div title={disabledReason || undefined}>
                            <Button
                                onClick={handleStart}
                                disabled={isStartDisabled}
                                className={isStartDisabled ? 'opacity-60 cursor-not-allowed' : ''}
                            >
                                <Play className="h-4 w-4 mr-2" />
                                Start Campaign
                            </Button>
                        </div>
                    </div>
                );
            case 'running':
                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        <Button onClick={handlePause} disabled={isExecutingAction}>
                            <Pause className="h-4 w-4 mr-2" />
                            Pause Campaign
                        </Button>
                        <Button variant="destructive" onClick={() => setIsCancelDialogOpen(true)} disabled={isExecutingAction}>
                            <X className="h-4 w-4 mr-2" />
                            Cancel
                        </Button>
                    </div>
                );
            case 'paused':
                return (
                    <div className="flex items-center gap-2">
                        {editButton}
                        <Button onClick={handleResume} disabled={isExecutingAction}>
                            <RefreshCw className="h-4 w-4 mr-2" />
                            Resume
                        </Button>
                        <Button variant="destructive" onClick={() => setIsCancelDialogOpen(true)} disabled={isExecutingAction}>
                            <X className="h-4 w-4 mr-2" />
                            Cancel
                        </Button>
                    </div>
                );
            case 'cancelled':
                return (
                    <div className="flex items-center gap-2">
                        {archiveButton}
                        <Button variant="outline" onClick={openRedialDialog}>
                            <RefreshCw className="h-4 w-4 mr-2" />
                            Retry Unreachable Contacts
                        </Button>
                        <Badge variant="destructive" className="px-3 py-1.5 text-sm font-semibold">
                            Cancelled
                        </Badge>
                    </div>
                );
            case 'completed':
                return (
                    <div className="flex items-center gap-2">
                        {archiveButton}
                        {!campaign.redialed_campaign_id && (
                            <Button onClick={openRedialDialog} className="bg-indigo-600 hover:bg-indigo-700 text-white">
                                <Phone className="h-4 w-4 mr-2" />
                                Retry Eligible Contacts
                            </Button>
                        )}
                    </div>
                );
            default:
                return archiveButton;
        }
    };

    if (isLoadingCampaign) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <div className="animate-pulse space-y-4">
                    <div className="h-8 bg-muted rounded w-1/4"></div>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="h-28 bg-muted rounded"></div>
                        <div className="h-28 bg-muted rounded"></div>
                        <div className="h-28 bg-muted rounded"></div>
                        <div className="h-28 bg-muted rounded"></div>
                    </div>
                    <div className="h-64 bg-muted rounded"></div>
                </div>
            </div>
        );
    }

    if (!campaign) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <p className="text-center text-muted-foreground">Campaign not found</p>
            </div>
        );
    }

    const totalContacts = ownerSummary?.total_contacts ?? campaign.total_queued_count ?? 0;
    const attemptedContacts = ownerSummary?.attempted_contacts ?? campaign.executed_count ?? 0;
    const completionPct = ownerSummary?.completion_percentage ?? (totalContacts > 0 ? Math.round((attemptedContacts / totalContacts) * 100) : 0);

    return (
        <div className="container mx-auto p-6 space-y-6">
            <div>
                <Button
                    variant="ghost"
                    onClick={handleBack}
                    className="mb-4"
                >
                    <ArrowLeft className="h-4 w-4 mr-2" />
                    Back to Campaigns
                </Button>

                <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                    <div>
                        <div className="flex items-center gap-3 mb-1">
                            <h1 className="text-3xl font-bold tracking-tight">{campaign.name}</h1>
                            <Badge variant={getStateBadgeVariant(campaign.state)} className="capitalize font-semibold text-sm">
                                {campaign.state}
                            </Badge>
                            {campaign.is_archived && (
                                <Badge variant="outline" className="border-amber-400 text-amber-700 bg-amber-50">
                                    <Archive className="h-3 w-3 mr-1" />
                                    Archived
                                </Badge>
                            )}
                        </div>
                        <div className="flex items-center gap-3 text-sm text-muted-foreground">
                            <span>ID: #{campaign.id}</span>
                            <span>•</span>
                            <span>Created {formatDate(campaign.created_at)}</span>
                            <span>•</span>
                            <span>Workflow: <button onClick={handleWorkflowClick} className="text-blue-600 hover:underline">{campaign.workflow_name}</button></span>
                        </div>
                    </div>

                    <div className="flex items-center flex-wrap gap-2">
                        <Popover open={isReportPopoverOpen} onOpenChange={setIsReportPopoverOpen}>
                            <PopoverTrigger asChild>
                                <Button variant="outline" disabled={isDownloadingReport}>
                                    <Download className="h-4 w-4 mr-2" />
                                    Download Report
                                </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-auto p-4" align="end">
                                <div className="space-y-4">
                                    <div className="text-sm font-medium">Filter by date range</div>
                                    <div className="grid gap-3">
                                        <div className="space-y-1.5">
                                            <Label className="text-xs">From</Label>
                                            <div className="flex gap-2">
                                                <Popover>
                                                    <PopoverTrigger asChild>
                                                        <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                            <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                            {reportStartDate ? format(reportStartDate, 'MMM dd, yyyy') : 'Start date'}
                                                        </Button>
                                                    </PopoverTrigger>
                                                    <PopoverContent className="w-auto p-0" align="start">
                                                        <Calendar
                                                            mode="single"
                                                            selected={reportStartDate}
                                                            onSelect={setReportStartDate}
                                                            disabled={(date) => reportEndDate ? date > reportEndDate : false}
                                                        />
                                                    </PopoverContent>
                                                </Popover>
                                                <Input
                                                    type="time"
                                                    value={reportStartTime}
                                                    onChange={(e) => setReportStartTime(e.target.value)}
                                                    className="w-[100px] h-8 text-xs"
                                                />
                                            </div>
                                        </div>
                                        <div className="space-y-1.5">
                                            <Label className="text-xs">To</Label>
                                            <div className="flex gap-2">
                                                <Popover>
                                                    <PopoverTrigger asChild>
                                                        <Button variant="outline" size="sm" className="w-[140px] justify-start text-left font-normal">
                                                            <CalendarIcon className="mr-2 h-3.5 w-3.5" />
                                                            {reportEndDate ? format(reportEndDate, 'MMM dd, yyyy') : 'End date'}
                                                        </Button>
                                                    </PopoverTrigger>
                                                    <PopoverContent className="w-auto p-0" align="start">
                                                        <Calendar
                                                            mode="single"
                                                            selected={reportEndDate}
                                                            onSelect={setReportEndDate}
                                                            disabled={(date) => reportStartDate ? date < reportStartDate : false}
                                                        />
                                                    </PopoverContent>
                                                </Popover>
                                                <Input
                                                    type="time"
                                                    value={reportEndTime}
                                                    onChange={(e) => setReportEndTime(e.target.value)}
                                                    className="w-[100px] h-8 text-xs"
                                                />
                                            </div>
                                        </div>
                                    </div>
                                    <Separator />
                                    <div className="flex justify-between">
                                        <Button variant="ghost" size="sm" onClick={handleClearDateRange}>
                                            Clear
                                        </Button>
                                        <Button size="sm" onClick={handleDownloadReport} disabled={isDownloadingReport}>
                                            <Download className="h-3.5 w-3.5 mr-1.5" />
                                            {reportStartDate || reportEndDate ? 'Download Filtered' : 'Download All'}
                                        </Button>
                                    </div>
                                </div>
                            </PopoverContent>
                        </Popover>
                        {renderActionButton()}
                    </div>
                </div>
            </div>

            {/* OWNER KPI DASHBOARD - 4 KEY CARDS */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                {/* CARD 1: OUTREACH PROGRESS */}
                <Card className="border shadow-sm">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-sm font-semibold text-muted-foreground">
                                Outreach Progress
                            </CardTitle>
                            <Target className="h-4 w-4 text-blue-600" />
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <div className="flex items-baseline justify-between">
                            <span className="text-2xl font-bold tracking-tight">
                                {attemptedContacts} <span className="text-sm font-normal text-muted-foreground">/ {totalContacts}</span>
                            </span>
                            <Badge variant="outline" className="font-semibold text-xs">
                                {completionPct}%
                            </Badge>
                        </div>
                        <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                            <div
                                className="bg-blue-600 h-2 rounded-full transition-all duration-500"
                                style={{ width: `${Math.min(100, Math.max(0, completionPct))}%` }}
                            />
                        </div>
                        <div className="flex justify-between text-xs text-muted-foreground pt-1">
                            <span>Remaining: <strong className="text-foreground">{ownerSummary?.remaining_contacts ?? Math.max(0, totalContacts - attemptedContacts)}</strong></span>
                            <span>Eligible Contacts</span>
                        </div>
                    </CardContent>
                </Card>

                {/* CARD 2: CALL OUTCOMES */}
                <Card className="border shadow-sm">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-sm font-semibold text-muted-foreground">
                                Call Outcomes
                            </CardTitle>
                            <PhoneCall className="h-4 w-4 text-emerald-600" />
                        </div>
                    </CardHeader>
                    <CardContent>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="p-2 rounded bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200/50">
                                <div className="text-muted-foreground">Connected</div>
                                <div className="text-lg font-bold text-emerald-700 dark:text-emerald-400">
                                    {ownerSummary?.connected_calls ?? 0}
                                </div>
                            </div>
                            <div className="p-2 rounded bg-amber-50 dark:bg-amber-950/30 border border-amber-200/50">
                                <div className="text-muted-foreground">No Answer</div>
                                <div className="text-lg font-bold text-amber-700 dark:text-amber-400">
                                    {ownerSummary?.no_answer_calls ?? 0}
                                </div>
                            </div>
                            <div className="p-2 rounded bg-slate-50 dark:bg-slate-900 border border-slate-200">
                                <div className="text-muted-foreground">Busy / VM</div>
                                <div className="text-lg font-bold text-slate-700 dark:text-slate-300">
                                    {ownerSummary?.busy_calls ?? 0}
                                </div>
                            </div>
                            <div className="p-2 rounded bg-rose-50 dark:bg-rose-950/30 border border-rose-200/50">
                                <div className="text-muted-foreground">Failed</div>
                                <div className="text-lg font-bold text-rose-700 dark:text-rose-400">
                                    {ownerSummary?.failed_calls ?? 0}
                                </div>
                            </div>
                        </div>
                    </CardContent>
                </Card>

                {/* CARD 3: BUSINESS CONVERSIONS */}
                <Card className="border shadow-sm">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-sm font-semibold text-muted-foreground">
                                Business Conversions
                            </CardTitle>
                            <Sparkles className="h-4 w-4 text-purple-600" />
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-1.5 font-medium">
                                <Flame className="h-3.5 w-3.5 text-rose-500" />
                                Hot Leads
                            </span>
                            <button
                                onClick={() => router.push(`/leads?campaign_id=${campaignId}`)}
                                className="font-bold text-blue-600 hover:underline flex items-center gap-1"
                            >
                                {ownerSummary?.hot_leads_count ?? 0}
                                <ExternalLink className="h-3 w-3" />
                            </button>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-1.5 font-medium">
                                <Clock className="h-3.5 w-3.5 text-amber-500" />
                                Callbacks Requested
                            </span>
                            <button
                                onClick={() => router.push(`/conversations?campaign_id=${campaignId}`)}
                                className="font-bold text-blue-600 hover:underline flex items-center gap-1"
                            >
                                {ownerSummary?.callback_requested_count ?? 0}
                                <ExternalLink className="h-3 w-3" />
                            </button>
                        </div>
                        <div className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-1.5 font-medium">
                                <Users className="h-3.5 w-3.5 text-indigo-500" />
                                Appointments Booked
                            </span>
                            <button
                                onClick={() => router.push(`/appointments?campaign_id=${campaignId}`)}
                                className="font-bold text-blue-600 hover:underline flex items-center gap-1"
                            >
                                {ownerSummary?.appointments_booked_count ?? 0}
                                <ExternalLink className="h-3 w-3" />
                            </button>
                        </div>
                    </CardContent>
                </Card>

                {/* CARD 4: GOVERNANCE & SPEND */}
                <Card className="border shadow-sm">
                    <CardHeader className="pb-2">
                        <div className="flex items-center justify-between">
                            <CardTitle className="text-sm font-semibold text-muted-foreground">
                                Governance & Spend
                            </CardTitle>
                            <DollarSign className="h-4 w-4 text-emerald-600" />
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-2 text-xs">
                        <div className="flex justify-between items-center">
                            <span className="text-muted-foreground">Estimated Spend:</span>
                            <span className="font-bold text-sm text-foreground">
                                ₹{(ownerSummary?.estimated_spend_inr ?? 0).toFixed(2)}
                            </span>
                        </div>
                        <div className="flex justify-between items-center">
                            <span className="text-muted-foreground">Mode:</span>
                            <Badge variant={ownerSummary?.is_simulation ? 'outline' : 'secondary'} className="text-[10px] py-0">
                                {ownerSummary?.is_simulation ? 'Simulation' : 'Live Carrier'}
                            </Badge>
                        </div>
                        <div className="flex justify-between items-center">
                            <span className="text-muted-foreground">Emergency Stop:</span>
                            {ownerSummary?.emergency_stop_active ? (
                                <Badge variant="destructive" className="text-[10px] py-0 flex items-center gap-1">
                                    <ShieldAlert className="h-3 w-3" />
                                    Active (Calls Blocked)
                                </Badge>
                            ) : (
                                <Badge variant="outline" className="text-emerald-700 border-emerald-300 bg-emerald-50 text-[10px] py-0 flex items-center gap-1">
                                    <ShieldCheck className="h-3 w-3" />
                                    Operational
                                </Badge>
                            )}
                        </div>
                        <div className="flex justify-between items-center pt-0.5">
                            <span className="text-muted-foreground">Concurrency:</span>
                            <span className="font-medium text-foreground">
                                {campaign.max_concurrency ? `${campaign.max_concurrency} calls` : 'Default'}
                            </span>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Campaign Details */}
            <Card className="mb-6">
                <CardHeader>
                    <CardTitle>Campaign Details</CardTitle>
                    <CardDescription>
                        Configuration, source metadata, and execution history
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <dl className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <dt className="text-sm font-medium">Workflow</dt>
                            <dd className="mt-1">
                                <button
                                    onClick={handleWorkflowClick}
                                    className="text-blue-600 hover:text-blue-800 hover:underline"
                                >
                                    {campaign.workflow_name}
                                </button>
                            </dd>
                        </div>
                        <div>
                            <dt className="text-sm font-medium">Source Type</dt>
                            <dd className="mt-1 capitalize">{campaign.source_type.replace('-', ' ')}</dd>
                        </div>
                        <div>
                            <dt className="text-sm font-medium">
                                {campaign.source_type === 'csv' ? 'Source File' : 'Source Sheet'}
                            </dt>
                            <dd className="mt-1">
                                {campaign.source_type === 'csv' ? (
                                    <button
                                        onClick={handleDownloadCsv}
                                        className="text-blue-600 hover:text-blue-800 hover:underline text-sm break-all"
                                    >
                                        {campaign.source_id.split('/').pop()}
                                    </button>
                                ) : (
                                    <a
                                        href={campaign.source_id}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-blue-600 hover:text-blue-800 hover:underline text-sm break-all"
                                    >
                                        {campaign.source_id}
                                    </a>
                                )}
                            </dd>
                        </div>
                        <div>
                            <dt className="text-sm font-medium">Telephony Configuration</dt>
                            <dd className="mt-1">
                                {campaign.telephony_configuration_id ? (
                                    <button
                                        onClick={() => router.push(`/telephony-configurations/${campaign.telephony_configuration_id}`)}
                                        className="text-blue-600 hover:text-blue-800 hover:underline"
                                    >
                                        {campaign.telephony_configuration_name || `Configuration #${campaign.telephony_configuration_id}`}
                                    </button>
                                ) : (
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <Badge variant="destructive" className="text-xs">
                                            Not assigned (Required to launch)
                                        </Badge>
                                        {canEdit && (
                                            <Button
                                                variant="link"
                                                size="sm"
                                                onClick={() => router.push(`/campaigns/${campaignId}/edit`)}
                                                className="text-xs h-auto p-0 text-blue-600 hover:underline"
                                            >
                                                Assign in Edit
                                            </Button>
                                        )}
                                    </div>
                                )}
                            </dd>
                        </div>
                        <div>
                            <dt className="text-sm font-medium">State</dt>
                            <dd className="mt-1 capitalize">{campaign.state}</dd>
                        </div>
                        <div>
                            <dt className="text-sm font-medium">Progress</dt>
                            <dd className="mt-1">
                                {attemptedContacts} / {totalContacts} contacts attempted
                            </dd>
                        </div>
                        {campaign.parent_campaign_id && (
                            <div>
                                <dt className="text-sm font-medium">Redial Of</dt>
                                <dd className="mt-1">
                                    <button
                                        onClick={() => router.push(`/campaigns/${campaign.parent_campaign_id}`)}
                                        className="text-blue-600 hover:text-blue-800 hover:underline"
                                    >
                                        Campaign #{campaign.parent_campaign_id}
                                    </button>
                                </dd>
                            </div>
                        )}
                        {campaign.redialed_campaign_id && (
                            <div>
                                <dt className="text-sm font-medium">Redialed As</dt>
                                <dd className="mt-1">
                                    <button
                                        onClick={() => router.push(`/campaigns/${campaign.redialed_campaign_id}`)}
                                        className="text-blue-600 hover:text-blue-800 hover:underline"
                                    >
                                        Campaign #{campaign.redialed_campaign_id}
                                    </button>
                                </dd>
                            </div>
                        )}
                        {campaign.started_at && (
                            <div>
                                <dt className="text-sm font-medium">Started At</dt>
                                <dd className="mt-1">{formatDateTime(campaign.started_at)}</dd>
                            </div>
                        )}
                        {campaign.completed_at && (
                            <div>
                                <dt className="text-sm font-medium">Completed At</dt>
                                <dd className="mt-1">{formatDateTime(campaign.completed_at)}</dd>
                            </div>
                        )}
                    </dl>
                </CardContent>
            </Card>

            {/* Campaign Settings */}
            <Card className="mb-6">
                <CardHeader>
                    <CardTitle>Campaign Settings</CardTitle>
                    <CardDescription>
                        Concurrency and retry configuration
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    <div>
                        <dt className="text-sm font-medium">Max Concurrent Calls</dt>
                        <dd className="mt-1">
                            {campaign.max_concurrency ? (
                                <span>{campaign.max_concurrency}</span>
                            ) : (
                                <span className="text-muted-foreground">Using organization default</span>
                            )}
                        </dd>
                    </div>

                    <Separator />

                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">Automatic Retries</span>
                            {campaign.retry_config.enabled ? (
                                <Badge variant="default" className="flex items-center gap-1">
                                    <Check className="h-3 w-3" />
                                    Enabled
                                </Badge>
                            ) : (
                                <Badge variant="secondary" className="flex items-center gap-1">
                                    <X className="h-3 w-3" />
                                    Disabled
                                </Badge>
                            )}
                        </div>

                        {campaign.retry_config.enabled && (
                            <div className="grid grid-cols-2 md:grid-cols-3 gap-4 pl-4 border-l-2 border-muted">
                                <div>
                                    <dt className="text-sm text-muted-foreground">Max Retries</dt>
                                    <dd className="mt-1 font-medium">{campaign.retry_config.max_retries}</dd>
                                </div>
                                <div>
                                    <dt className="text-sm text-muted-foreground">Retry Delay</dt>
                                    <dd className="mt-1 font-medium">{campaign.retry_config.retry_delay_seconds}s</dd>
                                </div>
                                <div className="col-span-2 md:col-span-1">
                                    <dt className="text-sm text-muted-foreground">Retry On</dt>
                                    <dd className="mt-1 flex flex-wrap gap-1">
                                        {campaign.retry_config.retry_on_busy && (
                                            <Badge variant="outline" className="text-xs">Busy</Badge>
                                        )}
                                        {campaign.retry_config.retry_on_no_answer && (
                                            <Badge variant="outline" className="text-xs">No Answer</Badge>
                                        )}
                                        {campaign.retry_config.retry_on_voicemail && (
                                            <Badge variant="outline" className="text-xs">Voicemail</Badge>
                                        )}
                                    </dd>
                                </div>
                            </div>
                        )}
                    </div>

                    <Separator />

                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-medium">Call Schedule</span>
                            <div className="flex items-center gap-2">
                                {campaign.schedule_config?.enabled ? (
                                    <Badge variant="default" className="flex items-center gap-1">
                                        <Clock className="h-3 w-3" />
                                        Enabled
                                    </Badge>
                                ) : (
                                    <Badge variant="secondary" className="flex items-center gap-1">
                                        <X className="h-3 w-3" />
                                        Not configured
                                    </Badge>
                                )}
                            </div>
                        </div>

                        {campaign.schedule_config?.enabled && (
                            <div className="pl-4 border-l-2 border-muted space-y-3">
                                <div>
                                    <dt className="text-sm text-muted-foreground">Timezone</dt>
                                    <dd className="mt-1 font-medium">{campaign.schedule_config.timezone.replace(/_/g, ' ')}</dd>
                                </div>
                                <div>
                                    <dt className="text-sm text-muted-foreground">Time Slots</dt>
                                    <dd className="mt-1 flex flex-wrap gap-2">
                                        {campaign.schedule_config.slots.map((slot, index) => {
                                            const dayNames = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
                                            return (
                                                <div key={index} className="flex items-center gap-1">
                                                    <Badge variant="outline" className="text-xs">{dayNames[slot.day_of_week]}</Badge>
                                                    <span className="text-sm">{slot.start_time} - {slot.end_time}</span>
                                                </div>
                                            );
                                        })}
                                    </dd>
                                </div>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>

            {/* COLLAPSIBLE TECHNICAL DRILLDOWN & WORKFLOW RUNS */}
            <Card className="mb-6">
                <Collapsible open={!isRunsCollapsed} onOpenChange={(open) => setIsRunsCollapsed(!open)}>
                    <CardHeader className="cursor-pointer" onClick={() => setIsRunsCollapsed(!isRunsCollapsed)}>
                        <div className="flex items-center justify-between">
                            <div>
                                <CardTitle className="text-lg flex items-center gap-2">
                                    <Activity className="h-5 w-5 text-indigo-600" />
                                    Technical Drilldown & Call Traces
                                </CardTitle>
                                <CardDescription>
                                    Inspect underlying workflow runs, SIP participant dispatches, and contact audio
                                </CardDescription>
                            </div>
                            <CollapsibleTrigger asChild>
                                <Button variant="ghost" size="sm">
                                    {isRunsCollapsed ? (
                                        <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                            <span>Expand Runs</span>
                                            <ChevronDown className="h-4 w-4" />
                                        </div>
                                    ) : (
                                        <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                            <span>Collapse</span>
                                            <ChevronUp className="h-4 w-4" />
                                        </div>
                                    )}
                                </Button>
                            </CollapsibleTrigger>
                        </div>
                    </CardHeader>
                    <CollapsibleContent>
                        <CardContent>
                            <CampaignRuns
                                campaignId={campaignId}
                                workflowId={campaign.workflow_id}
                                searchParams={searchParams}
                            />
                        </CardContent>
                    </CollapsibleContent>
                </Collapsible>
            </Card>

            {/* Activity Log */}
            <Card className="mb-6">
                <CardHeader>
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div>
                            <CardTitle className="text-lg">Activity Log</CardTitle>
                            <CardDescription>
                                Recent state transitions and operational events. Newest first.
                            </CardDescription>
                        </div>
                        <div className="flex items-center gap-2">
                            {availableEventTypes.length > 1 && (
                                <select
                                    value={logEventFilter}
                                    onChange={(e) => {
                                        setLogEventFilter(e.target.value);
                                        setLogPage(1);
                                    }}
                                    className="text-xs h-8 px-2 rounded-md border border-input bg-background"
                                >
                                    <option value="all">All Events ({sortedLogs.length})</option>
                                    {availableEventTypes.map((evt) => (
                                        <option key={evt} value={evt}>
                                            {formatEventLabel(evt)} ({sortedLogs.filter((l) => l.event === evt).length})
                                        </option>
                                    ))}
                                </select>
                            )}
                            <select
                                value={logsPerPage}
                                onChange={(e) => {
                                    setLogsPerPage(Number(e.target.value));
                                    setLogPage(1);
                                }}
                                className="text-xs h-8 px-2 rounded-md border border-input bg-background"
                            >
                                <option value={10}>10 / page</option>
                                <option value={25}>25 / page</option>
                                <option value={50}>50 / page</option>
                            </select>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {filteredLogs.length === 0 ? (
                        <p className="text-sm text-muted-foreground">
                            {sortedLogs.length === 0 ? 'No events recorded yet.' : 'No events match the selected filter.'}
                        </p>
                    ) : (
                        <>
                            <ul className="space-y-3">
                                {paginatedLogs.map((entry, idx) => (
                                    <li
                                        key={`${entry.ts}-${idx}`}
                                        className="flex gap-3 border-b last:border-b-0 pb-3 last:pb-0"
                                    >
                                        <div className="mt-0.5">{getLogIcon(entry.level)}</div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <Badge variant={getLogBadgeVariant(entry.level)} className="text-xs">
                                                    {entry.level}
                                                </Badge>
                                                <span className="font-semibold text-xs text-foreground">
                                                    {formatEventLabel(entry.event)}
                                                </span>
                                                <code className="text-[11px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                    {entry.event}
                                                </code>
                                                <span className="text-xs text-muted-foreground">
                                                    {formatLogTimestamp(entry.ts)}
                                                </span>
                                            </div>
                                            <p className="text-sm mt-1 break-words">{entry.message}</p>
                                            {entry.details && Object.keys(entry.details).length > 0 && (
                                                <details className="mt-1.5">
                                                    <summary className="text-xs text-muted-foreground cursor-pointer hover:text-foreground">
                                                        Details
                                                    </summary>
                                                    <pre className="mt-1.5 text-xs bg-muted rounded p-2 overflow-x-auto whitespace-pre-wrap break-words">
                                                        {JSON.stringify(entry.details, null, 2)}
                                                    </pre>
                                                </details>
                                            )}
                                        </div>
                                    </li>
                                ))}
                            </ul>

                            {filteredLogs.length > logsPerPage && (
                                <div className="flex items-center justify-between pt-4 mt-2 border-t text-xs text-muted-foreground">
                                    <span>
                                        Showing {(logPage - 1) * logsPerPage + 1} to{' '}
                                        {Math.min(logPage * logsPerPage, filteredLogs.length)} of {filteredLogs.length} events
                                    </span>
                                    <div className="flex items-center gap-2">
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setLogPage((p) => Math.max(1, p - 1))}
                                            disabled={logPage <= 1}
                                            className="h-7 px-2 text-xs"
                                        >
                                            Previous
                                        </Button>
                                        <span className="text-xs">
                                            Page {logPage} of {totalLogPages}
                                        </span>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            onClick={() => setLogPage((p) => Math.min(totalLogPages, p + 1))}
                                            disabled={logPage >= totalLogPages}
                                            className="h-7 px-2 text-xs"
                                        >
                                            Next
                                        </Button>
                                    </div>
                                </div>
                            )}
                        </>
                    )}
                </CardContent>
            </Card>

            {/* RETRY ELIGIBLE CONTACTS (REDIAL) DIALOG */}
            <Dialog open={isRedialDialogOpen} onOpenChange={setIsRedialDialogOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Retry Eligible Contacts</DialogTitle>
                        <DialogDescription>
                            Create a follow-up campaign that re-dials unreachable contacts. Answered calls and opted-out contacts are strictly excluded.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        <div className="space-y-1.5">
                            <Label htmlFor="redial-name">Follow-up Campaign Name</Label>
                            <Input
                                id="redial-name"
                                value={redialName}
                                onChange={(e) => setRedialName(e.target.value)}
                                placeholder="Follow-up Campaign Name"
                            />
                        </div>

                        <div className="space-y-2.5">
                            <Label className="text-sm font-medium">Retry when previous call resulted in:</Label>
                            <div className="space-y-2 rounded-md border p-3 bg-muted/40">
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-voicemail"
                                        checked={redialOnVoicemail}
                                        onCheckedChange={(v) => {
                                            const val = v === true;
                                            setRedialOnVoicemail(val);
                                            fetchRedialPreview(val, redialOnNoAnswer, redialOnBusy);
                                        }}
                                    />
                                    <Label htmlFor="redial-voicemail" className="font-normal cursor-pointer text-sm">
                                        Voicemail / Answering Machine
                                    </Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-no-answer"
                                        checked={redialOnNoAnswer}
                                        onCheckedChange={(v) => {
                                            const val = v === true;
                                            setRedialOnNoAnswer(val);
                                            fetchRedialPreview(redialOnVoicemail, val, redialOnBusy);
                                        }}
                                    />
                                    <Label htmlFor="redial-no-answer" className="font-normal cursor-pointer text-sm">
                                        No Answer
                                    </Label>
                                </div>
                                <div className="flex items-center gap-2">
                                    <Checkbox
                                        id="redial-busy"
                                        checked={redialOnBusy}
                                        onCheckedChange={(v) => {
                                            const val = v === true;
                                            setRedialOnBusy(val);
                                            fetchRedialPreview(redialOnVoicemail, redialOnNoAnswer, val);
                                        }}
                                    />
                                    <Label htmlFor="redial-busy" className="font-normal cursor-pointer text-sm">
                                        Line Busy
                                    </Label>
                                </div>
                            </div>
                        </div>

                        {/* Candidate Preview Box */}
                        <div className="rounded-md border p-3 bg-slate-50 dark:bg-slate-900/50 space-y-2 text-xs">
                            <div className="flex justify-between items-center">
                                <span className="font-semibold text-foreground">Eligible Candidates:</span>
                                {isLoadingPreview ? (
                                    <span className="text-muted-foreground">Calculating...</span>
                                ) : (
                                    <Badge variant="secondary" className="font-bold">
                                        {redialPreview?.total_eligible ?? 0} Contacts
                                    </Badge>
                                )}
                            </div>
                            <p className="text-muted-foreground text-[11px] leading-relaxed">
                                ✓ Answered/completed subscribers are excluded from redialing.<br />
                                ✓ Suppression and DNC list checks will be applied prior to dispatch.<br />
                                ✓ This creates a saved follow-up draft; no calls are dialed immediately.
                            </p>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setIsRedialDialogOpen(false)}
                            disabled={isRedialing}
                        >
                            Cancel
                        </Button>
                        <Button
                            onClick={handleRedial}
                            disabled={isRedialing || (redialPreview ? redialPreview.total_eligible === 0 : false)}
                            className="bg-indigo-600 hover:bg-indigo-700 text-white"
                        >
                            {isRedialing ? 'Creating Draft...' : 'Create Follow-up Draft'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* CANCEL CONFIRMATION DIALOG */}
            <Dialog open={isCancelDialogOpen} onOpenChange={setIsCancelDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Cancel Campaign</DialogTitle>
                        <DialogDescription>
                            Are you sure you want to cancel this campaign? All remaining queued calls will be terminated.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setIsCancelDialogOpen(false)}
                            disabled={isCancelling}
                        >
                            Back
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleCancelCampaign}
                            disabled={isCancelling}
                        >
                            {isCancelling ? 'Cancelling...' : 'Confirm Cancel'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
