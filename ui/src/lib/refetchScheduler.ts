/**
 * Bounded, coalescing refetch scheduling for real-time list pages (pure; no React/DOM).
 *
 * An event is only a *hint* that server state changed: it never carries authoritative data,
 * so the page always refetches. This class guarantees that
 *  - bursts of events collapse into a single pending refetch;
 *  - refetches are spaced by at least `minGapMs`;
 *  - events that arrive while the tab is hidden are remembered and trigger one refetch when
 *    the tab becomes visible again (they are never silently lost);
 *  - a reconnect always triggers a re-synchronisation, because events may have been missed.
 */
/**
 * Events that mean "server state changed, refetch". They are hints, never data. Includes the
 * lifecycle events from the call pipeline AND the events the scripted-simulation route emits
 * (`call_completed`, `lead_created`), so a simulated call also updates the lists live.
 */
export const REFRESH_EVENTS: ReadonlySet<string> = new Set([
  "conversation_started",
  "conversation_finalized",
  "conversation_analysis_completed",
  "conversation_analysis_failed",
  "recording_status_changed",
  "call_completed",
  "lead_created",
  "lead_updated",
  "lead_intelligence_updated",
  "appointment_booked",
  "conversations_archived",
  "conversations_trashed",
  "conversations_restored",
  "conversations_analyzed",
]);

export interface RefetchSchedulerOptions {
  run: () => void;
  minGapMs?: number;
  slackMs?: number;
  now?: () => number;
  isVisible?: () => boolean;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export class RefetchScheduler {
  private timer: unknown = null;
  private lastRun = 0;
  private pendingWhileHidden = false;
  private disposed = false;
  private readonly minGapMs: number;
  private readonly slackMs: number;
  private readonly now: () => number;
  private readonly isVisible: () => boolean;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (handle: unknown) => void;
  private readonly opts: RefetchSchedulerOptions;

  constructor(opts: RefetchSchedulerOptions) {
    this.opts = opts;
    this.minGapMs = opts.minGapMs ?? 1500;
    this.slackMs = opts.slackMs ?? 250;
    this.now = opts.now ?? (() => Date.now());
    this.isVisible = opts.isVisible ?? (() => true);
    this.setTimer = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  }

  /** Call whenever a *fetch actually completed* (from any trigger). */
  markFetched(): void {
    this.lastRun = this.now();
  }

  /** A relevant server event arrived. Returns true when a refetch was newly scheduled. */
  notifyEvent(): boolean {
    if (this.disposed) return false;
    if (!this.isVisible()) {
      this.pendingWhileHidden = true;
      return false;
    }
    return this.schedule();
  }

  /** The event stream reconnected: anything may have been missed, so resynchronise. */
  notifyReconnect(): boolean {
    if (this.disposed) return false;
    if (!this.isVisible()) {
      this.pendingWhileHidden = true;
      return false;
    }
    return this.schedule();
  }

  /** The tab became visible: catch up on anything that happened while it was hidden. */
  notifyVisible(): boolean {
    if (this.disposed || !this.pendingWhileHidden || !this.isVisible()) return false;
    this.pendingWhileHidden = false;
    return this.schedule();
  }

  get hasPending(): boolean {
    return this.timer !== null;
  }

  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
  }

  private schedule(): boolean {
    if (this.timer !== null) return false; // coalesce: one pending refetch at a time
    const wait = Math.max(0, this.minGapMs - (this.now() - this.lastRun)) + this.slackMs;
    this.timer = this.setTimer(() => {
      this.timer = null;
      if (this.disposed) return;
      this.lastRun = this.now();
      this.opts.run();
    }, wait);
    return true;
  }
}
