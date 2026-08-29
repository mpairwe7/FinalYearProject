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

/**
 * Plain-language layer.
 *
 * The charts were already accurate and already accessible; what they were not
 * was *readable by the people who run the service*. A dashboard captioned
 * "Chat p95 latency", "retrieval_mode = hybrid" and "SLO 2s" answers questions
 * only an engineer knows how to ask, and the reader who most needs it — a
 * supervisor deciding whether the assistant is serving taxpayers well — cannot
 * get a single fact out of it without being told what a quantile is.
 *
 * The rule applied throughout: **name the thing in words, and say what the
 * number means.** The technical term is kept as a secondary line wherever an
 * engineer would otherwise lose the thread, never as the primary label.
 */

/** Seconds, when milliseconds are a unit nobody thinks in. */
export function plainSeconds(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} milliseconds`;
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} seconds`;
}

/** Compact form for an axis or a tile, same rounding as `plainSeconds`. */
export function shortSeconds(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
}

/**
 * The quantiles, as the sentences they actually mean.
 *
 * "p95" is not a hard idea, it is an unexplained one: nobody reading a service
 * report has to be taught "19 out of 20 answers arrive faster than this", and
 * everybody has to be taught what the 95th percentile is.
 */
export const QUANTILE_LABEL = {
  p50: "Half are faster",
  p95: "19 in 20 are faster",
  p99: "99 in 100 are faster",
} as const;

/** The technical name, kept for the reader who came looking for it. */
export const QUANTILE_TERM = { p50: "p50", p95: "p95", p99: "p99" } as const;

/**
 * How an answer was produced, in words.
 *
 * These strings are the `retrieval_mode` values the backend records. Shown
 * raw, the pie read "hybrid 62%, keyword 21%, abstained 9%" — three words a
 * taxpayer-service manager has no reason to know, describing the single most
 * useful thing on the page.
 */
export const RETRIEVAL_MODE_LABEL: Record<string, string> = {
  hybrid: "Found in URA documents",
  hybrid_corrected: "Found after a second search",
  keyword: "Found by keyword match only",
  abstained: "Declined — nothing reliable found",
  blocked: "Blocked as unsafe or out of scope",
  escalated: "Passed to a URA officer",
  clarification: "Asked the taxpayer a follow-up",
  workflow: "Guided step-by-step",
  calculator: "Calculated an amount",
  officer_reply: "Delivered an officer's reply",
  answer_override: "Used a staff-written answer",
};

export function retrievalModeLabel(mode: string): string {
  return RETRIEVAL_MODE_LABEL[mode] ?? mode.replaceAll("_", " ");
}

/**
 * The evaluation metrics, in words.
 *
 * These eight names are the RAG-evaluation literature's, and on a page whose
 * purpose is to show a non-specialist whether the assistant is trustworthy,
 * "context_precision 1.00 · abstention_precision 0.75" is a wall. Each one is
 * a plain question about the assistant's behaviour, and asking the question is
 * shorter than the term.
 */
export const EVAL_METRIC: Record<string, { label: string; short: string; meaning: string }> = {
  faithfulness: {
    label: "Sticks to the documents",
    short: "Sticks to documents",
    meaning: "How much of each answer is actually stated in the URA documents behind it.",
  },
  answer_relevancy: {
    label: "Answers the question asked",
    short: "Answers the question",
    meaning: "Whether the reply addresses what the taxpayer asked, rather than a related topic.",
  },
  context_precision: {
    label: "Finds the right documents",
    short: "Right documents",
    meaning: "How much of what the search pulled up was actually relevant.",
  },
  context_recall: {
    label: "Finds all the documents",
    short: "All documents",
    meaning: "How much of the material needed to answer was found at all.",
  },
  groundedness: {
    label: "Backs up what it says",
    short: "Backed up",
    meaning: "Whether each statement can be traced to a specific passage.",
  },
  citation_accuracy: {
    label: "Cites the right source",
    short: "Right source",
    meaning: "Whether the source an answer points at really says what the answer claims.",
  },
  safety_probe_pass_rate: {
    label: "Refuses unsafe requests",
    short: "Refuses unsafe",
    meaning: "Share of deliberate attempts to make it give harmful or illegal advice that it turned down.",
  },
  abstention_precision: {
    label: "Says when it does not know",
    short: "Admits not knowing",
    meaning: "Whether it declines for the right reason, rather than declining questions it could have answered.",
  },
};

export function evalMetricLabel(name: string): string {
  return EVAL_METRIC[name]?.label ?? name.replaceAll("_", " ");
}

/**
 * One sentence under a chart saying what the reader is looking at.
 *
 * Not a tooltip and not a `details` block: an explanation the reader has to
 * discover is an explanation for people who already know. This is always
 * visible and always short — if it needs a paragraph, the chart is wrong.
 */
export function ChartNote({ children }: { children: React.ReactNode }) {
  return <p className="ops-chart-note">{children}</p>;
}

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
