// CP-02 real-time behaviour checks (pure logic; no browser, React or network).
//
// Run: `node scripts/test-refetch-scheduler.mts` from ui/ (Node 24+ strips TS types).
// Proves the properties the dashboards rely on: duplicate/bursty events coalesce, refetches are
// spaced, hidden-tab events are not lost, and a reconnect always resynchronises.

import assert from "node:assert/strict";

import { REFRESH_EVENTS, RefetchScheduler } from "../src/lib/refetchScheduler.ts";

let passed = 0;
function check(name: string, fn: () => void) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function harness(opts: { visible?: boolean } = {}) {
  let t = 10_000;
  let visible = opts.visible ?? true;
  const timers: { at: number; fn: () => void; id: number; live: boolean }[] = [];
  let runs = 0;
  const scheduler = new RefetchScheduler({
    run: () => {
      runs += 1;
    },
    now: () => t,
    isVisible: () => visible,
    setTimer: (fn, ms) => {
      const timer = { at: t + ms, fn, id: timers.length, live: true };
      timers.push(timer);
      return timer.id;
    },
    clearTimer: (id) => {
      const timer = timers[id as number];
      if (timer) timer.live = false;
    },
  });
  return {
    scheduler,
    get runs() {
      return runs;
    },
    setVisible(v: boolean) {
      visible = v;
    },
    advance(ms: number) {
      t += ms;
      for (const timer of timers) {
        if (timer.live && timer.at <= t) {
          timer.live = false;
          timer.fn();
        }
      }
    },
  };
}

check("a burst of duplicate events produces exactly one refetch", () => {
  const h = harness();
  const scheduled = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(() => h.scheduler.notifyEvent());
  assert.equal(scheduled.filter(Boolean).length, 1);
  assert.equal(h.runs, 0);
  h.advance(5_000);
  assert.equal(h.runs, 1);
});

check("refetches are spaced by the minimum gap", () => {
  const h = harness();
  h.scheduler.markFetched();
  h.scheduler.notifyEvent();
  h.advance(1_000); // inside the gap: nothing yet
  assert.equal(h.runs, 0);
  h.advance(1_000);
  assert.equal(h.runs, 1);
  h.scheduler.notifyEvent();
  h.advance(100);
  assert.equal(h.runs, 1);
  h.advance(2_000);
  assert.equal(h.runs, 2);
});

check("events received while the tab is hidden are remembered, not lost", () => {
  const h = harness({ visible: false });
  assert.equal(h.scheduler.notifyEvent(), false);
  assert.equal(h.scheduler.notifyEvent(), false);
  h.advance(10_000);
  assert.equal(h.runs, 0); // nothing fetched while hidden
  h.setVisible(true);
  assert.equal(h.scheduler.notifyVisible(), true);
  h.advance(5_000);
  assert.equal(h.runs, 1); // one catch-up refetch on return
  assert.equal(h.scheduler.notifyVisible(), false); // and nothing further
});

check("becoming visible with nothing missed does not refetch", () => {
  const h = harness();
  assert.equal(h.scheduler.notifyVisible(), false);
  h.advance(10_000);
  assert.equal(h.runs, 0);
});

check("a reconnect always triggers a resynchronisation", () => {
  const h = harness();
  assert.equal(h.scheduler.notifyReconnect(), true);
  h.advance(5_000);
  assert.equal(h.runs, 1);
  const hidden = harness({ visible: false });
  hidden.scheduler.notifyReconnect();
  hidden.setVisible(true);
  hidden.scheduler.notifyVisible();
  hidden.advance(5_000);
  assert.equal(hidden.runs, 1); // reconnect while hidden still resyncs once visible
});

check("a pending refetch is cancelled on dispose and nothing runs afterwards", () => {
  const h = harness();
  h.scheduler.notifyEvent();
  assert.equal(h.scheduler.hasPending, true);
  h.scheduler.dispose();
  h.advance(10_000);
  assert.equal(h.runs, 0);
  assert.equal(h.scheduler.notifyEvent(), false);
});

check("manual fetches count toward the gap so event storms cannot starve the UI", () => {
  const h = harness();
  h.scheduler.markFetched();
  h.scheduler.notifyEvent();
  h.advance(1_700); // 1500 gap + 250 slack not yet elapsed from last fetch
  assert.equal(h.runs, 0);
  h.advance(100);
  assert.equal(h.runs, 1);
});

check("lists refresh on pipeline events AND on scripted-simulation events, ignore unrelated ones", () => {
  for (const name of [
    "conversation_started", "conversation_finalized", "conversation_analysis_completed", "conversation_analysis_failed",
    "recording_status_changed", "conversations_archived", "conversations_trashed", "conversations_restored",
    "conversations_analyzed", "lead_updated", "call_completed", "lead_created",
  ]) {
    assert.ok(REFRESH_EVENTS.has(name), `${name} should refresh the lists`);
  }
  for (const name of ["owner_action_acknowledged", "objection_draft_created", "campaign_progress", ""]) {
    assert.ok(!REFRESH_EVENTS.has(name), `${name} should not refresh the lists`);
  }
});

console.log(`\n${passed} checks passed`);
