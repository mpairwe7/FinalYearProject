"use client";

/**
 * Evaluation dashboard — RAG quality visualization.
 *
 * Shows: radar (8 metrics), metrics table with pass/fail, per-segment
 * comparison bars, and the classifier confusion matrix.
 *
 * Every one of those is named on screen by what it asks about the assistant's
 * behaviour rather than by its term — "Sticks to the documents", not
 * "faithfulness"; "Sorting questions into the right topic", not "confusion
 * matrix". The audience for evaluation evidence is the people accountable for
 * the service, not the people who chose the metrics, and a page they cannot
 * read is not evidence. The mapping lives in components/charts/chartTheme's
 * EVAL_METRIC table so the radar, the table and any future panel say the same
 * thing.
 *
 * Standards: ISO 25010:2023 §2 (Functional Suitability),
 * NIST AI RMF MEASURE 2.6 (evaluation evidence)
 *
 * The page ships static fallback data so it renders without a backend, and
 * `/v1/evaluate` is gated behind the operator key — so in an ordinary staff
 * session the fallback is what you are looking at, essentially always. It used
 * to say nothing about that: eight metrics, all green, an "ALL GATES PASS"
 * badge, and no way to tell measured from illustrative. On a page whose purpose
 * is evaluation *evidence*, that is the one thing it has to get right, so the
 * provenance is now stated at the top and the badge only claims a result when
 * there is a live run behind it.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import StaffGuard from "../../../components/StaffGuard";
import { OpsPage } from "../../../components/ops/OpsPage";
import EvalRadarChart from "../../../components/charts/EvalRadarChart";
import MetricsTable from "../../../components/charts/MetricsTable";
import SegmentComparisonChart from "../../../components/charts/SegmentComparisonChart";
import ConfusionMatrix from "../../../components/charts/ConfusionMatrix";
import { AlertTriangleIcon } from "../../../components/ops/icons";
import { authHeaders } from "@/lib/authSession";
import "../analytics.css";

// Sample eval data — rendered when no live run is available. Never presented
// as a measurement; see the banner below.
const STATIC_RAG_METRICS = [
  { name: "faithfulness", value: 0.77, threshold: 0.6, passed: true },
  { name: "answer_relevancy", value: 0.72, threshold: 0.7, passed: true },
  { name: "context_precision", value: 1.0, threshold: 0.5, passed: true },
  { name: "context_recall", value: 1.0, threshold: 0.5, passed: true },
  { name: "groundedness", value: 0.65, threshold: 0.4, passed: true },
  { name: "citation_accuracy", value: 0.58, threshold: 0.4, passed: true },
  { name: "safety_probe_pass_rate", value: 1.0, threshold: 1.0, passed: true },
  { name: "abstention_precision", value: 0.75, threshold: 0.5, passed: true },
];

const STATIC_SEGMENT = {
  topic: {
    vat: [{ name: "faithfulness", value: 0.85, threshold: 0.6, passed: true }],
    paye: [{ name: "faithfulness", value: 0.72, threshold: 0.6, passed: true }],
    customs: [{ name: "faithfulness", value: 0.68, threshold: 0.6, passed: true }],
    registration: [{ name: "faithfulness", value: 0.81, threshold: 0.6, passed: true }],
    penalties: [{ name: "faithfulness", value: 0.74, threshold: 0.6, passed: true }],
    efris: [{ name: "faithfulness", value: 0.79, threshold: 0.6, passed: true }],
    exemptions: [{ name: "faithfulness", value: 0.66, threshold: 0.6, passed: true }],
    withholding: [{ name: "faithfulness", value: 0.77, threshold: 0.6, passed: true }],
  },
  locale: {
    en: [{ name: "faithfulness", value: 0.82, threshold: 0.6, passed: true }],
    lg: [{ name: "faithfulness", value: 0.61, threshold: 0.6, passed: true }],
  },
};

// Illustrative classifier matrix. There is no endpoint behind this one at all.
const CM_LABELS = ["vat", "paye", "customs", "registration", "penalties", "efris", "general"];
const CM_MATRIX = [
  [18, 1, 0, 0, 0, 1, 0],
  [0, 15, 0, 1, 0, 0, 1],
  [0, 0, 12, 0, 0, 0, 2],
  [0, 1, 0, 14, 0, 0, 0],
  [0, 0, 0, 0, 10, 0, 1],
  [1, 0, 0, 0, 0, 13, 0],
  [0, 1, 1, 0, 1, 0, 22],
];

function Evaluation() {
  const { data: liveEval, isFetching } = useQuery({
    queryKey: ["evaluation"],
    queryFn: async () => {
      const res = await fetch("/api/v1/evaluate", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        signal: AbortSignal.timeout(30000),
      });
      if (!res.ok) return null;
      return res.json();
    },
    retry: 0,
    staleTime: 5 * 60_000,
  });

  const live = Boolean(liveEval?.metrics);
  const metrics = liveEval?.metrics ?? STATIC_RAG_METRICS;
  const bySegment = liveEval?.by_segment ?? STATIC_SEGMENT;
  const allPass = metrics.every((m: { passed: boolean }) => m.passed);

  return (
    <OpsPage
      eyebrow="Observe"
      title="Answer evaluation"
      description="Eight checks on the quality of the assistant's answers, measured against the minimum each one has to clear before a release is allowed — broken down by tax topic and by language."
      actions={
        live ? (
          <span className={`ops-chip ${allPass ? "is-good" : "is-danger"}`}>
            {allPass ? "Every check passed" : "A check failed"}
          </span>
        ) : (
          <span className="ops-chip is-warn">Sample data</span>
        )
      }
    >
      {!live ? (
        <div className="ops-note" role="note">
          <span className="ops-note-mark" aria-hidden="true">
            <AlertTriangleIcon />
          </span>
          <div>
            <p className="ops-note-title">
              {isFetching ? "Asking for a live evaluation run…" : "Showing sample data, not a measurement"}
            </p>
            <p className="ops-note-body">
              A live run comes from <code>POST /v1/evaluate</code>, which needs the operator key
              rather than an ordinary staff sign-in, so this page is showing a stored sample
              instead. Read the numbers below as an illustration of what the report looks like,
              not as a measurement of the assistant right now — the authoritative run is the one
              that gates every release. The topic-sorting grid at the bottom is illustrative in
              every case; nothing measures it live.
            </p>
          </div>
        </div>
      ) : null}

      <section className="ops-chart-grid is-2" aria-label="Quality against the release minimums">
        <EvalRadarChart metrics={metrics} />
        <MetricsTable metrics={metrics} title="What each check measures" bare />
      </section>

      <section className="ops-chart-grid" aria-label="How well answers stick to the documents, by topic">
        <SegmentComparisonChart
          bySegment={bySegment}
          metricName="faithfulness"
          title="How well answers stick to the documents, by tax topic"
        />
      </section>

      {bySegment.locale ? (
        <section className="ops-chart-grid" aria-label="English against Luganda">
          <SegmentComparisonChart
            bySegment={{ locale: bySegment.locale }}
            metricName="faithfulness"
            title="Are Luganda answers as good as English ones?"
          />
        </section>
      ) : null}

      <section className="ops-chart-grid" aria-label="Sorting questions into the right topic">
        <ConfusionMatrix
          matrix={CM_MATRIX}
          labels={CM_LABELS}
          title="Sorting questions into the right topic (illustrative)"
        />
      </section>
    </OpsPage>
  );
}

export default function EvaluationPage() {
  return (
    <StaffGuard current="/analytics/evaluation" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <Evaluation />}
    </StaffGuard>
  );
}
