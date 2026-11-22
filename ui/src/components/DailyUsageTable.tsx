import type { DailyUsageBreakdownResponse } from '@/client/types.gen';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Table,
    TableBody,
    TableCell,
    TableFooter,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';

interface DailyUsageTableProps {
    data: DailyUsageBreakdownResponse | null;
    isLoading: boolean;
}

export function DailyUsageTable({ data, isLoading }: DailyUsageTableProps) {
    // Format date for display
    const formatDate = (dateString: string) => {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            month: 'short',
            day: 'numeric',
            year: 'numeric'
        });
    };

    if (isLoading) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Daily Usage Breakdown</CardTitle>
                    <CardDescription>Last 7 days of usage across voice agents</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="animate-pulse space-y-3">
                        {[...Array(7)].map((_, i) => (
                            <div key={i} className="h-12 bg-muted rounded"></div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (!data || !data.breakdown || data.breakdown.length === 0) {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Daily Usage Breakdown</CardTitle>
                    <CardDescription>Last 7 days of usage across voice agents</CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="text-center py-8 space-y-1">
                        <p className="text-sm font-medium text-foreground">No usage records found</p>
                        <p className="text-xs text-muted-foreground">Zero calls or conversational minutes logged in the selected window.</p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card>
            <CardHeader>
                <CardTitle>Daily Usage Breakdown</CardTitle>
                <CardDescription>Last 7 days of usage across voice agents</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="bg-card border border-border/70 rounded-lg overflow-x-auto shadow-xs">
                    <Table className="min-w-[600px]">
                        <TableHeader>
                            <TableRow className="bg-muted/50 hover:bg-muted/60">
                                <TableHead className="font-semibold text-foreground">Date</TableHead>
                                <TableHead className="font-semibold text-right text-foreground">Usage (minutes)</TableHead>
                                <TableHead className="font-semibold text-right text-foreground">Cost (USD)</TableHead>
                                <TableHead className="font-semibold text-right text-foreground">Calls</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {data.breakdown.map((day) => {
                                const isZeroUsage = day.call_count === 0 && day.minutes === 0;
                                const isMissingBilling = day.cost_usd === undefined || day.cost_usd === null;

                                return (
                                    <TableRow key={day.date} className="hover:bg-accent/40">
                                        <TableCell className="font-medium text-foreground">
                                            {formatDate(day.date)}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            {isZeroUsage ? (
                                                <span className="text-muted-foreground text-xs italic">0.0 min</span>
                                            ) : (
                                                <span className="font-mono text-sm">{day.minutes.toFixed(1)}</span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            {isMissingBilling ? (
                                                <span className="text-muted-foreground text-xs italic">No billing data</span>
                                            ) : isZeroUsage ? (
                                                <span className="text-muted-foreground text-xs">—</span>
                                            ) : day.cost_usd === 0 ? (
                                                <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                                                    $0.00 <span className="text-[10px] text-muted-foreground font-normal">(Free tier)</span>
                                                </span>
                                            ) : (
                                                <span className="font-mono font-medium text-foreground">
                                                    ${(day.cost_usd ?? 0).toFixed(2)}
                                                </span>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            {day.call_count === 0 ? (
                                                <span className="text-muted-foreground text-xs italic">Zero calls</span>
                                            ) : (
                                                <span className="font-mono text-sm font-medium">{day.call_count}</span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                        <TableFooter>
                            <TableRow className="bg-muted/40 font-semibold">
                                <TableCell className="text-foreground">Total</TableCell>
                                <TableCell className="text-right font-mono text-foreground">
                                    {data.total_minutes.toFixed(1)}
                                </TableCell>
                                <TableCell className="text-right font-mono text-foreground">
                                    {data.total_cost_usd !== undefined && data.total_cost_usd !== null
                                        ? `$${data.total_cost_usd.toFixed(2)}`
                                        : '-'
                                    }
                                </TableCell>
                                <TableCell className="text-right font-mono text-foreground">
                                    {data.breakdown.reduce((sum, day) => sum + day.call_count, 0)}
                                </TableCell>
                            </TableRow>
                        </TableFooter>
                    </Table>
                </div>
            </CardContent>
        </Card>
    );
}
