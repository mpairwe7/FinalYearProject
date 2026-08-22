"use client";

import React from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { ChartTable, OpsTooltip, SERIES } from "./chartTheme";

interface Props {
  counters: Record<string, number>;
}

/**
 * How answers were produced — hybrid retrieval, keyword only, abstained,
 * blocked, escalated, asked for clarification.
 *
 * Part-to-whole at a glance with six segments or fewer, which is the one case a
 * donut is the right form for. Colour follows the *mode*, not its rank, so
 * filtering or a change in volume never repaints a mode the reader has already
 * learnt; the fixed slot order below is what guarantees that.
 */
const MODE_SLOT: Record<string, string> = {
  hybrid: SERIES[0],
  keyword: SERIES[1],
  abstained: SERIES[2],
  blocked: SERIES[3],
  escalated: SERIES[4],
  clarification: SERIES[5],
};

export default function RetrievalModeChart({ counters }: Props) {
  const data = Object.entries(counters)
    .filter(([k]) => k.startsWith("retrieval_mode_total"))
    .map(([k, v]) => {
      const mode = k.match(/mode="([^"]+)"/)?.[1] ?? k;
      return { name: mode, value: v, color: MODE_SLOT[mode] ?? SERIES[7] };
    })
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  if (data.length === 0) return null;
  const total = data.reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">How answers were produced</h3>
        <span className="ops-chart-sub">{total} since this replica started</span>
      </div>
      <ResponsiveContainer width="100%" height={196}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={52}
            outerRadius={84}
            paddingAngle={2}
            dataKey="value"
            stroke="var(--ops-panel)"
            strokeWidth={2}
            isAnimationActive={false}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip
            content={
              <OpsTooltip
                formatValue={(value) =>
                  `${value} · ${Math.round((Number(value) / total) * 100)}%`
                }
              />
            }
          />
        </PieChart>
      </ResponsiveContainer>
      <ul className="ops-legend">
        {data.map((d) => (
          <li className="ops-legend-item" key={d.name}>
            <span className="ops-legend-swatch" style={{ background: d.color }} />
            {d.name} <strong>{Math.round((d.value / total) * 100)}%</strong>
          </li>
        ))}
      </ul>
      <ChartTable
        caption="Retrieval mode distribution"
        columns={["Mode", "Answers", "Share"]}
        rows={data.map((d) => [d.name, d.value, `${Math.round((d.value / total) * 100)}%`])}
      />
    </div>
  );
}
