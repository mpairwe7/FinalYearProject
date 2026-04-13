"use client";

import React from "react";
import {
  RadialBarChart,
  RadialBar,
  PolarAngleAxis,
  ResponsiveContainer,
} from "recharts";

interface Props {
  label: string;
  value: number;
  target: number;
  unit?: string;
  invert?: boolean; // true = lower is better (error rate, latency)
}

export default function SloGaugeCard({ label, value, target, unit = "", invert = false }: Props) {
  const pct = invert
    ? Math.max(0, Math.min(100, ((target - value) / target) * 100 + 50))
    : Math.max(0, Math.min(100, (value / target) * 100));
  const ok = invert ? value <= target : value >= target;
  const fill = ok ? "#22c55e" : pct > 60 ? "#eab308" : "#ef4444";

  return (
    <div className="chart-card gauge-card">
      <h4 className="chart-title">{label}</h4>
      <ResponsiveContainer width="100%" height={140}>
        <RadialBarChart
          innerRadius="70%"
          outerRadius="100%"
          startAngle={180}
          endAngle={0}
          data={[{ value: pct, fill }]}
          cx="50%"
          cy="90%"
        >
          <PolarAngleAxis type="number" domain={[0, 100]} tick={false} angleAxisId={0} />
          <RadialBar
            dataKey="value"
            cornerRadius={6}
            background={{ fill: "rgba(255,255,255,0.06)" }}
            angleAxisId={0}
          />
        </RadialBarChart>
      </ResponsiveContainer>
      <div className="gauge-value" style={{ color: fill }}>
        {typeof value === "number" ? value.toFixed(value < 10 ? 2 : 0) : "—"}
        {unit}
      </div>
      <div className="gauge-target">Target: {invert ? "≤" : "≥"} {target}{unit}</div>
    </div>
  );
}
