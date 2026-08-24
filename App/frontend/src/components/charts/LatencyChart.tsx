"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import {
  AXIS_TICK,
  AXIS_TICK_SM,
  ChartLegend,
  ChartNote,
  ChartTable,
  ORDINAL,
  OpsTooltip,
  QUANTILE_LABEL,
  THRESHOLD_STROKE,
  plainSeconds,
  shortSeconds,
} from "./chartTheme";

interface Props {
  latency: Record<string, { p50: number; p95: number; p99: number; avg: number; count: number }>;
}

/**
 * Plain-language names for the endpoints a reader might care about.
 *
 * The axis said `/v1/chat`, `/v1/tts`, `/v1/documents/analyze` — URL paths,
 * which are the names the code uses and not the names anything the service
 * DOES has. A supervisor asking "are answers slow?" could not find the row
 * that answers it.
 *
 * Anything unlisted keeps its path: inventing a friendly name for a route
 * nobody has described would be worse than showing the true one.
 */
const ROUTE_LABEL: Record<string, string> = {
  "/v1/chat": "Answering a question",
  "/v1/chat/stream": "Answering a question (live)",
  "/v1/asr": "Understanding speech",
  "/v1/tts": "Speaking an answer",
  "/v1/translate": "Translating",
  "/v1/voice/chat": "Answering a spoken question",
  "/v1/documents/analyze": "Reading an uploaded document",
  "/v1/escalate": "Passing a question to an officer",
  "/v1/feedback": "Recording feedback",
  "/v1/me": "Loading an account",
};

const SERIES = [
  { key: "p50", label: QUANTILE_LABEL.p50, term: "p50", color: ORDINAL[0] },
  { key: "p95", label: QUANTILE_LABEL.p95, term: "p95", color: ORDINAL[1] },
  { key: "p99", label: QUANTILE_LABEL.p99, term: "p99", color: ORDINAL[2] },
];

/** The service-level target the reference line marks, in milliseconds. */
const TARGET_MS = 2000;

/**
 * How long the assistant takes to do each thing.
 *
 * Two problems, and the second one was invisible to whoever wrote it.
 *
 * The encoding was already right: p50 → p95 → p99 is an *ordered* set, so it
 * takes the one-hue ordinal ramp rather than three competing identities.
 *
 * The language was not. "Endpoint latency", "p50 (median)", "p95", "p99",
 * "SLO 2s", an axis of URL paths and a unit of milliseconds — that is six
 * pieces of vocabulary between the reader and one fact, on a panel whose
 * audience is the people running a taxpayer service rather than the people who
 * built it. Every one of them has a plain equivalent that is not longer:
 * "19 in 20 are faster" is what p95 means, and needs no glossary.
 */
export default function LatencyChart({ latency }: Props) {
  const data = Object.entries(latency)
    .map(([path, v]) => {
      const route = path.replace(/^(GET|POST)\|/, "");
      return {
        path: ROUTE_LABEL[route] ?? route,
        route,
        p50: Math.round(v.p50),
        p95: Math.round(v.p95),
        p99: Math.round(v.p99),
        count: v.count,
      };
    })
    .filter((d) => d.count > 0)
    .sort((a, b) => b.p95 - a.p95)
    .slice(0, 8);

  if (data.length === 0) return null;

  const slowest = data[0];

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">How long each thing takes</h3>
        <span className="ops-chart-sub">
          {data.length} busiest {data.length === 1 ? "task" : "tasks"}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} margin={{ left: 4, right: 12, top: 8, bottom: 4 }} barGap={2}>
          <XAxis
            dataKey="path"
            tick={AXIS_TICK_SM}
            angle={-20}
            textAnchor="end"
            height={72}
            tickLine={false}
            axisLine={{ stroke: "var(--ops-grid)" }}
            interval={0}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={(value: number) => shortSeconds(value)}
          />
          <Tooltip
            cursor={{ fill: "var(--ops-row-hover)" }}
            content={<OpsTooltip formatValue={(value) => plainSeconds(Number(value))} />}
          />
          <ReferenceLine
            y={TARGET_MS}
            stroke={THRESHOLD_STROKE}
            strokeDasharray="4 4"
            label={{
              value: "Target: 2 seconds",
              fill: THRESHOLD_STROKE,
              fontSize: 10,
              position: "insideTopRight",
            }}
          />
          {SERIES.map((series) => (
            <Bar
              key={series.key}
              dataKey={series.key}
              name={series.label}
              fill={series.color}
              radius={[4, 4, 0, 0]}
              maxBarSize={24}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <ChartLegend items={SERIES.map((s) => ({ label: s.label, color: s.color }))} />
      {/* The reading, not a definition of the axis. A note that says "this
          chart shows latency percentiles" tells the reader nothing they could
          not see; naming the slowest task and whether it clears the target is
          the fact they came for. */}
      <ChartNote>
        The slowest of these is <strong>{slowest.path}</strong> — 19 in 20 finish within{" "}
        {plainSeconds(slowest.p95)}, against a target of 2 seconds. Taller bars are slower.
      </ChartNote>
      <ChartTable
        caption="How long each task takes, and how often it ran"
        columns={[
          "Task",
          `${QUANTILE_LABEL.p50} (p50)`,
          `${QUANTILE_LABEL.p95} (p95)`,
          `${QUANTILE_LABEL.p99} (p99)`,
          "Times run",
        ]}
        rows={data.map((d) => [
          d.path,
          shortSeconds(d.p50),
          shortSeconds(d.p95),
          shortSeconds(d.p99),
          d.count,
        ])}
      />
    </div>
  );
}
