"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { AXIS_TICK, ChartTable, OpsTooltip, SERIES, THRESHOLD_STROKE } from "./chartTheme";

interface EvalMetric {
  name: string;
  value: number;
  threshold: number;
  passed: boolean;
}

interface Props {
  bySegment: Record<string, Record<string, EvalMetric[]>>;
  metricName?: string;
  title?: string;
}

/**
 * One metric across segments — the fairness read.
 *
 * Segments are nominal, so one hue for every bar; the threshold line is what
 * the eye is meant to travel along, and it is the only mark that gets a
 * contrasting colour.
 */
export default function SegmentComparisonChart({
  bySegment,
  metricName = "faithfulness",
  title = "Quality by segment",
}: Props) {
  const dims = Object.keys(bySegment);
  if (dims.length === 0) return null;

  const dimName = dims[0];
  const segments = bySegment[dimName];
  let threshold = 0.7;

  const data = Object.entries(segments)
    .map(([seg, metrics]) => {
      const m = metrics.find((metric) => metric.name === metricName);
      if (m) threshold = m.threshold;
      return { segment: seg, score: m ? Math.round(m.value * 100) : 0 };
    })
    .sort((a, b) => b.score - a.score);

  const min = Math.round(threshold * 100);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">
          {metricName.replace(/_/g, " ")} by {dimName} · percent
        </span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ left: 4, right: 16, top: 8, bottom: 4 }}>
          <XAxis dataKey="segment" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "var(--ops-grid)" }} />
          <YAxis domain={[0, 100]} tick={AXIS_TICK} tickLine={false} axisLine={false} width={40} />
          <Tooltip cursor={{ fill: "var(--ops-row-hover)" }} content={<OpsTooltip unit="%" />} />
          <ReferenceLine
            y={min}
            stroke={THRESHOLD_STROKE}
            strokeDasharray="4 4"
            label={{ value: `min ${min}%`, fill: THRESHOLD_STROKE, fontSize: 10, position: "insideTopRight" }}
          />
          <Bar
            dataKey="score"
            name={metricName.replace(/_/g, " ")}
            fill={SERIES[0]}
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
      <ChartTable
        caption={`${metricName} by ${dimName}`}
        columns={[dimName, "Score", "Minimum"]}
        rows={data.map((d) => [d.segment, `${d.score}%`, `${min}%`])}
      />
    </div>
  );
}
