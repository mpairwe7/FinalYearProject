"use client";

import React from "react";
import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
  Legend,
  Tooltip,
} from "recharts";

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
  context_precision: "Ctx Precision",
  context_recall: "Ctx Recall",
  groundedness: "Groundedness",
  citation_accuracy: "Citation Acc",
  safety_probe_pass_rate: "Safety",
  abstention_precision: "Abstention",
};

export default function EvalRadarChart({ metrics, title = "RAG Quality Radar" }: Props) {
  const data = metrics.map((m) => ({
    metric: LABELS[m.name] ?? m.name,
    score: Math.round(m.value * 100),
    threshold: Math.round(m.threshold * 100),
  }));

  return (
    <div className="chart-card chart-card-wide">
      <h4 className="chart-title">{title}</h4>
      <ResponsiveContainer width="100%" height={340}>
        <RadarChart data={data} cx="50%" cy="50%" outerRadius="70%">
          <PolarGrid stroke="#334155" />
          <PolarAngleAxis dataKey="metric" tick={{ fill: "#cbd5e1", fontSize: 11 }} />
          <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fill: "#64748b", fontSize: 9 }} />
          <Radar name="Score" dataKey="score" stroke="#8b5cf6" fill="#8b5cf6" fillOpacity={0.3} />
          <Radar
            name="Threshold"
            dataKey="threshold"
            stroke="#ef4444"
            strokeDasharray="4 4"
            fill="none"
          />
          <Legend wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />
          <Tooltip
            contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
