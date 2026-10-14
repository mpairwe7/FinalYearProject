"use client";

/**
 * How satisfied people were, level by level: five faces, a bar each.
 *
 * An average of 3.4 hides whether the service is mostly fine with a few
 * furious ratings or uniformly lukewarm, and those need different fixes; the
 * distribution says which. Each bar carries its face, its words and its
 * count, so nothing is read from colour or bar length alone, and the note
 * under it states the reading.
 */
import React from "react";
import { ChartNote, ChartTable } from "../charts/chartTheme";
import { SATISFACTION_FACES, SATISFACTION_LABELS_EN } from "../rating/SatisfactionScale";
import type { RatingsFigures } from "../../services/analyticsApi";

const LEVELS = ["1", "2", "3", "4", "5"] as const;

export function distributionReading(figures: Pick<RatingsFigures, "distribution" | "answered" | "low" | "csat_pct">): string {
  const rated = LEVELS.reduce((sum, level) => sum + (figures.distribution[level] || 0), 0);
  if (!rated) return "Nothing has been rated in this period yet.";
  const satisfied = (figures.distribution["4"] || 0) + (figures.distribution["5"] || 0);
  const low = (figures.distribution["1"] || 0) + (figures.distribution["2"] || 0);
  const lowShare = Math.round((low / rated) * 1000) / 10;
  const verdict =
    low === 0
      ? "nobody was dissatisfied"
      : lowShare >= 20
        ? `${low} (${lowShare}%) were dissatisfied — enough to read each one`
        : `${low} (${lowShare}%) were dissatisfied`;
  return `${satisfied} of ${rated} rated were satisfied or very satisfied; ${verdict}.`;
}

export function SatisfactionDistribution({
  figures,
  title = "How satisfied taxpayers were",
}: {
  figures: Pick<RatingsFigures, "distribution" | "answered" | "low" | "csat_pct">;
  title?: string;
}) {
  const counts = LEVELS.map((level) => figures.distribution[level] || 0);
  const rated = counts.reduce((sum, n) => sum + n, 0);
  const max = Math.max(1, ...counts);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">{rated} rated, by level</span>
      </div>
      <ul className="qa-sat-dist" aria-label={title}>
        {[...LEVELS].reverse().map((level) => {
          const index = Number(level) - 1;
          const count = counts[index];
          const share = rated ? Math.round((count / rated) * 1000) / 10 : 0;
          return (
            <li key={level} className={`qa-sat-row is-level-${level}`}>
              <span className="qa-sat-face" aria-hidden="true">
                {SATISFACTION_FACES[index]}
              </span>
              <span className="qa-sat-name">
                {SATISFACTION_LABELS_EN[index]} <span className="qa-sat-score">({level}/5)</span>
              </span>
              <span className="qa-sat-bar" aria-hidden="true">
                <span className="qa-sat-fill" style={{ width: `${(count / max) * 100}%` }} />
              </span>
              <span className="qa-sat-count">
                {count}
                <span className="qa-sat-pct"> · {share}%</span>
              </span>
            </li>
          );
        })}
      </ul>
      <ChartNote>{distributionReading(figures)}</ChartNote>
      <ChartTable
        caption={title}
        columns={["Level", "Ratings", "Share"]}
        rows={LEVELS.map((level, index) => [
          `${level} · ${SATISFACTION_LABELS_EN[index]}`,
          counts[index],
          rated ? `${Math.round((counts[index] / rated) * 1000) / 10}%` : "—",
        ])}
      />
    </div>
  );
}
