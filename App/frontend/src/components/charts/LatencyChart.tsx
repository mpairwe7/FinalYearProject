"use client";

import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Cell,
} from "recharts";

interface Props {
  latency: Record<string, { p50: number; p95: number; p99: number; avg: number; count: number }>;
}

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

  return (
    <div className="chart-card chart-card-wide">
      <h4 className="chart-title">Endpoint Latency (ms)</h4>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ left: 8, right: 16, top: 8, bottom: 8 }}>
          <XAxis
            dataKey="path"
            tick={{ fill: "#94a3b8", fontSize: 10 }}
            angle={-20}
            textAnchor="end"
            height={60}
          />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
            labelStyle={{ color: "#e2e8f0" }}
          />
          <ReferenceLine y={2000} stroke="#ef4444" strokeDasharray="4 4" label={{ value: "SLO 2s", fill: "#ef4444", fontSize: 10 }} />
          <Bar dataKey="p50" name="p50" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          <Bar dataKey="p95" name="p95" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
          <Bar dataKey="p99" name="p99" fill="#f97316" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
