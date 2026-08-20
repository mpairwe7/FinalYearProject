import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DocumentInspectionViewer, ExtractedField } from '../../components/staff/DocumentInspectionViewer';

describe('DocumentInspectionViewer', () => {
  const mockFields: ExtractedField[] = [
    {
      id: 'f1',
      name: 'TIN',
      value: '1001234567',
      confidence: 0.98,
      source: 'vector_glyph',
      status: 'auto_use',
      page: 1,
      bbox: { x1: 10, y1: 10, x2: 100, y2: 30 },
    },
    {
      id: 'f2',
      name: 'PRN',
      value: '22009988776655',
      confidence: 0.88,
      source: 'ocr_inset',
      status: 'use_with_warning',
      page: 1,
      bbox: { x1: 10, y1: 40, x2: 120, y2: 60 },
    },
  ];

  it('renders document title, pages and extracted fields', () => {
    render(
      <DocumentInspectionViewer
        documentName="Assessment_Notice_2026.pdf"
        pageCount={2}
        fields={mockFields}
      />
    );

    expect(screen.getByText('Assessment_Notice_2026.pdf')).toBeDefined();
    expect(screen.getByText('Page 1 of 2')).toBeDefined();
    expect(screen.getAllByText('TIN').length).toBeGreaterThan(0);
    expect(screen.getAllByText('1001234567').length).toBeGreaterThan(0);
    expect(screen.getByText('Vector Glyph (100%)')).toBeDefined();
    expect(screen.getByText('Triton PP-OCRv6')).toBeDefined();
  });

  it('triggers onVerifyField and onFlagField callbacks', () => {
    const handleVerify = vi.fn();
    const handleFlag = vi.fn();

    render(
      <DocumentInspectionViewer
        documentName="Receipt.pdf"
        fields={mockFields}
        onVerifyField={handleVerify}
        onFlagField={handleFlag}
      />
    );

    const verifyButtons = screen.getAllByRole('button', { name: /verify/i });
    expect(verifyButtons.length).toBeGreaterThan(0);
    fireEvent.click(verifyButtons[0]);
    expect(handleVerify).toHaveBeenCalledWith('f1', '1001234567');

    const flagButtons = screen.getAllByRole('button', { name: /flag/i });
    expect(flagButtons.length).toBeGreaterThan(0);
    fireEvent.click(flagButtons[0]);
    expect(handleFlag).toHaveBeenCalledWith('f1', expect.any(String));
  });
});
