"use client";

import React from "react";
import { TableScroll } from "../ops/OpsPage";

interface Props {
  matrix: number[][];
  labels: string[];
  title?: string;
}

/**
 * Predicted against true, as a heatmap.
 *
 * Two sequential ramps rather than one, because the two kinds of cell mean
 * opposite things: the diagonal is agreement and everything off it is a
 * confusion. Each ramp is a single hue, light to dark with magnitude, and the
 * cell's own number is always printed — the colour ranks the cells, it never
 * has to carry the value.
 */
export default function ConfusionMatrix({ matrix, labels, title = "Confusion matrix" }: Props) {
  const flat = matrix.flat();
  const maxVal = Math.max(...flat, 1);
  const fractional = flat.some((v) => v % 1 !== 0);

  return (
    <div className="ops-chart-card">
      <div className="ops-chart-head">
        <h3 className="ops-chart-title">{title}</h3>
        <span className="ops-chart-sub">rows predicted · columns true</span>
      </div>
      <TableScroll label={title}>
        <table className="ops-cm-table">
          <thead>
            <tr>
              <th className="ops-cm-corner" scope="col">
                Pred \ True
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
                      title={`True ${labels[j]}, predicted ${labels[i]}: ${val}`}
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
    </div>
  );
}
