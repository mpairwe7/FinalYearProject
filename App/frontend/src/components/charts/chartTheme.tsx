"use client";

import React from "react";
import { TableScroll } from "../ops/OpsPage";

/**
 * One theme for every chart on the console.
 *
 * The charts each carried their own hardcoded slate palette — `#94a3b8` axis
 * ticks, `#cbd5e1` category labels, a `#1e293b` tooltip with `#e2e8f0` text —
 * chosen for the dark canvas and never revisited when the app gained a light
 * theme. On the light dashboard those ticks measure about 2.2:1 against the
 * page and the category labels about 1.4:1, which is not a styling preference;
 * it is text nobody can read. Everything here is a CSS custom property instead,
 * so a chart follows the same Auto/Light/Dark preference as the page around it.
 *
 * SVG presentation attributes accept `var()`, so recharts needs no JS theme
 * plumbing and no re-render on a theme change — the browser resolves the
 * variable at paint.
 *
 * Colour assignment follows the four jobs:
 *  - identity between series → the categorical slots, in fixed order
 *  - ordered quantiles (p50 → p95 → p99) → the three-step ordinal ramp
 *  - a single nominal series → one hue for every bar; the bar length is
 *    already carrying the magnitude, so hue would be re-encoding it
 *  - good / bad → the reserved status colours, always with a text label
 */

export const SERIES = [
  "var(--ops-series-1)",
  "var(--ops-series-2)",
  "var(--ops-series-3)",
  "var(--ops-series-4)",
  "var(--ops-series-5)",
  "var(--ops-series-6)",
  "var(--ops-series-7)",
  "var(--ops-series-8)",
] as const;

/** Ordered magnitude: light→dark on light, dark→light on dark. */
export const ORDINAL = ["var(--ops-ord-1)", "var(--ops-ord-2)", "var(--ops-ord-3)"] as const;

export const STATUS_MARK = {
  good: "var(--ops-mark-good)",
  warning: "var(--ops-mark-warning)",
  serious: "var(--ops-mark-serious)",
  critical: "var(--ops-mark-critical)",
} as const;

export const AXIS_TICK = { fill: "var(--ops-axis-text)", fontSize: 11 } as const;
export const AXIS_TICK_SM = { fill: "var(--ops-axis-text)", fontSize: 10 } as const;
export const GRID_STROKE = "var(--ops-grid)";
export const THRESHOLD_STROKE = "var(--ops-mark-critical)";

type TooltipEntry = {
  name?: React.ReactNode;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
};

/**
 * Tooltips are HTML, not SVG, so they wear a class rather than an inline style
 * and pick up the theme the same way the rest of the console does.
 */
export function OpsTooltip({
  active,
  payload,
  label,
  unit = "",
  formatValue,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: React.ReactNode;
  unit?: string;
  formatValue?: (value: number | string, entry: TooltipEntry) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="ops-recharts-tip">
      {label != null && label !== "" ? <p className="ops-recharts-tip-label">{label}</p> : null}
      {payload.map((entry, index) => (
        <div className="ops-recharts-tip-row" key={`${entry.dataKey}-${index}`}>
          <span
            className="ops-legend-swatch"
            style={{ background: entry.color }}
            aria-hidden="true"
          />
          {entry.name ? <span>{entry.name}</span> : null}
          <strong>
            {formatValue && entry.value != null
              ? formatValue(entry.value, entry)
              : `${entry.value}${unit}`}
          </strong>
        </div>
      ))}
    </div>
  );
}

/**
 * A legend that is always present for two or more series, so identity never
 * depends on colour alone. Rendered as HTML beside the chart rather than
 * recharts' `<Legend>`, which cannot take a class and so cannot be themed.
 */
export function ChartLegend({ items }: { items: { label: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <ul className="ops-legend" aria-hidden="true">
      {items.map((item) => (
        <li className="ops-legend-item" key={item.label}>
          <span className="ops-legend-swatch" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * The table twin every chart needs: the same numbers, reachable without colour,
 * hover, or a pointing device. Collapsed by default so it costs no space.
 */
export function ChartTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: string[];
  rows: (string | number)[][];
}) {
  if (!rows.length) return null;
  return (
    <details className="ops-chart-table">
      <summary>Table view</summary>
      <TableScroll label={caption}>
        <table className="ops-table">
          <caption className="ops-sr-only">{caption}</caption>
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th key={column} scope="col" className={index === 0 ? undefined : "is-num"}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row[0])}>
                {row.map((cell, index) => (
                  <td key={index} className={index === 0 ? undefined : "is-num"}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </details>
  );
}
