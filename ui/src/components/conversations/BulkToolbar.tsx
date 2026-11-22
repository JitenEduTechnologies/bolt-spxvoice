"use client";

import { Archive, ArchiveRestore, ChevronLeft, ChevronRight, Download, Loader2, RefreshCw, Trash2, X } from "lucide-react";
import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MAX_BULK_ANALYZE, MAX_BULK_SELECTION, type useConversationList } from "@/hooks/useConversationList";
import type { BulkAnalyzeResult, BulkMode } from "@/lib/conversationsApi";

type ListState = ReturnType<typeof useConversationList>;

const CONFIRM_COPY: Record<BulkMode, { title: string; body: string; action: string }> = {
  archive: {
    title: "Archive",
    body: "Archived conversations are hidden from the active lists. Nothing is deleted: transcripts, analysis and cost records are kept, and you can restore them at any time.",
    action: "Archive",
  },
  trash: {
    title: "Move to Trash",
    body: "Conversations in Trash are hidden from the active lists but are NOT permanently deleted. Transcripts, analysis and all cost and usage records are kept, and you can restore them from the Trash view.",
    action: "Move to Trash",
  },
  restore: {
    title: "Restore",
    body: "These conversations will appear in the active lists again.",
    action: "Restore",
  },
};

/** Selection summary, bulk Archive / Move to Trash / Restore (with confirmation) and exports. */
export function BulkToolbar({ list, noun = "conversation" }: { list: ListState; noun?: string }) {
  const [confirm, setConfirm] = useState<null | BulkMode>(null);
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  const [preview, setPreview] = useState<BulkAnalyzeResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const openAnalyze = async () => {
    setAnalyzeOpen(true);
    setPreview(null);
    setPreviewLoading(true);
    setPreview(await list.runAnalyze(true)); // dry run: nothing is written
    setPreviewLoading(false);
  };
  const view = list.filters.archived;
  const inRestoreView = view === "archived" || view === "trashed";
  const plural = (n: number) => `${n} ${noun}${n === 1 ? "" : "s"}`;
  const hasSelection = list.selectionCount > 0;
  const copy = confirm ? CONFIRM_COPY[confirm] : null;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-card px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {hasSelection ? (
            <>
              <span className="font-semibold">{plural(list.selectionCount)} selected</span>
              <Button variant="ghost" size="sm" className="h-7 gap-1 text-xs" onClick={list.clearSelection}>
                <X className="h-3.5 w-3.5" /> Clear
              </Button>
            </>
          ) : (
            <span className="text-muted-foreground text-xs">
              {view === "trashed"
                ? "Trash holds conversations you removed from the active lists. Select rows to restore them."
                : "Select rows to archive, move to Trash or export them, or export everything that matches the current filters."}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {hasSelection && inRestoreView && (
            <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={list.busy} onClick={() => setConfirm("restore")}>
              <ArchiveRestore className="h-3.5 w-3.5" /> Restore
            </Button>
          )}
          {hasSelection && !inRestoreView && (
            <>
              <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={list.busy} onClick={() => setConfirm("archive")}>
                <Archive className="h-3.5 w-3.5" /> Archive
              </Button>
              <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={list.busy} onClick={() => setConfirm("trash")}>
                <Trash2 className="h-3.5 w-3.5" /> Move to Trash
              </Button>
              <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={list.busy} onClick={() => void openAnalyze()}>
                <RefreshCw className="h-3.5 w-3.5" /> Re-analyze
              </Button>
            </>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" disabled={list.busy || list.total === 0}>
                {list.busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                Export
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-72">
              <DropdownMenuLabel className="text-xs">
                {hasSelection && !list.selectAllMatching
                  ? `Export ${plural(list.selectionCount)} selected`
                  : `Export all ${plural(list.total)} matching the filters`}
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => list.runExport("csv")}>CSV (spreadsheet, Unicode-safe)</DropdownMenuItem>
              <DropdownMenuItem onClick={() => list.runExport("json", { includeTranscripts: true })}>JSON archive with transcripts</DropdownMenuItem>
              <DropdownMenuItem onClick={() => list.runExport("zip")}>ZIP: transcripts + manifest</DropdownMenuItem>
              <DropdownMenuItem onClick={() => list.runExport("zip", { includeRecordings: true })}>
                ZIP including recordings (up to 10 verified)
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <p className="px-2 py-1.5 text-[11px] text-muted-foreground">
                Exports contain full phone numbers and are recorded in the audit log. Recordings are included only when you
                choose the recordings option and the file is verified in storage. PDF and Excel are not available.
              </p>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Select-all-matching banner (server re-verifies the scope before acting) */}
      {list.allPageSelected && list.total > list.items.length && !list.selectAllMatching && (
        <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-2 text-xs">
          All {list.items.length} on this page are selected.{" "}
          {list.canSelectAllMatching ? (
            <button className="font-semibold text-primary underline" onClick={list.selectAllMatchingRows}>
              Select all {list.total} {noun}s matching the filters
            </button>
          ) : (
            <span className="text-muted-foreground">
              {list.total} match, but at most {MAX_BULK_SELECTION} can be selected at once. Narrow the filters (for example by
              date or classification) and try again.
            </span>
          )}
        </div>
      )}
      {list.selectAllMatching && (
        <div className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-2 text-xs">
          All {list.total} {noun}s matching the current filters are selected. The scope is re-checked on the server
          before anything changes.
        </div>
      )}

      <AlertDialog open={analyzeOpen} onOpenChange={(open) => !open && setAnalyzeOpen(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-analyze {plural(list.selectionCount)} with the latest rules?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm">
                <p>
                  Titles, summaries and classifications are recomputed from what the caller actually said. The earlier result is kept
                  in the history. Corrections you made with the earlier version are kept there too, but the new analysis is what is
                  displayed. At most {MAX_BULK_ANALYZE} conversations are processed at a time.
                </p>
                {previewLoading && <p className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Checking what would change…</p>}
                {!previewLoading && preview === null && (
                  <p className="text-destructive">Preview unavailable. Select {MAX_BULK_ANALYZE} or fewer and try again.</p>
                )}
                {preview && (
                  <div className="rounded-md border bg-muted/40 p-3 text-xs">
                    <p className="font-semibold">
                      {preview.eligible} will be re-analyzed · {preview.skipped.up_to_date} already up to date · {preview.skipped.no_transcript} have no transcript (left untouched)
                    </p>
                    {preview.examples.length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {preview.examples.map((ex) => (
                          <li key={ex.run_id}>
                            #{ex.run_id}: {ex.before.classification}
                            {ex.before.intent ? ` (“${ex.before.intent}”)` : ""} → <strong>{ex.after.classification}</strong>, “{ex.after.title}”
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={previewLoading || !preview || preview.eligible === 0}
              onClick={() => {
                setAnalyzeOpen(false);
                void list.runAnalyze(false);
              }}
            >
              Re-analyze
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirm !== null} onOpenChange={(open) => !open && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {copy?.title} {plural(list.selectionCount)}?
            </AlertDialogTitle>
            <AlertDialogDescription>{copy?.body}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                const mode = confirm;
                setConfirm(null);
                if (mode) void list.runBulk(mode);
              }}
            >
              {copy?.action}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

export function PaginationBar({ list, noun = "conversation" }: { list: ListState; noun?: string }) {
  if (list.total === 0) return null;
  const from = (list.page - 1) * list.pageSize + 1;
  const to = Math.min(list.page * list.pageSize, list.total);
  return (
    <div className="flex flex-col gap-2 border-t bg-muted/20 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-xs text-muted-foreground">
        Showing {from}–{to} of {list.total} {noun}
        {list.total === 1 ? "" : "s"}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" className="h-8 gap-1 text-xs" disabled={list.page <= 1} onClick={() => list.goToPage(list.page - 1)}>
          <ChevronLeft className="h-3.5 w-3.5" /> Previous
        </Button>
        <span className="px-2 text-xs font-medium">
          {list.page} / {list.totalPages}
        </span>
        <Button
          variant="outline"
          size="sm"
          className="h-8 gap-1 text-xs"
          disabled={list.page >= list.totalPages}
          onClick={() => list.goToPage(list.page + 1)}
        >
          Next <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
