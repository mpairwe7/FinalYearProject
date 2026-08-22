"use client";

import React from "react";
import type { Delta } from "../../lib/trends";
import { formatDelta } from "../../lib/trends";
import { Sparkline } from "./Sparkline";
import { Skeleton } from "./States";

export type StatTone = "neutral" | "good" | "warn" | "danger";

/**
 * The console's KPI tile.
 *
 * The overview used to show six flat numbers with no comparison and no target,
 * which makes each one unreadable: "3 awaiting first response" is either a
 * quiet morning or a crisis and the card said neither. A tile here can carry a
 * delta against a named period, a target it is measured against, and a trend
 * shape — and shows each only when the data behind it is real. `delta` is
 * computed by lib/trends.ts, which refuses to derive one the API cannot
 * support.
 *
 * Whether a rise is good news depends on the metric: more resolved is good,
 * more breaching is not, so the direction→colour mapping is the caller's to
 * state via `higherIsBetter`.
 */
export function StatCard({
  label,
  value,
  hint,
  tone = "neutral",
  href,
  delta,
  deltaPeriod,
  higherIsBetter,
  trend,
  trendLabel,
  loading,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: StatTone;
  href?: string;
  delta?: Delta;
  /** Named comparison window, e.g. "previous 30 days". */
  deltaPeriod?: string;
  /** Omit for a metric where neither direction is inherently good. */
  higherIsBetter?: boolean;
  trend?: number[];
  trendLabel?: string;
  loading?: boolean;
}) {
  const toneClass = tone === "neutral" ? "" : ` is-${tone}`;

  let deltaClass = "";
  if (delta && delta.direction !== "flat" && higherIsBetter !== undefined) {
    const isGood = delta.direction === "up" ? higherIsBetter : !higherIsBetter;
    deltaClass = isGood ? " is-good" : " is-bad";
  }

  const body = (
    <>
      <span className="ops-stat-label">{label}</span>
      {loading ? (
        <Skeleton width="45%" height={26} />
      ) : (
        <span className="ops-stat-row">
          <strong className="ops-stat-value">{value}</strong>
          {delta ? (
            <span className={`ops-delta${deltaClass}`}>
              <span className="ops-delta-glyph" aria-hidden="true">
                {delta.direction === "up" ? "▲" : delta.direction === "down" ? "▼" : "→"}
              </span>
              {formatDelta(delta)}
              {deltaPeriod ? <span className="ops-sr-only"> versus the {deltaPeriod}</span> : null}
            </span>
          ) : null}
        </span>
      )}
      {hint ? <span className="ops-stat-hint">{hint}</span> : null}
      {delta && deltaPeriod ? (
        <span className="ops-stat-hint" aria-hidden="true">
          vs {deltaPeriod} ({delta.previous})
        </span>
      ) : null}
      {trend && trend.length > 1 ? <Sparkline points={trend} label={trendLabel} /> : null}
    </>
  );

  if (href) {
    return (
      <a className={`ops-stat${toneClass}`} href={href}>
        {body}
      </a>
    );
  }
  return <div className={`ops-stat${toneClass}`}>{body}</div>;
}
