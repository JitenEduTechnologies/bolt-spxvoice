import { client } from "@/client/client.gen";
import { authHeaders, type GetToken, saveBlob } from "@/lib/conversationsApi";

type ApiResult<T> = {
  data?: T;
  error?: unknown;
};

export interface RecalculateWorkflowRunCostResponse {
  status: string;
  workflow_run_id: number;
}

function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean)
      .join(", ");
  }
  return null;
}

function errorMessage(error: unknown, fallback: string): string {
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const detail = "detail" in error ? detailToMessage((error as { detail: unknown }).detail) : null;
    if (detail) return detail;
    if ("message" in error && typeof (error as { message: unknown }).message === "string") {
      return (error as { message: string }).message;
    }
  }
  return fallback;
}

async function unwrap<T>(request: Promise<ApiResult<T>>, fallback: string): Promise<T> {
  const response = await request;
  if (response.error) {
    throw new Error(errorMessage(response.error, fallback));
  }
  if (response.data === undefined || response.data === null) {
    throw new Error(fallback);
  }
  return response.data;
}

export interface CostSummary {
  scope: { runs: number; costed_runs: number; uncosted_runs: number };
  totals_inr: { total: number; ai: number; telephony: number };
  costed_minutes: number;
  per_minute_inr: number | null;
  per_minute_basis: string;
  status_breakdown: Record<string, number>;
  reconciled_runs: number;
  estimated_runs: number;
  basis: "no_cost_data" | "reconciled" | "estimate" | "mixed";
  telephony: { pstn_runs: number; applicable: boolean; negotiated_rate_configured: boolean };
  pricing_inputs: {
    unpriced_models: { model: string; runs: number }[];
    fx: { pair?: string; rate?: number; source_last_checked?: string }[];
    estimated_components: number;
    notes: string[];
  };
  target: { per_minute_inr: number | null; status: string };
}

export interface CostRunsQuery {
  page: number;
  limit: number;
  sort_by: string;
  sort_order: "asc" | "desc";
  filters?: string;
}

export const costApi = {
  listRuns<T>(query: CostRunsQuery): Promise<T> {
    return unwrap(
      client.get<{ 200: T }>({
        url: "/api/v1/superuser/workflow-runs",
        query: {
          page: query.page,
          limit: query.limit,
          sort_by: query.sort_by,
          sort_order: query.sort_order,
          ...(query.filters ? { filters: query.filters } : {}),
        },
      }),
      "Failed to load cost analytics",
    );
  },
  getSummary(filters?: string): Promise<CostSummary> {
    return unwrap(
      client.get<{ 200: CostSummary }>({
        url: "/api/v1/superuser/cost-summary",
        query: filters ? { filters } : {},
      }),
      "Failed to load the cost summary",
    );
  },
  async exportRecords(
    getAccessToken: GetToken,
    format: "csv" | "json" | "zip",
    scope: { ids?: number[]; filters?: string },
  ): Promise<number> {
    const params = new URLSearchParams({ format });
    if (scope.ids?.length) params.set("ids", scope.ids.join(","));
    else if (scope.filters) params.set("filters", scope.filters);
    const res = await fetch(`/api/v1/superuser/cost-export?${params.toString()}`, {
      headers: await authHeaders(getAccessToken),
    });
    if (!res.ok) {
      let message = `Export failed (${res.status})`;
      try {
        const body = await res.json();
        if (typeof body?.detail === "string") message = body.detail;
      } catch {
        /* not JSON */
      }
      throw new Error(message);
    }
    const match = /filename="?([^";]+)"?/i.exec(res.headers.get("Content-Disposition") || "");
    saveBlob(await res.blob(), match?.[1] || `spx-cost-records.${format}`);
    return Number(res.headers.get("X-Export-Count") || "0");
  },

  recalculateWorkflowRunCost(workflowRunId: number): Promise<RecalculateWorkflowRunCostResponse> {
    return unwrap(
      client.post<{ 200: RecalculateWorkflowRunCostResponse }>({
        url: `/api/v1/superuser/workflow-runs/${workflowRunId}/recalculate-cost`,
      }),
      "Failed to recalculate run cost",
    );
  },
};
