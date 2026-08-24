"use client";

import React from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { ChartNote, ChartTable, OpsTooltip, SERIES, retrievalModeLabel } from "./chartTheme";

interface Props {
  counters: Record<string, number>;
}

/**
 * Where answers came from.
 *
 * Part-to-whole at a glance with six segments or fewer, which is the one case a
 * donut is the right form for. Colour follows the *mode*, not its rank, so
 * filtering or a change in volume never repaints a mode the reader has already
 * learnt; the fixed slot order below is what guarantees that.
 *
 * The segments used to be labelled with the raw `retrieval_mode` values — the
 * legend read "hybrid 62% · keyword 21% · abstained 9%". Those are the
 * database's words. This is arguably the single most useful panel on the page
 * for someone deciding whether the assistant is doing its job, and it was
 * written in a vocabulary that had to be taught first. `retrievalModeLabel`
 * turns each one into what actually happened to a taxpayer's question.
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
      return {
        mode,
        name: retrievalModeLabel(mode),
        value: v,
        color: MODE_SLOT[mode] ?? SERIES[7],
      };
    })
    .filter((d) => d.value > 0)
    .sort((a, b) => b.value - a.value);

  if (data.length === 0) return null;
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const grounded = data
    .filter((d) => d.mode.startsWith("hybrid"))
    .reduce((sum, d) => sum + d.value, 0);
  const groundedPct = Math.round((grounded / total) * 100);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">Where answers came from</h3>
        <span className="ops-chart-sub">{total} answers since the service last restarted</span>
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
      {/* The number a reader is actually here for: how much of the time the
          assistant answered from URA's own documents rather than declining,
          guessing from a keyword match, or handing the question on. */}
      <ChartNote>
        {groundedPct}% of answers were found in URA documents. The rest were either
        answered a weaker way, declined, or passed to an officer.
      </ChartNote>
      <ChartTable
        caption="Where answers came from"
        columns={["What happened", "Answers", "Share"]}
        rows={data.map((d) => [d.name, d.value, `${Math.round((d.value / total) * 100)}%`])}
      />
    </div>
  );
}
