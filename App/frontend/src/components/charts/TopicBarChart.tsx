"use client";

import React from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";

const COLORS = [
  "#8b5cf6", "#6366f1", "#3b82f6", "#06b6d4", "#14b8a6",
  "#22c55e", "#84cc16", "#eab308", "#f97316", "#ef4444",
];

interface Props {
  data: { tag: string; count: number }[];
  title: string;
}

export default function TopicBarChart({ data, title }: Props) {
  const sorted = [...data].sort((a, b) => b.count - a.count).slice(0, 12);

  return (
    <div className="chart-card">
      <h4 className="chart-title">{title}</h4>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={sorted} layout="vertical" margin={{ left: 80, right: 16, top: 8, bottom: 8 }}>
          <XAxis type="number" tick={{ fill: "#94a3b8", fontSize: 11 }} />
          <YAxis
            type="category"
            dataKey="tag"
            tick={{ fill: "#cbd5e1", fontSize: 11 }}
            width={75}
          />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
            labelStyle={{ color: "#e2e8f0" }}
            itemStyle={{ color: "#a5b4fc" }}
          />
          <Bar dataKey="count" radius={[0, 4, 4, 0]}>
            {sorted.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
