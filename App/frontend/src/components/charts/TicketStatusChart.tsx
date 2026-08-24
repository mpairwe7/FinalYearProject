"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { AXIS_TICK, ChartNote, ChartTable, OpsTooltip, SERIES } from "./chartTheme";

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

const PRIORITY_TONE: Record<string, string> = {
  urgent: "is-danger",
  high: "is-warn",
  normal: "is-info",
  low: "",
};

/**
 * Where the period's escalations stand.
 *
 * One series — a count per status — so one hue. The previous version painted
 * open red, assigned orange and resolved green, which reads as a severity
 * scale: it implied that having open tickets is an error state rather than the
 * normal condition of a queue. The status word on the axis is the label; the
 * bar length is the number.
 */
export default function TicketStatusChart({ stats }: Props) {
  const statusData = [
    { name: "Waiting", value: stats.open },
    { name: "With an officer", value: stats.assigned },
    { name: "Answered", value: stats.resolved },
    { name: "Closed unanswered", value: stats.wontfix },
  ];

  const priorityData = Object.entries(stats.by_priority || {}).map(([k, v]) => ({
    name: k,
    value: v,
  }));

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">Questions passed to an officer</h3>
        <span className="ops-chart-sub">{stats.total} raised in this period</span>
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart data={statusData} margin={{ left: 4, right: 8, top: 8, bottom: 4 }}>
          <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} axisLine={{ stroke: "var(--ops-grid)" }} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={36} allowDecimals={false} />
          <Tooltip cursor={{ fill: "var(--ops-row-hover)" }} content={<OpsTooltip />} />
          <Bar
            dataKey="value"
            name="Escalations"
            fill={SERIES[0]}
            radius={[4, 4, 0, 0]}
            maxBarSize={64}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
      {priorityData.length > 0 && (
        <ul className="ops-legend">
          {priorityData.map((p) => (
            <li key={p.name}>
              <span className={`ops-chip ${PRIORITY_TONE[p.name] ?? ""}`}>
                {p.name}: {p.value}
              </span>
            </li>
          ))}
        </ul>
      )}
      {/* Waiting is the number an officer's manager acts on; the others are
          context for it. */}
      <ChartNote>
        {stats.open === 0
          ? "Nothing is waiting — every question raised in this period has been picked up."
          : `${stats.open} ${stats.open === 1 ? "question is" : "questions are"} waiting for an officer to pick up.`}
      </ChartNote>
      <ChartTable
        caption="Questions passed to an officer"
        columns={["Where it stands", "Questions"]}
        rows={statusData.map((d) => [d.name, d.value])}
      />
    </div>
  );
}
