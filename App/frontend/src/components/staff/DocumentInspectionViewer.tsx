"use client";

import React, { useState, useMemo } from "react";
import "./documentInspection.css";

export interface BoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface ExtractedField {
  id: string;
  name: string;
  value: string;
  confidence: number;
  source: "vector_glyph" | "ocr_inset" | "ruling_grid" | "vlm" | "manual";
  status: "auto_use" | "use_with_warning" | "review_required";
  page: number;
  bbox?: BoundingBox;
  polygon?: number[][];
}

export interface DocumentInspectionProps {
  documentName: string;
  documentUrl?: string;
  pageCount?: number;
  fields: ExtractedField[];
  tables?: Array<{
    name: string;
    matrix: string[][];
    source: string;
    status: string;
  }>;
  onVerifyField?: (fieldId: string, updatedValue?: string) => void;
  onFlagField?: (fieldId: string, reason: string) => void;
}

export function DocumentInspectionViewer({
  documentName,
  documentUrl,
  pageCount = 1,
  fields = [],
  tables = [],
  onVerifyField,
  onFlagField,
}: DocumentInspectionProps) {
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [hoveredFieldId, setHoveredFieldId] = useState<string | null>(null);
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null);
  const [zoomLevel, setZoomLevel] = useState<number>(100);

  const pageFields = useMemo(() => {
    return fields.filter((f) => f.page === currentPage);
  }, [fields, currentPage]);

  const activeHoveredField = useMemo(() => {
    return fields.find((f) => f.id === hoveredFieldId) || null;
  }, [fields, hoveredFieldId]);

  return (
    <div className="doc-inspect-container">
      <header className="doc-inspect-header">
        <div className="doc-inspect-title-group">
          <h3 className="doc-inspect-title">{documentName}</h3>
          <span className="doc-inspect-badge">
            Page {currentPage} of {Math.max(1, pageCount)}
          </span>
        </div>
        <div className="doc-inspect-controls">
          <button
            type="button"
            className="doc-inspect-btn"
            disabled={currentPage <= 1}
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            aria-label="Previous page"
          >
            Previous
          </button>
          <button
            type="button"
            className="doc-inspect-btn"
            disabled={currentPage >= pageCount}
            onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
            aria-label="Next page"
          >
            Next
          </button>
          <div className="doc-inspect-zoom">
            <button
              type="button"
              className="doc-inspect-btn-icon"
              onClick={() => setZoomLevel((z) => Math.max(50, z - 25))}
              aria-label="Zoom out"
            >
              -
            </button>
            <span className="doc-inspect-zoom-text">{zoomLevel}%</span>
            <button
              type="button"
              className="doc-inspect-btn-icon"
              onClick={() => setZoomLevel((z) => Math.min(200, z + 25))}
              aria-label="Zoom in"
            >
              +
            </button>
          </div>
        </div>
      </header>

      <div className="doc-inspect-split">
        {/* Left Visual Viewer Pane */}
        <section className="doc-inspect-visual-pane" aria-label="Visual document preview">
          <div
            className="doc-inspect-canvas-wrapper"
            style={{ transform: `scale(${zoomLevel / 100})`, transformOrigin: "top left" }}
          >
            {documentUrl ? (
              <img
                src={documentUrl}
                alt={`Document scan page ${currentPage}`}
                className="doc-inspect-image"
              />
            ) : (
              <div className="doc-inspect-placeholder-canvas">
                <div className="doc-inspect-placeholder-sheet">
                  <div className="doc-inspect-sheet-header">UGANDA REVENUE AUTHORITY</div>
                  <div className="doc-inspect-sheet-body">
                    {pageFields.map((f) => (
                      <div
                        key={f.id}
                        className={`doc-inspect-mock-line ${
                          hoveredFieldId === f.id || selectedFieldId === f.id
                            ? "is-highlighted"
                            : ""
                        }`}
                      >
                        <span className="mock-label">{f.name}:</span>
                        <span className="mock-val">{f.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Bounding box highlight overlays */}
            {pageFields.map((f) => {
              if (!f.bbox) return null;
              const isHovered = hoveredFieldId === f.id;
              const isSelected = selectedFieldId === f.id;
              return (
                <div
                  key={`bbox-${f.id}`}
                  className={`doc-inspect-bbox ${f.source} ${f.status} ${
                    isHovered || isSelected ? "is-active" : ""
                  }`}
                  style={{
                    left: `${f.bbox.x1}px`,
                    top: `${f.bbox.y1}px`,
                    width: `${f.bbox.x2 - f.bbox.x1}px`,
                    height: `${f.bbox.y2 - f.bbox.y1}px`,
                  }}
                  onMouseEnter={() => setHoveredFieldId(f.id)}
                  onMouseLeave={() => setHoveredFieldId(null)}
                  onClick={() => setSelectedFieldId(f.id)}
                  title={`${f.name}: ${f.value} (${Math.round(f.confidence * 100)}% conf)`}
                />
              );
            })}
          </div>
        </section>

        {/* Right Extraction & Verification Pane */}
        <section className="doc-inspect-data-pane" aria-label="Extracted fields and verification">
          <div className="doc-inspect-fields-list">
            <h4 className="doc-inspect-section-heading">Extracted Fields ({pageFields.length})</h4>
            {pageFields.length === 0 ? (
              <p className="doc-inspect-empty">No fields extracted for this page.</p>
            ) : (
              pageFields.map((field) => {
                const isHovered = hoveredFieldId === field.id;
                const isSelected = selectedFieldId === field.id;
                const sourceLabel =
                  field.source === "vector_glyph"
                    ? "Vector Glyph (100%)"
                    : field.source === "ocr_inset"
                    ? "Triton PP-OCRv6"
                    : field.source === "ruling_grid"
                    ? "Ruling Grid"
                    : "VLM Fallback";

                return (
                  <article
                    key={field.id}
                    className={`doc-inspect-card ${field.status} ${
                      isHovered || isSelected ? "is-active" : ""
                    }`}
                    onMouseEnter={() => setHoveredFieldId(field.id)}
                    onMouseLeave={() => setHoveredFieldId(null)}
                    onClick={() => setSelectedFieldId(field.id)}
                  >
                    <div className="doc-inspect-card-head">
                      <span className="doc-inspect-field-name">{field.name}</span>
                      <span className={`doc-inspect-status-pill ${field.status}`}>
                        {field.status === "auto_use"
                          ? "Auto Use"
                          : field.status === "use_with_warning"
                          ? "Warning"
                          : "Review Needed"}
                      </span>
                    </div>

                    <div className="doc-inspect-field-val">{field.value}</div>

                    <div className="doc-inspect-card-meta">
                      <span className={`doc-inspect-source-pill ${field.source}`}>
                        {sourceLabel}
                      </span>
                      <div className="doc-inspect-conf-bar-group">
                        <span className="doc-inspect-conf-label">
                          {Math.round(field.confidence * 100)}%
                        </span>
                        <div className="doc-inspect-conf-track">
                          <div
                            className={`doc-inspect-conf-fill ${
                              field.confidence >= 0.9
                                ? "high"
                                : field.confidence >= 0.75
                                ? "mid"
                                : "low"
                            }`}
                            style={{ width: `${Math.round(field.confidence * 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>

                    {onVerifyField || onFlagField ? (
                      <div className="doc-inspect-actions">
                        {onVerifyField ? (
                          <button
                            type="button"
                            className="doc-inspect-act-btn verify"
                            onClick={(e) => {
                              e.stopPropagation();
                              onVerifyField(field.id, field.value);
                            }}
                          >
                            Verify
                          </button>
                        ) : null}
                        {onFlagField ? (
                          <button
                            type="button"
                            className="doc-inspect-act-btn flag"
                            onClick={(e) => {
                              e.stopPropagation();
                              onFlagField(field.id, "Ambiguous text in scan");
                            }}
                          >
                            Flag
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </article>
                );
              })
            )}

            {tables.length > 0 ? (
              <div className="doc-inspect-tables-section">
                <h4 className="doc-inspect-section-heading">Structured Tables ({tables.length})</h4>
                {tables.map((t, tIdx) => (
                  <div key={`table-${tIdx}`} className="doc-inspect-table-card">
                    <div className="doc-inspect-table-head">
                      <span>{t.name}</span>
                      <span className="doc-inspect-source-pill vector_glyph">{t.source}</span>
                    </div>
                    <div className="doc-inspect-table-scroll">
                      <table className="doc-inspect-preview-table">
                        <tbody>
                          {t.matrix.map((row, rI) => (
                            <tr key={`r-${rI}`}>
                              {row.map((cell, cI) => (
                                <td key={`c-${cI}`}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}
