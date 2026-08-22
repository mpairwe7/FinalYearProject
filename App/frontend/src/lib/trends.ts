/**
 * Trend arithmetic for the operations overview.
 *
 * A number with no comparison is unreadable — "4 open escalations" is only
 * good or bad next to what last month looked like — but a comparison the API
 * cannot actually support is worse than none. So this file only computes what
 * the existing endpoints make exact, and the callers show nothing where they
 * do not:
 *
 * - `/v1/admin/tickets/stats?days=N` counts tickets with `created_at >= now-N`.
 *   Counting the 2N window and subtracting the N window therefore gives the
 *   previous N window exactly. That is where `previousWindow` comes from.
 * - The SLA medians are computed over one window server-side and cannot be
 *   recovered by subtraction, so no median carries a delta.
 * - Daily arrivals are bucketed from the ticket rows the page already loads.
 *   The list endpoint orders urgent-first and truncates at `limit`, so a full
 *   page is a biased sample: `dailyCounts` reports `truncated` and the caller
 *   draws nothing rather than a wrong shape.
 */

export interface Delta {
  /** Current window value. */
  value: number;
  /** Same-length window immediately before it. */
  previous: number;
  /** value − previous. */
  change: number;
  /** Signed fraction, or null when the previous window was zero. */
  ratio: number | null;
  direction: "up" | "down" | "flat";
}

/** The previous window's count, given totals over N days and 2N days. */
export function previousWindow(current?: number, doubleWindow?: number): number | undefined {
  if (current == null || doubleWindow == null) return undefined;
  return Math.max(0, doubleWindow - current);
}

export function toDelta(value?: number, previous?: number): Delta | undefined {
  if (value == null || previous == null) return undefined;
  const change = value - previous;
  return {
    value,
    previous,
    change,
    ratio: previous === 0 ? null : change / previous,
    direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
  };
}

/** "+12%" / "−3" / "no change" — percent when there is a base to divide by. */
export function formatDelta(delta: Delta): string {
  if (delta.direction === "flat") return "no change";
  const sign = delta.change > 0 ? "+" : "−";
  const magnitude = Math.abs(delta.change);
  if (delta.ratio == null) return `${sign}${magnitude}`;
  return `${sign}${Math.abs(Math.round(delta.ratio * 100))}%`;
}

export interface DailySeries {
  /** One count per day, oldest first, zero-filled. */
  points: number[];
  /** Unix-second start of each bucket, parallel to `points`. */
  starts: number[];
  /** True when the source list hit its limit, so the shape cannot be trusted. */
  truncated: boolean;
}

/**
 * Bucket `created_at` timestamps into one count per day over the last `days`.
 *
 * Buckets are anchored to the caller's `now` rather than to local midnight:
 * the console is read at any hour and a half-empty final bucket reads as a
 * cliff that is not in the data.
 */
export function dailyCounts(
  createdAt: number[],
  days: number,
  now: number,
  truncated = false,
): DailySeries {
  const buckets = Math.max(1, Math.min(days, 90));
  const span = (days * 86400) / buckets;
  const nowSeconds = now / 1000;
  const start = nowSeconds - days * 86400;
  const points = new Array<number>(buckets).fill(0);
  const starts = Array.from({ length: buckets }, (_, i) => Math.round(start + i * span));

  for (const ts of createdAt) {
    if (!Number.isFinite(ts) || ts < start || ts > nowSeconds) continue;
    const index = Math.min(buckets - 1, Math.floor((ts - start) / span));
    points[index] += 1;
  }
  return { points, starts, truncated };
}
