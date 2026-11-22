// CP-02 pure-logic checks for the Conversations/Leads client helpers.
//
// Run: `node scripts/test-conversations-api.mts` from ui/ (Node 24+ strips TS types).
// These guard the contract the server enforces: bounded, explicit filter
// serialisation and honest duration formatting. No network, DOM or React.

import assert from "node:assert/strict";

import {
  DEFAULT_FILTERS,
  filtersToBody,
  filtersToParams,
  formatDuration,
} from "../src/lib/conversationsApi.ts";

let passed = 0;
function check(name: string, fn: () => void) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

check("default filters send nothing (active, unfiltered)", () => {
  assert.equal(filtersToParams(DEFAULT_FILTERS).toString(), "");
});

check("every active filter is serialised, defaults are omitted", () => {
  const params = filtersToParams({
    ...DEFAULT_FILTERS,
    search: "  gachibowli ",
    classification: "Hot",
    call_type: "inbound",
    source: "browser",
    status: "failed",
    archived: "archived",
    date_from: "2026-11-01",
    date_to: "2026-11-16",
  });
  assert.equal(params.get("search"), "gachibowli");
  assert.equal(params.get("classification"), "Hot");
  assert.equal(params.get("call_type"), "inbound");
  assert.equal(params.get("source"), "browser");
  assert.equal(params.get("status"), "failed");
  assert.equal(params.get("archived"), "archived");
  assert.equal(params.get("date_from"), "2026-11-01");
  assert.equal(params.get("date_to"), "2026-11-16");
});

check("a one-character search is never sent (server requires 2+)", () => {
  assert.equal(filtersToParams({ ...DEFAULT_FILTERS, search: "a" }).has("search"), false);
  assert.equal(filtersToBody({ ...DEFAULT_FILTERS, search: "a" }).search, null);
});

check("select-all-matching body carries the full scope incl. classification", () => {
  const body = filtersToBody({ ...DEFAULT_FILTERS, classification: "Warm", source: "pstn", archived: "active" });
  assert.deepEqual(body, {
    search: null,
    classification: "Warm",
    call_type: null,
    source: "pstn",
    status: "all",
    archived: "active",
    date_from: null,
    date_to: null,
  });
});

check("duration formatting is honest for zero/short/long calls", () => {
  assert.equal(formatDuration(0), "—");
  assert.equal(formatDuration(-5), "—");
  assert.equal(formatDuration(49), "49s");
  assert.equal(formatDuration(225), "3m 45s");
  assert.equal(formatDuration(3605), "60m 5s");
});

console.log(`\n${passed} checks passed`);
