/**
 * The overrides console says how far an override reaches, and shows the live
 * answer a save would replace (G637).
 *
 * It said a question "must match exactly, character for character" and the
 * reply is "served word for word". The server matches the normalized question
 * (case, punctuation and spacing ignored), also tries the reworded and the
 * machine-translated question, translates the reply for other languages, and
 * an override with the same normalized question is replaced without a word.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import OverridesPage from '../../app/admin/overrides/page';
import { normalizeOverrideQuery } from '../../lib/overrides';
import { analyticsApi } from '../../services/analyticsApi';

vi.mock('../../components/StaffGuard', () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role: 'ura_admin', email: 'admin@ura.go.ug' }),
}));

const LIVE = {
  id: 'ov-1',
  match_query: 'what is the vat registration threshold',
  reply: 'UGX 150,000,000 a year.',
  source_url: '',
  created_by: 'admin',
  enabled: true,
  updated_at: 1790000000,
};

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OverridesPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(analyticsApi, 'overrides').mockResolvedValue({ overrides: [LIVE] } as never);
});

describe('normalizeOverrideQuery', () => {
  it('matches the server: case, punctuation and spacing ignored, decimals kept', () => {
    expect(normalizeOverrideQuery('  What is the VAT   registration threshold?? ')).toBe(
      'what is the vat registration threshold',
    );
    expect(normalizeOverrideQuery('Is the rate 0.5% or 1-2%?')).toBe('is the rate 0.5 or 1-2');
    expect(normalizeOverrideQuery('Omusolo gwa VAT guli ki?')).toBe('omusolo gwa vat guli ki');
  });
});

describe('Answer overrides console', () => {
  it('says how matching really works', async () => {
    renderPage();
    await screen.findByText('UGX 150,000,000 a year.');
    expect(screen.queryByText(/character for character/i)).toBeNull();
    expect(screen.queryByText(/word for word/i)).toBeNull();
    expect(screen.getByText(/case, punctuation and spacing are ignored/i)).toBeInTheDocument();
    expect(screen.getByText(/machine-translat/i)).toBeInTheDocument();
  });

  it('shows the live answer a save would replace, and says it replaces it', async () => {
    const put = vi.spyOn(analyticsApi, 'putOverride').mockResolvedValue({ ...LIVE, reply: 'UGX 300,000,000 a year.' } as never);
    renderPage();
    await screen.findByText('UGX 150,000,000 a year.');
    fireEvent.change(screen.getByLabelText(/Taxpayer question/), { target: { value: 'What is the VAT Registration Threshold?' } });
    fireEvent.change(screen.getByLabelText(/Staff reply/), { target: { value: 'UGX 300,000,000 a year.' } });
    const notice = screen.getByRole('region', { name: /Replaces a live answer/i });
    expect(notice).toHaveTextContent('what is the vat registration threshold');
    expect(notice).toHaveTextContent('UGX 150,000,000 a year.');
    fireEvent.click(screen.getByRole('button', { name: /Replace the live answer/i }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  });

  it('shows the key a new question will be matched on', async () => {
    renderPage();
    await screen.findByText('UGX 150,000,000 a year.');
    fireEvent.change(screen.getByLabelText(/Taxpayer question/), { target: { value: 'How do I get a TIN??' } });
    expect(screen.getByText(/Matched as: how do i get a tin/i)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /Replaces a live answer/i })).toBeNull();
    expect(screen.getByRole('button', { name: /Save override/i })).toBeInTheDocument();
  });
});
