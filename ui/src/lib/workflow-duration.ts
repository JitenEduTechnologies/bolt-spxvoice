export interface WorkflowVersionLike {
    version_number?: number | null;
    status?: string | null;
    published_at?: string | null;
    workflow_configurations?: Record<string, unknown> | null;
}

export type DurationSource = "canonical" | "legacy" | "none";

export interface DurationResolution {
    seconds: number | null;
    source: DurationSource;
    valid: boolean;
}

export interface PublishedDurationSummary {
    /** False when the published-version readback failed; never guess a value. */
    available: boolean;
    publishedExists: boolean;
    publishedDuration: number | null;
    publishedDurationSource: DurationSource;
    publishedDurationValid: boolean;
    publishedVersionNumber: number | null;
    hasUnpublishedChanges: boolean;
}

export const MIN_DURATION_SECONDS = 30;
export const MAX_DURATION_SECONDS = 3600;

function isInRange(value: number): boolean {
    return value >= MIN_DURATION_SECONDS && value <= MAX_DURATION_SECONDS;
}

/**
 * Resolve the duration limit from a configuration object.
 * The canonical worker key wins; the legacy UI key is only a fallback when the
 * canonical key is absent. A non-numeric canonical value is reported as
 * unknown instead of being silently replaced by the legacy value.
 */
export function resolveDuration(
    configurations: Record<string, unknown> | null | undefined,
): DurationResolution {
    if (!configurations) {
        return { seconds: null, source: "none", valid: false };
    }
    const canonical = configurations.max_call_duration_seconds;
    if (canonical !== undefined && canonical !== null) {
        const seconds =
            typeof canonical === "number" && Number.isFinite(canonical)
                ? canonical
                : null;
        return {
            seconds,
            source: "canonical",
            valid: seconds !== null && isInRange(seconds),
        };
    }
    const legacy = configurations.max_call_duration;
    if (legacy !== undefined && legacy !== null) {
        const seconds =
            typeof legacy === "number" && Number.isFinite(legacy) ? legacy : null;
        return {
            seconds,
            source: "legacy",
            valid: seconds !== null && isInRange(seconds),
        };
    }
    return { seconds: null, source: "none", valid: false };
}

/** Backwards-compatible helper for callers that only need the number. */
export function resolveDurationSeconds(
    configurations: Record<string, unknown> | null | undefined,
): number | null {
    return resolveDuration(configurations).seconds;
}

/**
 * Summarize the effective published duration from the versions readback.
 *
 * Only the active `published` version is authoritative. Archived versions are
 * never treated as active, and a legacy-only or invalid published value is
 * flagged instead of being presented as worker-effective.
 */
export function summarizePublishedDuration(
    versions: WorkflowVersionLike[] | null | undefined,
    draftDuration: number | null,
    draftIsDraft: boolean,
): PublishedDurationSummary {
    if (!Array.isArray(versions)) {
        return {
            available: false,
            publishedExists: false,
            publishedDuration: null,
            publishedDurationSource: "none",
            publishedDurationValid: false,
            publishedVersionNumber: null,
            hasUnpublishedChanges: draftIsDraft,
        };
    }
    const published = versions.find(
        (version) => version && version.status === "published",
    );
    if (!published) {
        return {
            available: true,
            publishedExists: false,
            publishedDuration: null,
            publishedDurationSource: "none",
            publishedDurationValid: false,
            publishedVersionNumber: null,
            hasUnpublishedChanges: true,
        };
    }
    const resolution = resolveDuration(published.workflow_configurations ?? null);
    return {
        available: true,
        publishedExists: true,
        publishedDuration: resolution.seconds,
        publishedDurationSource: resolution.source,
        publishedDurationValid: resolution.valid,
        publishedVersionNumber: published.version_number ?? null,
        hasUnpublishedChanges:
            draftIsDraft ||
            resolution.source !== "canonical" ||
            !resolution.valid ||
            draftDuration !== resolution.seconds,
    };
}

/** Human-readable label for the effective published duration. */
export function publishedDurationLabel(
    summary: PublishedDurationSummary | null,
): string {
    if (summary === null) return "Checking…";
    if (!summary.available) return "Unavailable";
    if (!summary.publishedExists) return "Not published yet";
    if (summary.publishedDuration === null) return "Not set";
    if (!summary.publishedDurationValid) {
        return `Invalid ${summary.publishedDuration} s (not effective)`;
    }
    if (summary.publishedDurationSource !== "canonical") {
        return `Legacy ${summary.publishedDuration} s (not worker-effective)`;
    }
    return `${summary.publishedDuration} s`;
}

/**
 * Whether the form currently differs from the effective published revision.
 * Accounts for unsaved local edits in addition to a persisted draft.
 */
export function hasUnpublishedChanges(
    summary: PublishedDurationSummary | null,
    currentDuration: number | null,
): boolean | null {
    if (summary === null) return null;
    if (!summary.available) return true;
    if (!summary.publishedExists) return true;
    if (summary.hasUnpublishedChanges) return true;
    if (summary.publishedDuration === null) return true;
    return currentDuration !== summary.publishedDuration;
}
