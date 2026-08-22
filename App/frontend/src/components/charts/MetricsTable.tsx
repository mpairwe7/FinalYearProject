"use client";

import React from "react";
import { TableScroll } from "../ops/OpsPage";

interface Metric {
  name: string;
  value: number;
  threshold: number;
  passed: boolean;
}

interface Props {
  metrics: Metric[];
  title?: string;
}

/**
 * Every metric with its threshold and a bar showing the gap.
 *
 * "0.77 against 0.6" is arithmetic the reader should not have to do in their
 * head twice per row; the meter does it, and the PASS/FAIL chip names the
 * outcome in words so the colour is never carrying it alone.
 */
export default function MetricsTable({ metrics, title = "Evaluation metrics" }: Props) {
  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">{metrics.length} measured</span>
      </div>
      <TableScroll label={title}>
        <table className="ops-table ops-metrics-table">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">Against threshold</th>
              <th scope="col" className="is-num">
                Score
              </th>
              <th scope="col" className="is-num">
                Min
              </th>
              <th scope="col">Result</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.name}>
                <td className="ops-metric-name">{m.name.replace(/_/g, " ")}</td>
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
