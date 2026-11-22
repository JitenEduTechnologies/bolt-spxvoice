"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { useRealtimeDashboard } from "@/hooks/useRealtimeDashboard";
import {
  type BulkAnalyzeResult,
  bulkAnalyze,
  type BulkMode,
  type BulkSelection,
  bulkLifecycle,
  type ConversationFilters,
  type ConversationItem,
  type ConversationListResponse,
  DEFAULT_FILTERS,
  downloadExport,
  fetchConversations,
  filtersToBody,
  type GetToken,
} from "@/lib/conversationsApi";
import { REFRESH_EVENTS, RefetchScheduler } from "@/lib/refetchScheduler";

export { REFRESH_EVENTS };
/** Server-side cap for "select all matching" (mirrors MAX_BULK_SELECTION). */
export const MAX_BULK_SELECTION = 1000;
/** Re-analysis reads transcripts, so it is capped lower per request (mirrors MAX_BULK_ANALYZE). */
export const MAX_BULK_ANALYZE = 200;

interface Options {
  authReady: boolean;
  getAccessToken: GetToken;
  initialFilters?: Partial<ConversationFilters>;
  pageSize?: number;
}

export function useConversationList({ authReady, getAccessToken, initialFilters, pageSize = 25 }: Options) {
  const [filters, setFiltersState] = useState<ConversationFilters>({ ...DEFAULT_FILTERS, ...initialFilters });
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<{ key: string; order: "asc" | "desc" }>({ key: "created_at", order: "desc" });
  const [data, setData] = useState<ConversationListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [selectAllMatching, setSelectAllMatching] = useState(false);
  const [busy, setBusy] = useState(false);

  const requestSeq = useRef(0);
  const inFlight = useRef<AbortController | null>(null);
  const schedulerRef = useRef<RefetchScheduler | null>(null);

  const load = useCallback(
    async (silent = false) => {
      if (!authReady) return;
      const seq = ++requestSeq.current;
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      if (silent) setRefreshing(true);
      else setLoading(true);
      try {
        const result = await fetchConversations(
          getAccessToken,
          filters,
          { limit: pageSize, offset: (page - 1) * pageSize, sort: sort.key, order: sort.order },
          controller.signal,
        );
        if (seq !== requestSeq.current) return; // a newer request superseded this one
        // If the page vanished (e.g. rows were archived), step back instead of showing nothing.
        if (result.items.length === 0 && result.total > 0 && page > 1) {
          setPage(Math.max(1, Math.ceil(result.total / pageSize)));
          return;
        }
        schedulerRef.current?.markFetched();
        setData(result);
        setError(null);
      } catch (err) {
        if ((err as Error)?.name === "AbortError" || seq !== requestSeq.current) return;
        setError(err instanceof Error ? err.message : "Failed to load conversations");
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [authReady, getAccessToken, filters, page, pageSize, sort],
  );

  useEffect(() => {
    void load(false);
    return () => inFlight.current?.abort();
  }, [load]);

  // --- real-time: events are hints; the server is always re-queried ------------------
  const { subscribe, status } = useRealtimeDashboard();
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const scheduler = new RefetchScheduler({
      run: () => void loadRef.current(true),
      isVisible: () => typeof document === "undefined" || document.visibilityState !== "hidden",
    });
    schedulerRef.current = scheduler;
    const unsubscribe = subscribe("*", (event) => {
      if (REFRESH_EVENTS.has(event.event_type)) scheduler.notifyEvent();
    });
    const onVisible = () => {
      if (document.visibilityState === "visible") scheduler.notifyVisible();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      unsubscribe();
      document.removeEventListener("visibilitychange", onVisible);
      scheduler.dispose();
      schedulerRef.current = null;
    };
  }, [subscribe]);

  // After a dropped connection comes back, resynchronise from the durable source of truth.
  const wasConnected = useRef(false);
  useEffect(() => {
    if (status === "connected") {
      if (wasConnected.current) schedulerRef.current?.notifyReconnect();
      wasConnected.current = true;
    }
  }, [status]);

  // --- filters / paging --------------------------------------------------------------
  const clearSelection = useCallback(() => {
    setSelected(new Set());
    setSelectAllMatching(false);
  }, []);

  const setFilters = useCallback(
    (patch: Partial<ConversationFilters>) => {
      setFiltersState((prev) => ({ ...prev, ...patch }));
      setPage(1);
      clearSelection(); // a selection never survives a scope change
    },
    [clearSelection],
  );

  const goToPage = useCallback((next: number) => setPage(Math.max(1, next)), []);

  // --- selection ---------------------------------------------------------------------
  const items: ConversationItem[] = useMemo(() => data?.items ?? [], [data]);
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageIds = useMemo(() => items.map((i) => i.id), [items]);
  const allPageSelected = pageIds.length > 0 && (selectAllMatching || pageIds.every((id) => selected.has(id)));
  const selectionCount = selectAllMatching ? total : selected.size;

  const toggleRow = useCallback((id: number) => {
    setSelectAllMatching(false);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const togglePage = useCallback(() => {
    setSelectAllMatching(false);
    setSelected((prev) => {
      const next = new Set(prev);
      const everySelected = pageIds.every((id) => next.has(id));
      pageIds.forEach((id) => (everySelected ? next.delete(id) : next.add(id)));
      return next;
    });
  }, [pageIds]);

  const canSelectAllMatching = total > pageIds.length && total <= MAX_BULK_SELECTION;
  const selectAllMatchingRows = useCallback(() => {
    if (total <= MAX_BULK_SELECTION) {
      setSelected(new Set());
      setSelectAllMatching(true);
    }
  }, [total]);

  const buildSelection = useCallback((): BulkSelection | null => {
    if (selectAllMatching) return { filters: filtersToBody(filters), expected_count: total };
    if (selected.size === 0) return null;
    return { ids: Array.from(selected) };
  }, [selectAllMatching, filters, total, selected]);

  const runBulk = useCallback(
    async (mode: BulkMode, reason?: string) => {
      const selection = buildSelection();
      if (!selection) return;
      setBusy(true);
      try {
        const result = await bulkLifecycle(getAccessToken, selection, mode, reason);
        const verb = mode === "restore" ? "Restored" : mode === "trash" ? "Moved to Trash:" : "Archived";
        toast.success(
          `${verb} ${result.changed} conversation${result.changed === 1 ? "" : "s"}` +
            (result.skipped ? ` (${result.skipped} skipped)` : ""),
        );
        clearSelection();
        await load(true);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Bulk action failed");
        await load(true); // the scope may have changed; show the current truth
      } finally {
        setBusy(false);
      }
    },
    [buildSelection, getAccessToken, clearSelection, load],
  );

  const runAnalyze = useCallback(
    async (dryRun: boolean): Promise<BulkAnalyzeResult | null> => {
      const selection = buildSelection();
      if (!selection) return null;
      setBusy(true);
      try {
        const result = await bulkAnalyze(getAccessToken, selection, dryRun);
        if (!dryRun) {
          toast.success(
            `Re-analyzed ${result.changed ?? 0} conversation${result.changed === 1 ? "" : "s"} with the latest rules`,
          );
          clearSelection();
          await load(true);
        }
        return result;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Re-analysis failed");
        if (!dryRun) await load(true);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [buildSelection, getAccessToken, clearSelection, load],
  );

  const runExport = useCallback(
    async (format: "csv" | "json" | "zip", opts: { includeTranscripts?: boolean; includeRecordings?: boolean } = {}) => {
      setBusy(true);
      try {
        const ids = !selectAllMatching && selected.size > 0 ? Array.from(selected) : undefined;
        const count = await downloadExport(getAccessToken, format, { ids, filters, ...opts });
        toast.success(`Exported ${count} conversation${count === 1 ? "" : "s"} as ${format.toUpperCase()}`);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Export failed");
      } finally {
        setBusy(false);
      }
    },
    [getAccessToken, selected, selectAllMatching, filters],
  );

  return {
    filters,
    setFilters,
    page,
    goToPage,
    pageSize,
    totalPages,
    sort,
    setSort,
    items,
    total,
    facets: data?.facets?.classification ?? {},
    loading,
    refreshing,
    error,
    reload: () => load(true),
    connection: status,
    // selection
    selected,
    selectAllMatching,
    selectionCount,
    allPageSelected,
    canSelectAllMatching,
    toggleRow,
    togglePage,
    selectAllMatchingRows,
    clearSelection,
    // actions
    busy,
    runBulk,
    runAnalyze,
    runExport,
  };
}
