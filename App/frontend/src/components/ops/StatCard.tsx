"use client";

import React from "react";
import type { Delta } from "../../lib/trends";
import { formatDelta } from "../../lib/trends";
import { Sparkline } from "./Sparkline";
import { Skeleton } from "./States";

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
/**
 * Split "0 milliseconds" into the figure and its unit so the unit can be set
 * smaller.
 *
 * At full display size the unit competes with the number: "0 milliseconds"
 * wrapped to two lines at 26px while the four tiles beside it stayed on one,
 * and the whole row of labels and hints below went ragged. `.ops-gauge-unit`
 * already solves this for the gauges; this brings the tiles into line.
 *
 * The separator stays inside the unit span rather than being dropped or
 * re-added by CSS, so `textContent` is byte-identical to the string that came
 * in. The e2e suite asserts on rendered text like "3.3h", and a stray space
 * would silently break it.
 */
const MEASURE = /^([-+]?\d+(?:[.,  ]\d+)*)(\s*)([^\d\s].*)$/;

function splitMeasure(value: React.ReactNode): React.ReactNode {
  if (typeof value !== "string") return value;

  const match = MEASURE.exec(value);
  if (!match) return value;

  const [, figure, gap, unit] = match;
  // A compound figure — "1d 8h 55m" — is measurement all the way through, so
  // shrinking the tail would shrink half the number. Only a single trailing
  // unit gets stepped down.
  if (/\d/.test(unit)) return value;

  return (
    <>
      {figure}
      <span className="ops-stat-unit">
        {gap}
        {unit}
      </span>
    </>
  );
}

export function StatCard({
  label,
  value,
  hint,
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
          <strong className="ops-stat-value">{splitMeasure(value)}</strong>
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
      <a className="ops-stat" href={href}>
        {body}
      </a>
    );
  }
  return <div className="ops-stat">{body}</div>;
}
