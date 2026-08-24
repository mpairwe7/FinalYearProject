"use client";

import React from "react";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import {
  AXIS_TICK,
  AXIS_TICK_SM,
  ChartLegend,
  ChartNote,
  EVAL_METRIC,
  GRID_STROKE,
  OpsTooltip,
  SERIES,
  THRESHOLD_STROKE,
} from "./chartTheme";

interface EvalMetric {
  name: string;
  value: number;
  threshold: number;
  passed: boolean;
}

interface Props {
  metrics: EvalMetric[];
  title?: string;
}

/**
 * Score against the release threshold, across the eight quality checks.
 *
 * The axis labels used to be the metric names shortened — "Ctx precision",
 * "Abstention", "Groundedness". Abbreviating a term the reader does not know
 * does not help them; it only makes the wall narrower. The shared EVAL_METRIC
 * table names each check as the question it asks about the assistant's
 * behaviour, and the `short` form is what fits on a radar spoke.
 */
export default function EvalRadarChart({ metrics, title = "Quality against thresholds" }: Props) {
  const data = metrics.map((m) => ({
    metric: EVAL_METRIC[m.name]?.short ?? m.name.replaceAll("_", " "),
    score: Math.round(m.value * 100),
    threshold: Math.round(m.threshold * 100),
  }));

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">percent</span>
      </div>
      <ResponsiveContainer width="100%" height={340}>
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
          <PolarGrid stroke={GRID_STROKE} />
          <PolarAngleAxis dataKey="metric" tick={AXIS_TICK} />
          <PolarRadiusAxis angle={30} domain={[0, 100]} tick={AXIS_TICK_SM} />
          <Radar
            name="Measured"
            dataKey="score"
            stroke={SERIES[0]}
            fill={SERIES[0]}
            fillOpacity={0.28}
            isAnimationActive={false}
          />
          <Radar
            name="Minimum required"
            dataKey="threshold"
            stroke={THRESHOLD_STROKE}
            strokeDasharray="4 4"
            fill="none"
            isAnimationActive={false}
          />
          <Tooltip content={<OpsTooltip unit="%" />} />
        </RadarChart>
      </ResponsiveContainer>
      <ChartLegend
        items={[
          { label: "Measured", color: SERIES[0] },
          { label: "Minimum required", color: THRESHOLD_STROKE },
        ]}
      />
      <ChartNote>
        The filled shape is how the assistant scored; the dashed outline is the minimum each
        check has to clear before a release is allowed. Wherever the filled shape reaches past
        the outline, that check passed.
      </ChartNote>
    </div>
  );
}
