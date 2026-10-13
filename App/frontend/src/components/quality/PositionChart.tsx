"use client";

/**
 * Helpful by position in the conversation: does the assistant hold up past its
 * first answer?
 *
 * The question the audit was asked to answer — a taxpayer likes the first
 * answer and not the follow-up — is a question about position, so this plots
 * the helpful share of every first answer, every second answer and so on. One
 * series, one hue; the bar height is the share, and each bar is labelled with
 * how many answers it rests on, because 100% of two ratings is not a finding.
 */
import React from "react";
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, ChartNote, ChartTable, OpsTooltip, SERIES } from "../charts/chartTheme";
import type { AiChatSummary } from "../../services/analyticsApi";

const POSITION_NAME: Record<string, string> = {
  "1": "First answer",
  "2": "2nd",
  "3": "3rd",
  "4": "4th",
  "5+": "5th or later",
};

function share(helpful: number, rated: number): number | null {
  return rated ? Math.round((helpful / rated) * 1000) / 10 : null;
}

/** The sentence under the chart: first answers against every follow-up. */
export function positionReading(byPosition: AiChatSummary["by_position"]): string {
  const first = byPosition.find((p) => p.position === "1");
  const later = byPosition.filter((p) => p.position !== "1");
  const laterRated = later.reduce((sum, p) => sum + p.rated, 0);
  const laterHelpful = later.reduce((sum, p) => sum + p.helpful, 0);
  const firstPct = first ? share(first.helpful, first.rated) : null;
  const laterPct = share(laterHelpful, laterRated);
  if (firstPct == null || laterPct == null) {
    return "Not enough follow-up answers have been rated to compare them with first answers yet.";
  }
  const gap = Math.round((firstPct - laterPct) * 10) / 10;
  const verdict =
    Math.abs(gap) < 5
      ? "about the same, so follow-up questions are handled as well as first ones"
      : gap > 0
        ? `${gap} points lower — follow-up questions are where answers start to fail`
        : `${-gap} points higher — follow-ups fare better than first answers`;
  return `First answers were found helpful ${firstPct}% of the time (${first?.rated} rated); follow-ups ${laterPct}% (${laterRated} rated): ${verdict}.`;
}

export default function PositionChart({ byPosition }: { byPosition: AiChatSummary["by_position"] }) {
  const rows = byPosition.map((p) => ({
    name: POSITION_NAME[p.position] ?? p.position,
    pct: p.helpful_pct ?? 0,
    rated: p.rated,
    label: p.rated ? `${p.helpful_pct ?? 0}% · ${p.rated}` : "none",
  }));
  if (!rows.some((r) => r.rated)) return null;
  const title = "Helpful by position in the conversation";

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">share of rated answers found helpful</span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={rows} margin={{ left: 0, right: 8, top: 22, bottom: 4 }} barCategoryGap="22%">
          <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={0} />
          <YAxis domain={[0, 100]} tick={AXIS_TICK} tickLine={false} axisLine={false} width={34} unit="%" />
          <Tooltip
            cursor={{ fill: "var(--ops-row-hover)" }}
            content={<OpsTooltip formatValue={(v) => `${v}%`} />}
          />
          <Bar dataKey="pct" name="Found helpful" fill={SERIES[0]} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList dataKey="label" position="top" className="qa-bar-label" />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <ChartNote>{positionReading(byPosition)}</ChartNote>
      <ChartTable
        caption={title}
        columns={["Answer", "Rated", "Found helpful"]}
        rows={rows.map((r) => [r.name, r.rated, r.rated ? `${r.pct}%` : "—"])}
      />
    </div>
  );
}
