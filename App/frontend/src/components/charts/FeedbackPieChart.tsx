"use client";

import React from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip } from "recharts";

interface Props {
  thumbsUp: number;
  thumbsDown: number;
}

export default function FeedbackPieChart({ thumbsUp, thumbsDown }: Props) {
  const data = [
    { name: "Helpful", value: thumbsUp },
    { name: "Not helpful", value: thumbsDown },
  ];
  const COLORS = ["#22c55e", "#ef4444"];
  const total = thumbsUp + thumbsDown;
  const satPct = total > 0 ? ((thumbsUp / total) * 100).toFixed(1) : "—";

  return (
    <div className="chart-card">
      <h4 className="chart-title">User Satisfaction</h4>
      <ResponsiveContainer width="100%" height={220}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={80}
            paddingAngle={3}
            dataKey="value"
            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={COLORS[i]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, color: "#94a3b8" }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="gauge-value" style={{ color: "#22c55e" }}>{satPct}%</div>
      <div className="gauge-target">{total} total ratings</div>
    </div>
  );
}
