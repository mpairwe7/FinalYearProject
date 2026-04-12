"use client";

import React from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from "recharts";

const MODE_COLORS: Record<string, string> = {
  hybrid: "#8b5cf6",
  keyword: "#3b82f6",
  abstained: "#eab308",
  blocked: "#ef4444",
  escalated: "#f97316",
  clarification: "#06b6d4",
};

interface Props {
  counters: Record<string, number>;
}

export default function RetrievalModeChart({ counters }: Props) {
  const data = Object.entries(counters)
    .filter(([k]) => k.startsWith("retrieval_mode_total"))
    .map(([k, v]) => {
      const mode = k.match(/mode="([^"]+)"/)?.[1] ?? k;
      return { name: mode, value: v };
    })
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  if (data.length === 0) return null;

  return (
    <div className="chart-card">
      <h4 className="chart-title">Retrieval Mode Distribution</h4>
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={45}
            outerRadius={85}
            paddingAngle={2}
            dataKey="value"
          >
            {data.map((d, i) => (
              <Cell key={i} fill={MODE_COLORS[d.name] ?? "#64748b"} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
