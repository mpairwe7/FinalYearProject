"use client";

/**
 * How rated conversations went, worst first, as one bar per pattern.
 *
 * HTML bars rather than a chart: four categories, one measure, and every row
 * is also a filter — clicking "Went wrong after a helpful answer" narrows the
 * list below to exactly those conversations.
 */
import React from "react";
import { ChartNote, patternLabel } from "../charts/chartTheme";
import type { QualityPattern } from "../../services/analyticsApi";

export const PATTERN_ORDER: QualityPattern[] = ["went_wrong", "never_helpful", "recovered", "all_helpful"];

export function PatternBreakdown({
  patterns,
  active,
  onSelect,
}: {
  patterns: Record<QualityPattern, number>;
  active?: QualityPattern | "";
  onSelect?: (pattern: QualityPattern | "") => void;
}) {
  const total = PATTERN_ORDER.reduce((sum, p) => sum + (patterns[p] ?? 0), 0);
  if (!total) return null;
  const troubled = (patterns.went_wrong ?? 0) + (patterns.never_helpful ?? 0);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">How rated conversations went</h3>
        <span className="ops-chart-sub">{total} conversations</span>
      </div>
      <ul className="qa-patterns">
        {PATTERN_ORDER.map((p) => {
          const n = patterns[p] ?? 0;
          const pct = Math.round((n / total) * 100);
          const selected = active === p;
          return (
            <li key={p}>
              <button
                type="button"
                className={`qa-pattern-row${selected ? " is-active" : ""}`}
                aria-pressed={selected}
                onClick={() => onSelect?.(selected ? "" : p)}
              >
                <span className={`qa-pattern-name is-${p}`}>{patternLabel(p)}</span>
                <span className="qa-pattern-bar" aria-hidden="true">
                  <span className={`qa-pattern-fill is-${p}`} style={{ width: `${Math.max(n ? 2 : 0, pct)}%` }} />
                </span>
                <span className="qa-pattern-count">
                  {n} <span className="ops-sr-only">conversations, </span>
                  <span className="qa-pattern-pct">{pct}%</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <ChartNote>
        {troubled
          ? `${troubled} of ${total} rated conversations ended on an unhelpful answer; ${patterns.went_wrong ?? 0} of them had started well. Choose a row to list them.`
          : `Every rated conversation ended on a helpful answer.`}
      </ChartNote>
    </div>
  );
}
