"use client";

import React from "react";
import { ChartNote, STATUS_MARK } from "./chartTheme";

interface Props {
  /** Plain-language name of what is being measured. */
  label: string;
  value: number;
  target: number;
  unit?: string;
  invert?: boolean; // true = lower is better (error rate, latency)
  /** One sentence saying what this number means. Always shown. */
  note?: string;
  /** How to render the figure when a raw number is not what a reader wants. */
  format?: (value: number) => string;
  /** The technical name, for the engineer who came looking for it. */
  term?: string;
}

/**
 * One service level against its target — a figure, a meter, and a sentence.
 *
 * This was a half-donut gauge, which is a two-slice pie: the reader had to
 * convert an arc back into the percentage printed underneath it, and the arc
 * was not even that percentage — it encoded "how well the target is being met"
 * while the number was the measurement, so the two disagreed by design. The
 * satisfaction card in this same row had already been converted to a meter for
 * exactly this reason; this is the last gauge.
 *
 * A meter earns the space because it shows the one thing a gauge could not:
 * *where the target is*. The marker is on the track, so "we are under it" is a
 * position rather than a colour the reader has to have been taught.
 *
 * The remaining problem was language. "Chat p95 latency · ≤ 2000ms · meeting
 * target" is three pieces of jargon and a unit nobody thinks in, on a panel
 * whose whole audience is the people running a taxpayer service. Every card
 * now carries a sentence saying what its number means.
 */
function defaultFormat(value: number, unit: string): string {
  if (!Number.isFinite(value)) return "—";
  if (unit === "ms") return String(Math.round(value));
  // Keep the decimal that carries the meaning (99.9 is not 100), drop it when
  // there is nothing there.
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export default function SloGaugeCard({
  label,
  value,
  target,
  unit = "",
  invert = false,
  note,
  format,
  term,
}: Props) {
  const safeTarget = target || 1;
  const ok = invert ? value <= target : value >= target;
  // Within a tenth of the target is a warning, not a failure.
  const ratio = invert
    ? value <= 0
      ? 1
      : Math.min(1, safeTarget / value)
    : Math.min(1, Math.max(0, value / safeTarget));
  const near = ratio >= 0.9;
  const fill = ok ? STATUS_MARK.good : near ? STATUS_MARK.warning : STATUS_MARK.critical;
  const state = ok ? "On target" : near ? "Close to target" : "Below target";

  // Where the marks sit on the track.
  //
  // For a "higher is better" measure the track runs 0 → target and the reading
  // is its own position, so the target marker sits at the far end. For "lower
  // is better" there is no natural maximum, so the track runs 0 → twice the
  // target: the target lands at the midpoint, which is what makes "we are
  // comfortably inside it" and "we are just over it" look different.
  const scaleMax = invert ? safeTarget * 2 : safeTarget;
  const valuePct = Math.min(100, Math.max(0, (value / scaleMax) * 100));
  const targetPct = invert ? 50 : 100;

  const shown = format ? format(value) : defaultFormat(value, unit);
  const targetShown = format ? format(target) : `${target}${unit}`;

  return (
    <div className="ops-chart-card ops-gauge">
      <h3 className="ops-chart-title">
        {label}
        {term ? <span className="ops-chart-term"> ({term})</span> : null}
      </h3>

      <p className="ops-gauge-value ops-gauge-value-lg">
        {shown}
        {format ? null : <span className="ops-gauge-unit">{unit}</span>}
      </p>

      <div
        className="ops-meter ops-meter-tracked"
        role="img"
        aria-label={`${label}: ${shown}${format ? "" : unit}. Target ${
          invert ? "at most" : "at least"
        } ${targetShown}. ${state}.`}
      >
        <span className="ops-meter-fill" style={{ width: `${valuePct}%`, background: fill }} />
        {/* The target, drawn on the track rather than implied by a colour.
            Same marker MetricsTable already uses for a threshold. */}
        <span
          className="ops-meter-threshold"
          style={{ left: `${targetPct}%` }}
          aria-hidden="true"
        />
      </div>

      {/* State in words as well as colour — the dot is the supplement, not the
          message. */}
      <p className="ops-gauge-target">
        <span
          className={`ops-gauge-dot is-${ok ? "good" : near ? "warn" : "bad"}`}
          aria-hidden="true"
        />
        {state} · target {invert ? "at most" : "at least"} {targetShown}
      </p>

      {note ? <ChartNote>{note}</ChartNote> : null}
    </div>
  );
}
