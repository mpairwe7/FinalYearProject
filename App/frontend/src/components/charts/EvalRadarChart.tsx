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
import { AXIS_TICK, AXIS_TICK_SM, ChartLegend, GRID_STROKE, OpsTooltip, SERIES, THRESHOLD_STROKE } from "./chartTheme";

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

const LABELS: Record<string, string> = {
  faithfulness: "Faithfulness",
  answer_relevancy: "Relevancy",
  context_precision: "Ctx precision",
  context_recall: "Ctx recall",
  groundedness: "Groundedness",
  citation_accuracy: "Citation accuracy",
  safety_probe_pass_rate: "Safety",
  abstention_precision: "Abstention",
};

/** Score against threshold across the eight RAG metrics. */
export default function EvalRadarChart({ metrics, title = "Quality against thresholds" }: Props) {
  const data = metrics.map((m) => ({
    metric: LABELS[m.name] ?? m.name,
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
            name="Score"
            dataKey="score"
            stroke={SERIES[0]}
            fill={SERIES[0]}
            fillOpacity={0.28}
            isAnimationActive={false}
          />
          <Radar
            name="Threshold"
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
          { label: "Score", color: SERIES[0] },
          { label: "Threshold", color: THRESHOLD_STROKE },
        ]}
      />
    </div>
  );
}
