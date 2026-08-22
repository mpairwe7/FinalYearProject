"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { AXIS_TICK, AXIS_TICK_SM, ChartLegend, ChartTable, ORDINAL, OpsTooltip, THRESHOLD_STROKE } from "./chartTheme";

interface Props {
  latency: Record<string, { p50: number; p95: number; p99: number; avg: number; count: number }>;
}

const SERIES = [
  { key: "p50", label: "p50 (median)", color: ORDINAL[0] },
  { key: "p95", label: "p95", color: ORDINAL[1] },
  { key: "p99", label: "p99", color: ORDINAL[2] },
];

/**
 * Endpoint latency by quantile.
 *
 * p50 → p95 → p99 is an *ordered* set, not three unrelated identities, so it
 * takes the one-hue ordinal ramp rather than the blue/violet/orange it had:
 * the reader sees the order in the colour, and the three bars stop competing
 * for attention as if they were separate things being compared.
 */
export default function LatencyChart({ latency }: Props) {
  const data = Object.entries(latency)
    .map(([path, v]) => ({
      path: path.replace(/^(GET|POST)\|/, ""),
      p50: Math.round(v.p50),
      p95: Math.round(v.p95),
      p99: Math.round(v.p99),
      count: v.count,
    }))
    .filter((d) => d.count > 0)
    .sort((a, b) => b.p95 - a.p95)
    .slice(0, 8);

  if (data.length === 0) return null;

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">Endpoint latency</h3>
        <span className="ops-chart-sub">
          milliseconds · {data.length} busiest {data.length === 1 ? "route" : "routes"}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ left: 4, right: 12, top: 8, bottom: 4 }} barGap={2}>
          <XAxis
            dataKey="path"
            tick={AXIS_TICK_SM}
            angle={-20}
            textAnchor="end"
            height={64}
            tickLine={false}
            axisLine={{ stroke: "var(--ops-grid)" }}
            interval={0}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={44}
            label={{ value: "ms", angle: 0, position: "top", offset: 12, fill: "var(--ops-axis-text)", fontSize: 10 }}
          />
          <Tooltip cursor={{ fill: "var(--ops-row-hover)" }} content={<OpsTooltip unit=" ms" />} />
          <ReferenceLine
            y={2000}
            stroke={THRESHOLD_STROKE}
            strokeDasharray="4 4"
            label={{ value: "SLO 2s", fill: THRESHOLD_STROKE, fontSize: 10, position: "insideTopRight" }}
          />
          {SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={series.color}
              radius={[4, 4, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <ChartLegend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
      <ChartTable
        caption="Endpoint latency by quantile, in milliseconds"
        columns={["Route", "p50", "p95", "p99", "Requests"]}
        rows={data.map((d) => [d.path, d.p50, d.p95, d.p99, d.count])}
      />
    </div>
  );
}
