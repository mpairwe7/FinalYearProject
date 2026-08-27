"use client";

import React from "react";
import { TableScroll } from "../ops/OpsPage";
import { EVAL_METRIC } from "./chartTheme";

interface Metric {
  name: string;
  value: number;
  threshold: number;
  passed: boolean;
}

interface Props {
  metrics: Metric[];
  title?: string;
  /** Drop the card and sit the table directly on the page — see
   *  `.ops-chart-card.is-bare` and OpsPanel's `bare`. */
  bare?: boolean;
}

/**
 * Every check with its threshold and a bar showing the gap.
 *
 * "0.77 against 0.6" is arithmetic the reader should not have to do in their
 * head twice per row; the meter does it, and the PASS/FAIL chip names the
 * outcome in words so the colour is never carrying it alone.
 *
 * The row label was the raw metric name with its underscores swapped for
 * spaces — "abstention precision", "context recall". That is the term, not the
 * meaning, and this table is the one place on the page with room to give both:
 * the plain name leads and the sentence explaining it sits underneath, so
 * nobody has to already know what was measured to read whether it passed.
 */
export default function MetricsTable({ metrics, title = "Evaluation metrics", bare }: Props) {
  return (
    <div className={`ops-chart-card${bare ? " is-bare" : ""}`}>
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">{metrics.length} measured</span>
      </div>
      <TableScroll label={title}>
        <table className="ops-table ops-metrics-table">
          <thead>
            <tr>
              <th scope="col">What was checked</th>
              <th scope="col">Against the minimum</th>
              <th scope="col" className="is-num">
                Scored
              </th>
              <th scope="col" className="is-num">
                Needs
              </th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.name}>
                <td className="ops-metric-name">
                  {EVAL_METRIC[m.name]?.label ?? m.name.replace(/_/g, " ")}
                  {EVAL_METRIC[m.name] ? (
                    <span className="ops-metric-meaning">{EVAL_METRIC[m.name].meaning}</span>
                  ) : null}
                </td>
                <td>
                  <span className="ops-meter ops-meter-sm" aria-hidden="true">
                    <span
                      className={`ops-meter-fill is-${m.passed ? "good" : "bad"}`}
                      style={{ width: `${Math.min(100, Math.max(0, m.value * 100))}%` }}
                    />
                    <span
                      className="ops-meter-threshold"
                      style={{ left: `${Math.min(100, Math.max(0, m.threshold * 100))}%` }}
                    />
                  </span>
                </td>
                <td className="is-num">{(m.value * 100).toFixed(1)}%</td>
                <td className="is-num ops-metric-threshold">≥ {(m.threshold * 100).toFixed(0)}%</td>
                <td>
                  <span className={`ops-chip ${m.passed ? "is-good" : "is-danger"}`}>
                    {m.passed ? "Pass" : "Fail"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}
