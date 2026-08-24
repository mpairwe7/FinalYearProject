"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { AXIS_TICK, ChartNote, ChartTable, OpsTooltip, SERIES } from "./chartTheme";

interface Props {
  data: { tag: string; count: number }[];
  title: string;
}

/**
 * What taxpayers are asking about.
 *
 * The bars used to cycle a ten-hue rainbow by row index, which spends the
 * identity channel on nothing: the colour said only "this is the fourth bar",
 * which the position already said, and the hue changed whenever the ranking
 * changed. Topics are nominal and this is one series, so every bar takes the
 * same hue and the length does the work.
 */
export default function TopicBarChart({ data, title }: Props) {
  const sorted = [...data].sort((a, b) => b.count - a.count).slice(0, 12);
  if (sorted.length === 0) return null;

  const total = sorted.reduce((sum, d) => sum + d.count, 0);
  const top = sorted[0];
  // Topic tags are stored snake_cased; a reader should not have to decode
  // "tin_registration".
  const topLabel = top.tag.replaceAll("_", " ");

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">conversations</span>
      </div>
      <ResponsiveContainer width="100%" height={Math.max(200, sorted.length * 26 + 40)}>
        <BarChart data={sorted} layout="vertical" margin={{ left: 4, right: 20, top: 4, bottom: 4 }} barCategoryGap={4}>
          <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} />
          <YAxis
            type="category"
            dataKey="tag"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={110}
            tickFormatter={(tag: string) => tag.replaceAll("_", " ")}
          />
          <Tooltip cursor={{ fill: "var(--ops-row-hover)" }} content={<OpsTooltip />} />
          <Bar
            dataKey="count"
            name="Conversations"
            fill={SERIES[0]}
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
      <ChartNote>
        Longer bars are asked about more often. <strong>{topLabel}</strong> is the most
        common, at {Math.round((top.count / total) * 100)}% of the conversations shown here.
      </ChartNote>
      <ChartTable
        caption={title}
        columns={["Topic", "Conversations"]}
        rows={sorted.map((d) => [d.tag.replaceAll("_", " "), d.count])}
      />
    </div>
  );
}
