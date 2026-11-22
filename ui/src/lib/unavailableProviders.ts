// Providers the API lists but this deployment cannot run (see
// api/services/configuration/provider_availability.py). Display-only: they are
// never part of the selectable schema sets.

export interface UnavailableProvider {
    id: string;
    state: string;
    reason_code: string;
    message: string;
}

const FALLBACK_MESSAGE = "Not available in this deployment.";

/** Normalise the API's `unavailable` map. Unknown shapes yield nothing, never a throw. */
export function parseUnavailable(raw: unknown): Record<string, UnavailableProvider[]> {
    const out: Record<string, UnavailableProvider[]> = {};
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
    for (const [service, items] of Object.entries(raw as Record<string, unknown>)) {
        if (!Array.isArray(items)) continue;
        out[service] = items
            .filter((i): i is Record<string, unknown> => !!i && typeof i === "object")
            .map((i) => ({
                id: typeof i.id === "string" ? i.id : "",
                state: typeof i.state === "string" ? i.state : "unavailable",
                reason_code: typeof i.reason_code === "string" ? i.reason_code : "",
                message:
                    typeof i.message === "string" && i.message.trim()
                        ? i.message
                        : FALLBACK_MESSAGE,
            }))
            .filter((i) => i.id.length > 0);
    }
    return out;
}

/** Providers to show as unavailable for one service: never anything selectable. */
export function unavailableForService(
    all: Record<string, UnavailableProvider[]>,
    service: string,
    selectable: string[],
): UnavailableProvider[] {
    const selectableSet = new Set(selectable);
    return (all[service] ?? []).filter((u) => !selectableSet.has(u.id));
}
