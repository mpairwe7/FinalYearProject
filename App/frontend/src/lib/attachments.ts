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

export interface PortalDomAction {
  step_number: number;
  element: string;
  selector: string;
  action_type: 'click' | 'fill' | 'select' | 'spotlight' | 'inspect';
  fill_value?: string | null;
  tooltip: string;
  security_gate: 'safe' | 'requires_confirmation' | 'blocked';
}

export interface ScreenshotHotspot {
  id: string;
  type: 'error' | 'action' | 'target';
  label: string;
  instruction: string;
  bbox?: [number, number, number, number];
  dom_selector?: string;
  confidence?: number;
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
  dom_recipe?: PortalDomAction[];
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
  /**
   * A small `data:` image of an attached picture, for its chip. Not a `blob:`
   * URL: the page's CSP allows `img-src 'self' data:`, and a blob URL rendered
   * as a broken image.
   */
  previewUrl?: string;
}

/**
 * Guidance steps arrive as "Step 1: Click …". The list that renders them is
 * numbered, so the prefix printed the number twice ("1. Step 1: Click …").
 */
export function stripStepPrefix(step: string): string {
  return step.replace(/^\s*step\s*\d+\s*[:.)\-–]\s*/i, '');
}

/** The edge, in CSS pixels, of the square a picture is cropped to for its chip. */
export const THUMBNAIL_EDGE = 56;

/**
 * A small cover-cropped JPEG `data:` URL of an image file, or `undefined` when
 * the browser cannot make one (no canvas, an undecodable file). Never throws:
 * a chip without a picture is the fallback, and the upload goes on regardless.
 */
export async function makeThumbnail(file: File, edge: number = THUMBNAIL_EDGE): Promise<string | undefined> {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return undefined;
  try {
    const bitmap = await createImageBitmap(file);
    try {
      const canvas = document.createElement('canvas');
      canvas.width = edge;
      canvas.height = edge;
      const context = canvas.getContext('2d');
      if (!context) return undefined;
      const scale = Math.max(edge / bitmap.width, edge / bitmap.height);
      const width = bitmap.width * scale;
      const height = bitmap.height * scale;
      context.drawImage(bitmap, (edge - width) / 2, (edge - height) / 2, width, height);
      return canvas.toDataURL('image/jpeg', 0.7);
    } finally {
      bitmap.close?.();
    }
  } catch {
    return undefined;
  }
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
