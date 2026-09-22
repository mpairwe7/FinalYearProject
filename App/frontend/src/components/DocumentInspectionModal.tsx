'use client';

import React, { useEffect, useCallback } from 'react';
import type { DocumentAnalysisData } from '../lib/attachments';
import { formatDocType, formatFileSize } from '../lib/attachments';
import { useTranslation } from '../lib/i18n';
import { useChatStore } from '../store/useChatStore';
import {
  CloseIcon,
  DownloadIcon,
  FileIcon,
  CheckCircleIcon,
  SparklesIcon,
} from './Icons';

interface DocumentInspectionModalProps {
  isOpen: boolean;
  onClose: () => void;
  document: {
    id: string;
    name: string;
    sizeBytes?: number;
    docType?: string;
    analysis?: DocumentAnalysisData;
  } | null;
  onSelectPrompt?: (prompt: string) => void;
  onDownloadReport?: (docId: string, docName: string) => void;
}

export function DocumentInspectionModal({
  isOpen,
  onClose,
  document,
  onSelectPrompt,
  onDownloadReport,
}: DocumentInspectionModalProps) {
  const t = useTranslation();
  const locale = useChatStore((s) => s.locale);

  // Escape key closes modal
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const copyToClipboard = useCallback((text: string) => {
    void navigator.clipboard.writeText(text);
  }, []);

  if (!isOpen || !document) return null;

  const analysis = document.analysis;
  const recon = analysis?.taxReconciliation;
  const fields = analysis?.fields;
  const docType = document.docType || analysis?.docType;
  const sg = analysis?.screenshot_guidance;

  // Build quick prompt suggestions based on document type, screenshot diagnosis, and fields
  const quickPrompts: string[] = [];
  if (sg?.is_screenshot) {
    quickPrompts.push(`How do I resolve the issue shown on ${sg.detected_portal || 'the URA portal'}?`);
    quickPrompts.push(`Walk me through the steps to complete this transaction on ${sg.detected_portal || 'URA e-Services'}.`);
  }
  if (fields?.tins && fields.tins.length > 0) {
    quickPrompts.push(`Verify taxpayer TIN ${fields.tins[0]} and explain filing obligations.`);
  }
  if (fields?.prns && fields.prns.length > 0) {
    quickPrompts.push(`How do I pay PRN ${fields.prns[0]} through bank or mobile money?`);
  }
  if (recon?.status === 'discrepancy_detected') {
    quickPrompts.push(`Explain the tax arithmetic discrepancy in this ${document.name}.`);
  } else if (docType === 'invoice' || docType === 'receipt') {
    quickPrompts.push(`Confirm if 18% VAT and EFRIS invoicing rules were correctly applied here.`);
  } else if (docType === 'assessment') {
    quickPrompts.push(`What are the deadlines and procedure to object to this tax assessment?`);
  } else if (!sg?.is_screenshot) {
    quickPrompts.push(`Summarize key tax implications and next steps for this document.`);
  }

  return (
    <div className="doc-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="doc-modal-container"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="doc-modal-title"
      >
        <header className="doc-modal-header">
          <div className="doc-modal-title-group">
            <div className="doc-modal-icon-badge">
              <FileIcon />
            </div>
            <div>
              <h2 id="doc-modal-title" className="doc-modal-title">
                {document.name}
              </h2>
              <div className="doc-modal-badges">
                <span className="doc-modal-type-badge">
                  {formatDocType(docType, locale)}
                </span>
                {document.sizeBytes != null && (
                  <span className="doc-modal-meta-badge">
                    {formatFileSize(document.sizeBytes)}
                  </span>
                )}
                {analysis?.confidence != null && (
                  <span className="doc-modal-meta-badge">
                    {(analysis.confidence * 100).toFixed(0)}% match
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="doc-modal-close-btn"
            onClick={onClose}
            aria-label={t('common.close')}
          >
            <CloseIcon />
          </button>
        </header>

        <div className="doc-modal-body">
          {/* Summary */}
          {analysis?.summary && (
            <section className="doc-modal-section">
              <h3 className="doc-modal-section-title">{t('documents.summary')}</h3>
              <p className="doc-modal-summary-text">{analysis.summary}</p>
            </section>
          )}

          {/* URA Portal Troubleshooting & Interactive Guidance */}
          {sg?.is_screenshot && (
            <section className="doc-modal-section doc-modal-portal-card" aria-label="Portal Troubleshooting Guidance">
              <div className="doc-modal-portal-header">
                <div>
                  <h3 className="doc-modal-section-title">🌐 {sg.detected_portal || 'URA Web Portal'}</h3>
                  {sg.detected_state ? (
                    <span className="doc-modal-portal-state">{sg.detected_state}</span>
                  ) : null}
                </div>
                {sg.portal_url ? (
                  <a
                    href={sg.portal_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="doc-modal-portal-link"
                  >
                    Open Official Portal ↗
                  </a>
                ) : null}
              </div>

              {sg.issues_detected && sg.issues_detected.length > 0 && (
                <div className="doc-modal-portal-issue-box">
                  <strong>Diagnostic Alert:</strong>
                  <ul>
                    {sg.issues_detected.map((issue, idx) => (
                      <li key={idx}>{issue}</li>
                    ))}
                  </ul>
                </div>
              )}

              {sg.hotspots && sg.hotspots.length > 0 && (
                <div className="doc-modal-hotspots-panel">
                  <h4 className="doc-modal-sub-title">🎯 UI Click Targets & Guidance Points</h4>
                  <div className="doc-modal-hotspots-grid">
                    {sg.hotspots.map((spot) => (
                      <div key={spot.id} className={`doc-modal-hotspot-item is-${spot.type}`}>
                        <span className="doc-modal-hotspot-tag">{spot.label}</span>
                        <p className="doc-modal-hotspot-inst">{spot.instruction}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {sg.steps && sg.steps.length > 0 && (
                <div className="doc-modal-portal-steps">
                  <h4 className="doc-modal-sub-title">📋 Step-by-Step Resolution Instructions</h4>
                  <ol className="doc-modal-steps-list">
                    {sg.steps.map((step, idx) => (
                      <li key={idx} className="doc-modal-step-item">
                        <span>{step}</span>
                        <button
                          type="button"
                          className="doc-modal-step-copy"
                          onClick={() => copyToClipboard(step)}
                          title="Copy instruction"
                        >
                          📋
                        </button>
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </section>
          )}

          {/* Tax Reconciliation & Audit */}
          {recon && (
            <section className="doc-modal-section doc-modal-recon-card">
              <div className="doc-modal-recon-header">
                <h3 className="doc-modal-section-title">{t('documents.reconciliation')}</h3>
                <span
                  className={`doc-modal-status-badge doc-modal-status-${recon.status}`}
                >
                  {recon.status === 'verified' && `✓ ${t('documents.verified')}`}
                  {recon.status === 'discrepancy_detected' && `! ${t('documents.discrepancy')}`}
                  {recon.status === 'unreconciled_partial' && 'ℹ Partial equation'}
                  {recon.status === 'informational_only' && 'General filing'}
                </span>
              </div>

              {(recon.subtotal_ugx != null || recon.total_ugx != null) && (
                <div className="doc-modal-recon-grid">
                  {recon.subtotal_ugx != null && (
                    <div className="doc-modal-recon-item">
                      <span className="doc-modal-recon-label">Taxable Subtotal</span>
                      <span className="doc-modal-recon-val">
                        UGX {recon.subtotal_ugx.toLocaleString()}
                      </span>
                    </div>
                  )}
                  {recon.tax_ugx != null && (
                    <div className="doc-modal-recon-item">
                      <span className="doc-modal-recon-label">
                        VAT / Tax {recon.effective_rate ? `(${(recon.effective_rate * 100).toFixed(1)}%)` : ''}
                      </span>
                      <span className="doc-modal-recon-val">
                        UGX {recon.tax_ugx.toLocaleString()}
                      </span>
                    </div>
                  )}
                  {recon.total_ugx != null && (
                    <div className="doc-modal-recon-item doc-modal-recon-item-total">
                      <span className="doc-modal-recon-label">Total Payable</span>
                      <span className="doc-modal-recon-val">
                        UGX {recon.total_ugx.toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>
              )}

              {recon.notes && recon.notes.length > 0 && (
                <ul className="doc-modal-notes-list">
                  {recon.notes.map((n, idx) => (
                    <li key={idx} className="doc-modal-note-item">
                      <CheckCircleIcon />
                      <span>{n}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* Extracted Fields */}
          {fields && (
            <section className="doc-modal-section">
              <h3 className="doc-modal-section-title">{t('documents.entities')}</h3>
              <div className="doc-modal-fields-grid">
                {fields.tins && fields.tins.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">TINs</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.tins.map((tin) => (
                        <button
                          key={tin}
                          type="button"
                          className="doc-modal-entity-chip"
                          onClick={() => copyToClipboard(tin)}
                          title="Click to copy TIN"
                        >
                          {tin} 📋
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {fields.prns && fields.prns.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">PRNs</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.prns.map((prn) => (
                        <button
                          key={prn}
                          type="button"
                          className="doc-modal-entity-chip"
                          onClick={() => copyToClipboard(prn)}
                          title="Click to copy PRN"
                        >
                          {prn} 📋
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {fields.efris_invoices && fields.efris_invoices.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">EFRIS Invoices</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.efris_invoices.map((inv) => (
                        <button
                          key={inv}
                          type="button"
                          className="doc-modal-entity-chip"
                          onClick={() => copyToClipboard(inv)}
                          title="Click to copy Invoice"
                        >
                          {inv} 📋
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {fields.tax_heads && fields.tax_heads.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">Tax Regimes</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.tax_heads.map((head) => (
                        <span key={head} className="doc-modal-tag-chip">
                          {head}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {fields.amounts && fields.amounts.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">Amounts</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.amounts.map((amt, idx) => (
                        <span key={idx} className="doc-modal-val-chip">
                          {amt}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {fields.dates && fields.dates.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">Dates</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.dates.map((d, idx) => (
                        <span key={idx} className="doc-modal-meta-chip">
                          {d}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {fields.references && fields.references.length > 0 && (
                  <div className="doc-modal-field-block">
                    <span className="doc-modal-field-label">References</span>
                    <div className="doc-modal-chip-wrap">
                      {fields.references.map((r, idx) => (
                        <span key={idx} className="doc-modal-meta-chip">
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Tables Summary */}
          {analysis?.tables && analysis.tables.length > 0 && (
            <section className="doc-modal-section">
              <h3 className="doc-modal-section-title">{t('documents.tables')}</h3>
              <div className="doc-modal-tables-list">
                {analysis.tables.map((tbl, idx) => (
                  <div key={idx} className="doc-modal-table-card">
                    <div className="doc-modal-table-header">
                      <strong>{tbl.name || `Table ${idx + 1}`}</strong>
                      <span>
                        {tbl.rows} rows × {tbl.cols} cols
                      </span>
                    </div>
                    {tbl.headers.length > 0 && (
                      <p className="doc-modal-table-headers">
                        Columns: {tbl.headers.slice(0, 6).join(', ')}
                        {tbl.headers.length > 6 ? '…' : ''}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Quick Action Suggestions */}
          {quickPrompts.length > 0 && (
            <section className="doc-modal-section">
              <h3 className="doc-modal-section-title">{t('documents.suggested')}</h3>
              <div className="doc-modal-prompts-list">
                {quickPrompts.map((p, idx) => (
                  <button
                    key={idx}
                    type="button"
                    className="doc-modal-prompt-btn"
                    onClick={() => {
                      onSelectPrompt?.(p);
                      onClose();
                    }}
                  >
                    <SparklesIcon />
                    <span>{p}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>

        <footer className="doc-modal-footer">
          <button
            type="button"
            className="doc-modal-report-btn"
            onClick={() => onDownloadReport?.(document.id, document.name)}
          >
            <DownloadIcon />
            <span>{t('documents.downloadReport')}</span>
          </button>
          <button
            type="button"
            className="doc-modal-close-secondary-btn"
            onClick={onClose}
          >
            {t('common.close')}
          </button>
        </footer>
      </div>
    </div>
  );
}
