/**
 * Shared types + helpers for chat document attachments.
 *
 * Upload flow: files picked in the composer are POSTed to
 * `/api/v1/documents/analyze`; the returned `document_id` is sent with the
 * next chat turn as `attachment_ids`, and backs the per-attachment PDF
 * analysis report at `/api/v1/documents/{id}/report`.
 */

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
}

/** Mirrors backend `documents.MAX_ATTACHMENTS_PER_TURN`. */
export const MAX_ATTACHMENTS = 3;
/** Mirrors backend `documents.MAX_FILE_BYTES` (40 MiB). */
export const MAX_ATTACHMENT_BYTES = 40 * 1024 * 1024;
/** Mirrors backend `documents.SUPPORTED_EXTENSIONS`. */
export const ATTACHMENT_ACCEPT = '.pdf,.docx,.xlsx,.csv,.txt,image/*';

const DOC_TYPE_LABELS: Record<string, string> = {
  receipt: 'Receipt',
  tin_card: 'TIN document',
  assessment: 'Assessment',
  customs_declaration: 'Customs',
  filing_form: 'Filing form',
  invoice: 'Invoice',
  generic: 'Document',
};

export function formatDocType(docType?: string): string {
  return (docType && DOC_TYPE_LABELS[docType]) || 'Document';
}

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}
