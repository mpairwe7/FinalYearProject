"use client";

import React from "react";

interface Props {
  matrix: number[][];
  labels: string[];
  title?: string;
}

export default function ConfusionMatrix({
  matrix,
  labels,
  title = "Confusion Matrix",
}: Props) {
  const maxVal = Math.max(...matrix.flat(), 1);

  return (
    <div className="chart-card chart-card-wide">
      <h4 className="chart-title">{title}</h4>
      <div className="cm-wrapper">
        <table className="cm-table">
          <thead>
            <tr>
              <th className="cm-corner">Pred \ True</th>
              {labels.map((l) => (
                <th key={l} className="cm-header">{l}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => (
              <tr key={i}>
                <td className="cm-row-label">{labels[i]}</td>
                {row.map((val, j) => {
                  const intensity = val / maxVal;
                  const isDiag = i === j;
                  const bg = isDiag
                    ? `rgba(139, 92, 246, ${0.15 + intensity * 0.65})`
                    : `rgba(239, 68, 68, ${intensity * 0.5})`;
                  return (
                    <td
                      key={j}
                      className="cm-cell"
                      style={{ background: bg }}
                      title={`True: ${labels[j]}, Pred: ${labels[i]}, Count: ${val}`}
                    >
                      {val > 0 ? val.toFixed(matrix.flat().some((v) => v % 1 !== 0) ? 2 : 0) : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
