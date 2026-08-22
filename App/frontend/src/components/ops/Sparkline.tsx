"use client";

import React, { useId } from "react";

/**
 * A trend line for a stat tile — the "12-point sparkline" half of the stat-tile
 * contract, in the de-emphasis hue with the last point accented.
 *
 * Deliberately axis-less and tooltip-less: it is a shape, not a chart, and
 * every value it summarises is also readable as a number in the tile beside it
 * and in the queue below. It renders nothing at all rather than a flat line
 * when there is no variation to show, so an empty period never looks like a
 * measured zero.
 */
export function Sparkline({
  points,
  label,
  tone = "accent",
  height = 28,
}: {
  points: number[];
  /** Text alternative — the sparkline is decorative only if this is absent. */
  label?: string;
  tone?: "accent" | "muted";
  height?: number;
}) {
  const gradientId = useId();
  if (points.length < 2) return null;

  const max = Math.max(...points);
  const min = Math.min(...points);
  if (max === min && max === 0) return null;

  const width = 100;
  const range = max - min || 1;
  const step = width / (points.length - 1);
  // 1.5px of headroom top and bottom so the stroke is never clipped.
  const y = (v: number) => 1.5 + (1 - (v - min) / range) * (height - 3);
  const coords = points.map((v, i) => [i * step, y(v)] as const);

  const line = coords.map(([x, yy], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${yy.toFixed(2)}`).join(" ");
  const area = `${line} L${width} ${height} L0 ${height} Z`;
  const [lastX, lastY] = coords[coords.length - 1];
  const stroke = tone === "accent" ? "var(--ops-series-1)" : "var(--text-3)";

  return (
    <svg
      className="ops-spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path className="ops-spark-area" d={area} fill={`url(#${gradientId})`} />
      <path className="ops-spark-line" d={line} stroke={stroke} vectorEffect="non-scaling-stroke" />
      <circle className="ops-spark-dot" cx={lastX} cy={lastY} r="2.4" fill={stroke} />
    </svg>
  );
}
