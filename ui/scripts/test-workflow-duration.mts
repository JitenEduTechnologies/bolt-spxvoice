import assert from "node:assert/strict";

import {
    hasUnpublishedChanges,
    publishedDurationLabel,
    resolveDuration,
    resolveDurationSeconds,
    summarizePublishedDuration,
    type WorkflowVersionLike,
} from "../src/lib/workflow-duration.ts";

const published = (
    duration: number | null,
    version = 12,
    config?: Record<string, unknown>,
): WorkflowVersionLike => ({
    version_number: version,
    status: "published",
    published_at: "2026-01-01T00:00:00Z",
    workflow_configurations:
        config ??
        (duration === null
            ? null
            : { max_call_duration_seconds: duration, max_call_duration: duration }),
});

const draft = (duration: number): WorkflowVersionLike => ({
    version_number: 13,
    status: "draft",
    workflow_configurations: { max_call_duration_seconds: duration },
});

const archived = (duration: number, version = 10): WorkflowVersionLike => ({
    version_number: version,
    status: "archived",
    workflow_configurations: {
        max_call_duration_seconds: duration,
        max_call_duration: duration,
    },
});

// 1. Draft 180 while the active published version stays 60.
let summary = summarizePublishedDuration([draft(180), published(60)], 180, true);
assert.equal(summary.publishedDuration, 60);
assert.equal(summary.publishedDurationSource, "canonical");
assert.equal(summary.publishedDurationValid, true);
assert.equal(summary.publishedVersionNumber, 12);
assert.equal(summary.hasUnpublishedChanges, true);
assert.equal(publishedDurationLabel(summary), "60 s");

// 2. After publishing 180, the effective value becomes 180.
summary = summarizePublishedDuration([published(180, 13)], 180, false);
assert.equal(summary.publishedDuration, 180);
assert.equal(summary.hasUnpublishedChanges, false);
assert.equal(publishedDurationLabel(summary), "180 s");

// 3. Unsaved edits: current form 240 vs published 60.
summary = summarizePublishedDuration([published(60)], 60, false);
assert.equal(summary.hasUnpublishedChanges, false);
assert.equal(hasUnpublishedChanges(summary, 60), false);
assert.equal(hasUnpublishedChanges(summary, 240), true);

// 4. Archived-only versions are never presented as active.
summary = summarizePublishedDuration([archived(60), draft(180)], 180, true);
assert.equal(summary.available, true);
assert.equal(summary.publishedExists, false);
assert.equal(summary.publishedDuration, null);
assert.equal(summary.hasUnpublishedChanges, true);
assert.equal(publishedDurationLabel(summary), "Not published yet");

// 5. Legacy-only published value is flagged as not worker-effective.
summary = summarizePublishedDuration(
    [
        {
            version_number: 11,
            status: "published",
            workflow_configurations: { max_call_duration: 90 },
        },
    ],
    90,
    false,
);
assert.equal(summary.publishedDuration, 90);
assert.equal(summary.publishedDurationSource, "legacy");
assert.equal(summary.publishedDurationValid, true);
assert.equal(summary.hasUnpublishedChanges, true);
assert.equal(
    publishedDurationLabel(summary),
    "Legacy 90 s (not worker-effective)",
);

// 6. Invalid / out-of-range published values are flagged.
for (const invalid of [0, 29, 3601, "abc"]) {
    summary = summarizePublishedDuration(
        [
            {
                version_number: 12,
                status: "published",
                workflow_configurations: {
                    max_call_duration_seconds: invalid,
                },
            },
        ],
        60,
        false,
    );
    assert.equal(summary.publishedDurationValid, false);
    assert.equal(summary.hasUnpublishedChanges, true);
    assert.match(publishedDurationLabel(summary), /Invalid|Not set/);
}

// 7. Missing published version.
summary = summarizePublishedDuration([draft(180)], 180, true);
assert.equal(summary.publishedExists, false);
assert.equal(publishedDurationLabel(summary), "Not published yet");

// 8. API error / readback unavailable.
summary = summarizePublishedDuration(null, 180, true);
assert.equal(summary.available, false);
assert.equal(summary.publishedDuration, null);
assert.equal(summary.hasUnpublishedChanges, true);
assert.equal(publishedDurationLabel(summary), "Unavailable");
assert.equal(hasUnpublishedChanges(summary, 180), true);
assert.equal(hasUnpublishedChanges(null, 180), null);

// 9. Resolution rules.
assert.deepEqual(
    resolveDuration({ max_call_duration_seconds: 60, max_call_duration: 60 }),
    { seconds: 60, source: "canonical", valid: true },
);
assert.deepEqual(
    resolveDuration({ max_call_duration_seconds: "abc", max_call_duration: 60 }),
    { seconds: null, source: "canonical", valid: false },
);
assert.deepEqual(resolveDuration({ max_call_duration: 60 }), {
    seconds: 60,
    source: "legacy",
    valid: true,
});
assert.deepEqual(resolveDuration({ max_call_duration: 5 }), {
    seconds: 5,
    source: "legacy",
    valid: false,
});
assert.equal(resolveDurationSeconds(null), null);

console.log("workflow-duration: all assertions passed");
