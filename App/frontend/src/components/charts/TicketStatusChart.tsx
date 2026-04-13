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

const STATUS_COLORS: Record<string, string> = {
  open: "#ef4444",
  assigned: "#f97316",
  resolved: "#22c55e",
  wontfix: "#64748b",
};

interface Props {
  stats: {
    total: number;
    open: number;
    assigned: number;
    resolved: number;
    wontfix: number;
    by_priority: Record<string, number>;
  };
}

export default function TicketStatusChart({ stats }: Props) {
  const statusData = [
    { name: "Open", value: stats.open },
    { name: "Assigned", value: stats.assigned },
    { name: "Resolved", value: stats.resolved },
    { name: "Won't fix", value: stats.wontfix },
  ];

  const priorityData = Object.entries(stats.by_priority || {}).map(([k, v]) => ({
    name: k,
    value: v,
  }));

  return (
    <div className="chart-card">
      <h4 className="chart-title">Escalation Queue ({stats.total} tickets)</h4>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={statusData} margin={{ left: 8, right: 8, top: 8, bottom: 8 }}>
          <XAxis dataKey="name" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {statusData.map((d) => (
              <Cell key={d.name} fill={STATUS_COLORS[d.name.toLowerCase().replace("'", "")] ?? "#64748b"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {priorityData.length > 0 && (
        <div className="priority-pills">
          {priorityData.map((p) => (
            <span key={p.name} className={`priority-pill priority-${p.name}`}>
              {p.name}: {p.value}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
