"use client";

import React from "react";

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

export default function MetricsTable({ metrics, title = "Evaluation Metrics" }: Props) {
  return (
    <div className="chart-card">
      <h4 className="chart-title">{title}</h4>
      <table className="metrics-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Score</th>
            <th>Threshold</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.name}>
              <td className="metric-name">{m.name.replace(/_/g, " ")}</td>
              <td className="metric-value">{(m.value * 100).toFixed(1)}%</td>
              <td className="metric-threshold">
                {"\u2265"} {(m.threshold * 100).toFixed(0)}%
              </td>
              <td>
                <span className={`status-badge ${m.passed ? "pass" : "fail"}`}>
                  {m.passed ? "PASS" : "FAIL"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
