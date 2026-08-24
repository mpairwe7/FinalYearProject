"use client";

import React from "react";
import { TableScroll } from "../ops/OpsPage";
import { ChartNote } from "./chartTheme";

interface Props {
  matrix: number[][];
  labels: string[];
  title?: string;
}

/**
 * What the classifier guessed against what the question really was.
 *
 * Two sequential ramps rather than one, because the two kinds of cell mean
 * opposite things: the diagonal is agreement and everything off it is a
 * confusion. Each ramp is a single hue, light to dark with magnitude, and the
 * cell's own number is always printed — the colour ranks the cells, it never
 * has to carry the value.
 *
 * The header said "rows predicted · columns true" and the corner cell said
 * "Pred \ True", which assumes the reader already knows how to read a
 * confusion matrix. They are the same words spelled out below, plus one
 * sentence saying where to look — the diagonal — and what the off-diagonal
 * cells are.
 */
export default function ConfusionMatrix({ matrix, labels, title = "Confusion matrix" }: Props) {
  const flat = matrix.flat();
  const maxVal = Math.max(...flat, 1);
  const fractional = flat.some((v) => v % 1 !== 0);
  const total = flat.reduce((sum, v) => sum + v, 0);
  const correct = matrix.reduce((sum, row, i) => sum + (row[i] ?? 0), 0);
  const correctPct = total > 0 ? Math.round((correct / total) * 100) : null;

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">
          each row is what the assistant guessed · each column is the real topic
        </span>
      </div>
      <TableScroll label={title}>
        <table className="ops-cm-table">
          <thead>
            <tr>
              <th className="ops-cm-corner" scope="col">
                Guessed \ Really was
              </th>
              {labels.map((l) => (
                <th key={l} className="ops-cm-header" scope="col">
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={labels[i] ?? i}>
                <th className="ops-cm-row-label" scope="row">
                  {labels[i]}
                </th>
                {row.map((val, j) => {
                  const intensity = val / maxVal;
                  const isDiag = i === j;
                  // The deepest steps carry white text; the rest keep ink.
                  const deep = intensity > 0.55;
                  return (
                    <td
                      key={j}
                      className={`ops-cm-cell${isDiag ? " is-diag" : ""}${deep ? " is-deep" : ""}`}
                      style={{ "--cm-intensity": intensity } as React.CSSProperties}
                      title={`${val} question${val === 1 ? "" : "s"} really about ${labels[j]}, guessed as ${labels[i]}`}
                    >
                      {val > 0 ? val.toFixed(fractional ? 2 : 0) : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <ChartNote>
        The shaded diagonal running from top-left to bottom-right is where the guess matched
        the real topic — {correctPct != null ? `${correctPct}% of questions here` : "the correct cases"}.
        Every other cell is a question sorted into the wrong topic.
      </ChartNote>
    </div>
  );
}
