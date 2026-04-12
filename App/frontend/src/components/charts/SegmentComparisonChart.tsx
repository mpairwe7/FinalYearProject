"use client";

import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
} from "recharts";

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

export default function SegmentComparisonChart({
  bySegment,
  metricName = "faithfulness",
  title = "Quality by Segment",
}: Props) {
  // Build comparison data from the first segment dimension
  const dims = Object.keys(bySegment);
  if (dims.length === 0) return null;

  const dimName = dims[0]; // e.g. "topic" or "locale"
  const segments = bySegment[dimName];
  let threshold = 0.7;

  const data = Object.entries(segments).map(([seg, metrics]) => {
    const m = metrics.find((m) => m.name === metricName);
    if (m) threshold = m.threshold;
    return {
      segment: seg,
      score: m ? Math.round(m.value * 100) : 0,
    };
  }).sort((a, b) => b.score - a.score);

  return (
    <div className="chart-card chart-card-wide">
      <h4 className="chart-title">{title} — {metricName} by {dimName}</h4>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
          <XAxis dataKey="segment" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
          <YAxis domain={[0, 100]} tick={{ fill: "#94a3b8", fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
            formatter={(v: number) => [`${v}%`, metricName]}
          />
          <ReferenceLine
            y={Math.round(threshold * 100)}
            stroke="#ef4444"
            strokeDasharray="4 4"
            label={{ value: `min ${Math.round(threshold * 100)}%`, fill: "#ef4444", fontSize: 10 }}
          />
          <Bar dataKey="score" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
