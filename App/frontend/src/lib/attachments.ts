/**
 * Shared types + helpers for chat document attachments.
 *
 * Upload flow: files picked in the composer are POSTed to
 * `/api/v1/documents/analyze`; the returned `document_id` is sent with the
 * next chat turn as `attachment_ids`, and backs the per-attachment PDF
 * analysis report at `/api/v1/documents/{id}/report`.
 */

export interface DocumentTaxReconciliation {
  status: 'verified' | 'discrepancy_detected' | 'unreconciled_partial' | 'informational_only';
  subtotal_ugx?: number | null;
  tax_ugx?: number | null;
  total_ugx?: number | null;
  effective_rate?: number | null;
  variance_ugx?: number | null;
  notes?: string[];
}

export interface ScreenshotHotspot {
  id: string;
  type: 'error' | 'action' | 'target';
  label: string;
  instruction: string;
}

export interface ScreenshotGuidance {
  is_screenshot?: boolean;
  detected_portal?: string;
  portal_url?: string;
  portal_category?: string;
  detected_state?: string;
  issues_detected?: string[];
  steps?: string[];
  hotspots?: ScreenshotHotspot[];
  direct_action?: {
    label: string;
    url: string;
  };
}

export interface DocumentAnalysisData {
  documentId: string;
  filename: string;
  kind: string;
  sizeBytes: number;
  docType: string;
  confidence: number;
  classificationMethod?: string;
  matchedKeywords?: string[];
  fields?: {
    tins?: string[];
    prns?: string[];
    efris_invoices?: string[];
    amounts?: string[];
    dates?: string[];
    references?: string[];
    tax_heads?: string[];
  };
  tables?: Array<{
    name: string;
    rows: number;
    cols: number;
    headers: string[];
    numeric_totals?: Record<string, number>;
  }>;
  textPreview?: string;
  truncated?: boolean;
  summary?: string;
  taxReconciliation?: DocumentTaxReconciliation;
  screenshot_guidance?: ScreenshotGuidance;
  warnings?: string[];
  expiresInSeconds?: number;
}

/** A file in the composer, from selection through analysis. */
export interface PendingAttachment {
  clientId: string;
  name: string;
  sizeBytes: number;
  status: 'uploading' | 'ready' | 'error';
  /** Backend id once analysed — sent as chat `attachment_ids`. */
  documentId?: string;
  docType?: string;
  error?: string;
  analysis?: DocumentAnalysisData;
}

/** Mirrors backend `documents.MAX_ATTACHMENTS_PER_TURN`. */
export const MAX_ATTACHMENTS = 3;
/** Mirrors backend `documents.MAX_FILE_BYTES` (40 MiB). */
export const MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024;
/** Mirrors backend `documents.SUPPORTED_EXTENSIONS`. */
export const ATTACHMENT_ACCEPT = '.pdf,.docx,.xlsx,.csv,.txt,image/*';

const DOC_TYPE_LABELS_EN: Record<string, string> = {
  receipt: 'Receipt',
  tin_card: 'TIN document',
  assessment: 'Assessment',
  customs_declaration: 'Customs',
  filing_form: 'Filing form',
  invoice: 'Invoice',
  statutory_act: 'Tax Law / Act',
  portal_screenshot: 'URA Portal Screenshot',
  generic: 'Document',
};

const DOC_TYPE_LABELS_LG: Record<string, string> = {
  receipt: "Kasiita k'okusasula",
  tin_card: 'Ekiwandiiko kya TIN',
  assessment: "Okubalirira omusolo",
  customs_declaration: "Tamko ly'omwalo",
  filing_form: "Foomu y'omusolo",
  invoice: 'Invooyisi / EFRIS',
  statutory_act: "Etteeka ly'Omusolo",
  portal_screenshot: "Ekifaananyi ky'omukutu gwa URA",
  generic: 'Ekiwandiiko',
};

const DOC_TYPE_LABELS_SW: Record<string, string> = {
  receipt: 'Stakabadhi',
  tin_card: 'Hati ya TIN',
  assessment: 'Tathmini ya kodi',
  customs_declaration: 'Tamko la forodha',
  filing_form: 'Fomu ya kodi',
  invoice: 'Invoisi / EFRIS',
  statutory_act: 'Sheria ya Kodi',
  portal_screenshot: 'Picha ya Tovuti ya URA',
  generic: 'Nyaraka',
};

export function formatDocType(docType?: string, locale?: string): string {
  if (!docType) return 'Document';
  const loc = (locale || 'en').toLowerCase();
  if (loc === 'lg') return DOC_TYPE_LABELS_LG[docType] || DOC_TYPE_LABELS_EN[docType] || 'Ekiwandiiko';
  if (loc === 'sw') return DOC_TYPE_LABELS_SW[docType] || DOC_TYPE_LABELS_EN[docType] || 'Nyaraka';
  return DOC_TYPE_LABELS_EN[docType] || 'Document';
}

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
