import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DocumentInspectionModal } from '../../components/DocumentInspectionModal';
import type { DocumentAnalysisData } from '../../lib/attachments';

describe('DocumentInspectionModal', () => {
  const mockAnalysis: DocumentAnalysisData = {
    documentId: 'doc1234567890abcdef1234567890abcdef',
    filename: 'efris_tax_invoice.pdf',
    kind: 'pdf',
    sizeBytes: 154200,
    docType: 'invoice',
    confidence: 0.95,
    summary: 'Tax invoice with 18% standard VAT calculated.',
    fields: {
      tins: ['1009876543'],
      prns: ['202699887766554'],
      efris_invoices: ['INV000011112222'],
      tax_heads: ['Value Added Tax (VAT)'],
      amounts: ['UGX 1,180,000'],
      dates: ['18/09/2026'],
      references: ['URA-2026-INV-01'],
    },
    taxReconciliation: {
      status: 'verified',
      subtotal_ugx: 1000000,
      tax_ugx: 180000,
      total_ugx: 1180000,
      effective_rate: 0.18,
      variance_ugx: 0,
      notes: [
        'Uganda Tax Identification format verified (valid 10-digit format).',
        'Standard 18.0% VAT confirmed: UGX 180,000 on taxable base of UGX 1,000,000 matches total UGX 1,180,000.',
      ],
    },
    tables: [
      {
        name: 'Line Items',
        rows: 5,
        cols: 4,
        headers: ['Item', 'Qty', 'Unit Price', 'Amount'],
      },
    ],
  };

  it('renders nothing when isOpen is false', () => {
    const { container } = render(
      <DocumentInspectionModal
        isOpen={false}
        onClose={vi.fn()}
        document={{
          id: 'doc123',
          name: 'efris_tax_invoice.pdf',
          analysis: mockAnalysis,
        }}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders document title, badges, tax reconciliation and extracted entities', () => {
    render(
      <DocumentInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        document={{
          id: 'doc123',
          name: 'efris_tax_invoice.pdf',
          sizeBytes: 154200,
          docType: 'invoice',
          analysis: mockAnalysis,
        }}
      />
    );

    expect(screen.getByText('efris_tax_invoice.pdf')).toBeDefined();
    expect(screen.getByText('Invoice')).toBeDefined();
    expect(screen.getByText('Tax invoice with 18% standard VAT calculated.')).toBeDefined();
    expect(screen.getByText('Taxable Subtotal')).toBeDefined();
    expect(screen.getByText('Total Payable')).toBeDefined();
    expect(screen.getAllByText(/1,180,000/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/1009876543/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/202699887766554/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/INV000011112222/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Value Added Tax (VAT)')).toBeDefined();
    expect(screen.getByText('Line Items')).toBeDefined();
  });

  it('handles quick prompt selection and report download callbacks', () => {
    const handleSelectPrompt = vi.fn();
    const handleDownloadReport = vi.fn();
    const handleClose = vi.fn();

    render(
      <DocumentInspectionModal
        isOpen={true}
        onClose={handleClose}
        document={{
          id: 'doc123',
          name: 'efris_tax_invoice.pdf',
          analysis: mockAnalysis,
        }}
        onSelectPrompt={handleSelectPrompt}
        onDownloadReport={handleDownloadReport}
      />
    );

    const downloadBtn = screen.getByText('Download Report');
    fireEvent.click(downloadBtn);
    expect(handleDownloadReport).toHaveBeenCalledWith('doc123', 'efris_tax_invoice.pdf');

    const promptBtns = screen.getAllByRole('button', { name: /Verify taxpayer TIN/i });
    expect(promptBtns.length).toBeGreaterThan(0);
    fireEvent.click(promptBtns[0]);
    expect(handleSelectPrompt).toHaveBeenCalled();
    expect(handleClose).toHaveBeenCalled();
  });

  it('closes on Escape key press', () => {
    const handleClose = vi.fn();
    render(
      <DocumentInspectionModal
        isOpen={true}
        onClose={handleClose}
        document={{
          id: 'doc123',
          name: 'efris_tax_invoice.pdf',
          analysis: mockAnalysis,
        }}
      />
    );

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(handleClose).toHaveBeenCalled();
  });

  it('renders interactive screenshot guidance and resolution steps', () => {
    const screenshotDoc: DocumentAnalysisData = {
      documentId: 'doc_shot_1',
      filename: 'ura_portal_error.png',
      kind: 'image',
      sizeBytes: 85400,
      docType: 'portal_screenshot',
      confidence: 0.96,
      screenshot_guidance: {
        is_screenshot: true,
        detected_portal: 'URA e-Services PRN & Payments Portal',
        portal_url: 'https://portal.ura.go.ug',
        detected_state: 'PRN Generation Failure',
        issues_detected: ['Missing mandatory payment mode selection'],
        steps: [
          'Step 1: Select your bank from the Payment Mode dropdown.',
          'Step 2: Click the green Generate PRN button.',
        ],
        hotspots: [
          {
            id: 'err-1',
            type: 'error',
            label: 'Missing Field',
            instruction: 'Select payment mode',
          },
        ],
      },
    };

    render(
      <DocumentInspectionModal
        isOpen={true}
        onClose={vi.fn()}
        document={{
          id: 'doc_shot_1',
          name: 'ura_portal_error.png',
          analysis: screenshotDoc,
        }}
      />
    );

    expect(screen.getAllByText(/URA e-Services PRN & Payments Portal/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/PRN Generation Failure/i)).toBeDefined();
    expect(screen.getByText(/Missing mandatory payment mode selection/i)).toBeDefined();
    expect(screen.getByText(/Step 1: Select your bank from the Payment Mode dropdown./i)).toBeDefined();
    expect(screen.getByText(/Open Official Portal ↗/i)).toBeDefined();
  });
});
