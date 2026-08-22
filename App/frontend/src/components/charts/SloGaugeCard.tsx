"use client";

import React from "react";
import { RadialBarChart, RadialBar, PolarAngleAxis, ResponsiveContainer } from "recharts";
import { STATUS_MARK } from "./chartTheme";

interface Props {
  label: string;
  value: number;
  target: number;
  unit?: string;
  invert?: boolean; // true = lower is better (error rate, latency)
  /** Where the number comes from, when that is not obvious. */
  note?: string;
}

/**
 * One service level against its target.
 *
 * Two things were wrong with the previous version and both changed what the
 * reader saw rather than how it looked:
 *
 * 1. `value.toFixed(value < 10 ? 2 : 0)` rendered an availability of 99.9 as
 *    "100" — the one digit that mattered was the one being rounded away.
 * 2. The ring for a "lower is better" metric was `((target - value) / target)
 *    * 100 + 50`, so a latency exactly on target drew a half-full ring while
 *    the label underneath said it was passing, and a latency of zero drew a
 *    ring past full. The arc now reads as "how well the target is being met":
 *    full at or better than target, falling away as it is missed, in both
 *    directions.
 */
function formatValue(value: number, unit: string): string {
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
}: Props) {
  const safeTarget = target || 1;
  const ratio = invert
    ? value <= 0
      ? 1
      : Math.min(1, safeTarget / value)
    : Math.min(1, Math.max(0, value / safeTarget));
  const pct = ratio * 100;
  const ok = invert ? value <= target : value >= target;
  // Within a tenth of the target is a warning, not a failure.
  const near = ratio >= 0.9;
  const fill = ok ? STATUS_MARK.good : near ? STATUS_MARK.warning : STATUS_MARK.critical;
  const state = ok ? "meeting target" : near ? "close to target" : "below target";

  return (
    <div className="ops-chart-card ops-gauge">
      <h3 className="ops-chart-title">{label}</h3>
      <ResponsiveContainer width="100%" height={116}>
        <RadialBarChart
          innerRadius="72%"
          outerRadius="100%"
          startAngle={180}
          endAngle={0}
          data={[{ value: pct, fill }]}
          cx="50%"
          cy="92%"
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} angleAxisId={0} />
          <RadialBar
            dataKey="value"
            cornerRadius={5}
            background={{ fill: "var(--ops-track)" }}
            angleAxisId={0}
            isAnimationActive={false}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      {/* The figure wears a text token; the coloured arc beside it carries the
          state, and the words below say it too. */}
      <p className="ops-gauge-value">
        {formatValue(value, unit)}
        <span className="ops-gauge-unit">{unit}</span>
      </p>
      <p className="ops-gauge-target">
        <span className={`ops-gauge-dot is-${ok ? "good" : near ? "warn" : "bad"}`} aria-hidden="true" />
        {state} · {invert ? "≤" : "≥"} {target}
        {unit}
      </p>
      {note ? <p className="ops-gauge-note">{note}</p> : null}
    </div>
  );
}
