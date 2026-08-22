"use client";

import React from "react";
import { ChartTable, STATUS_MARK } from "./chartTheme";

interface Props {
  thumbsUp: number;
  thumbsDown: number;
}

/**
 * Satisfaction — a figure and one bar, not a two-slice donut.
 *
 * A pie with two segments is the canonical "the number is the chart" case: the
 * reader has to convert two arcs back into one percentage that the component
 * was already computing and printing underneath. A meter says the same thing in
 * a tenth of the space and stays readable at 200px wide, which is where this
 * card actually lives.
 */
export default function FeedbackPieChart({ thumbsUp, thumbsDown }: Props) {
  const total = thumbsUp + thumbsDown;
  const pct = total > 0 ? (thumbsUp / total) * 100 : null;

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">Answer satisfaction</h3>
        <span className="ops-chart-sub">{total} rated</span>
      </div>

      {pct == null ? (
        <p className="ops-empty-body">
          No answer has been rated in this period, so there is nothing to report yet.
        </p>
      ) : (
        <>
          <p className="ops-gauge-value ops-gauge-value-lg">
            {pct.toFixed(1)}
            <span className="ops-gauge-unit">%</span>
          </p>
          <p className="ops-gauge-target">rated helpful</p>

          <div
            className="ops-meter"
            role="img"
            aria-label={`${thumbsUp} of ${total} rated answers were marked helpful, ${pct.toFixed(
              1,
            )} percent`}
          >
            <span
              className="ops-meter-fill"
              style={{ width: `${pct}%`, background: STATUS_MARK.good }}
            />
            <span
              className="ops-meter-fill"
              style={{ width: `${100 - pct}%`, background: STATUS_MARK.critical }}
            />
          </div>

          <ul className="ops-legend">
            <li className="ops-legend-item">
              <span className="ops-legend-swatch" style={{ background: STATUS_MARK.good }} />
              Helpful <strong>{thumbsUp}</strong>
            </li>
            <li className="ops-legend-item">
              <span className="ops-legend-swatch" style={{ background: STATUS_MARK.critical }} />
              Not helpful <strong>{thumbsDown}</strong>
            </li>
          </ul>
        </>
      )}

      <ChartTable
        caption="Answer feedback"
        columns={["Rating", "Count"]}
        rows={[
          ["Helpful", thumbsUp],
          ["Not helpful", thumbsDown],
        ]}
      />
    </div>
  );
}
