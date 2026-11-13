/**
 * What the officer's translation preview may claim, and send (G635).
 *
 * The composer sent its preview as `officer_reply_localized` even after the
 * officer had corrected the English, so a Luganda taxpayer read the old amount
 * as the officer's word; and it said "Figures & statutory amounts verified"
 * when translation was unavailable and the English came back unchanged.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TicketComposer } from '../../components/staff/TicketComposer';
import { analyticsApi, type TicketDetail } from '../../services/analyticsApi';

const ticket = {
  id: 't-1', status: 'assigned', assignee: 'nakato@ura.go.ug', locale: 'lg', modality: 'chat', viewers: [],
  chat: { state: 'officer' },
} as unknown as TicketDetail;

function renderComposer(onPatch = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TicketComposer
        ticket={ticket}
        who={{ authenticated: true, role: 'ura_staff', email: 'nakato@ura.go.ug' }}
        pending={false}
        isError={false}
        isSuccess={false}
        onPatch={onPatch}
      />
    </QueryClientProvider>,
  );
  return onPatch;
}

const reply = () => screen.getByLabelText('Reply to the taxpayer');

afterEach(() => vi.restoreAllMocks());

describe('TicketComposer translation preview', () => {
  it('never sends a preview of text the officer has since changed', async () => {
    vi.spyOn(analyticsApi, 'translate').mockResolvedValue({
      text: 'Obanjibwa UGX 200,000.', source_lang: 'en', target_lang: 'lg', latency_s: 0.1, backend: 'sunbird', figures_survived: true,
    });
    const onPatch = renderComposer();
    fireEvent.change(reply(), { target: { value: 'You owe UGX 200,000.' } });
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    await screen.findByText('Obanjibwa UGX 200,000.');
    fireEvent.change(reply(), { target: { value: 'You owe UGX 50,000.' } });
    expect(screen.queryByText('Obanjibwa UGX 200,000.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Send reply$/ }));
    expect(onPatch).toHaveBeenCalledTimes(1);
    expect(onPatch.mock.calls[0][0].officer_reply).toBe('You owe UGX 50,000.');
    expect(onPatch.mock.calls[0][0].officer_reply_localized).toBeUndefined();
  });

  // CodeRabbit on PR 730: an edit made while the translation was still on its
  // way was undone by its arrival, which showed the old preview and a passed
  // figure check as this reply's delivery preview.
  it('shows no preview for text edited while its translation was on its way', async () => {
    let arrive: (v: Awaited<ReturnType<typeof analyticsApi.translate>>) => void = () => {};
    vi.spyOn(analyticsApi, 'translate').mockImplementation(() => new Promise((resolve) => { arrive = resolve; }));
    const onPatch = renderComposer();
    fireEvent.change(reply(), { target: { value: 'You owe UGX 200,000.' } });
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    fireEvent.change(reply(), { target: { value: 'You owe UGX 50,000.' } });
    arrive({
      text: 'Obanjibwa UGX 200,000.', source_lang: 'en', target_lang: 'lg', latency_s: 0.1, backend: 'sunbird', figures_survived: true,
    });
    await screen.findByText(/preview again to see the translation of this text/i);
    expect(screen.queryByText('Obanjibwa UGX 200,000.')).toBeNull();
    expect(screen.queryByText(/Figure check passed/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Send reply$/ }));
    expect(onPatch.mock.calls[0][0].officer_reply_localized).toBeUndefined();
  });

  it('sends a checked preview of exactly the text being sent', async () => {
    vi.spyOn(analyticsApi, 'translate').mockResolvedValue({
      text: 'Obanjibwa UGX 50,000.', source_lang: 'en', target_lang: 'lg', latency_s: 0.1, backend: 'sunbird', figures_survived: true,
    });
    const onPatch = renderComposer();
    fireEvent.change(reply(), { target: { value: 'You owe UGX 50,000.' } });
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    await screen.findByText(/Figure check passed \(automatic\)/);
    fireEvent.click(screen.getByRole('button', { name: /^Send reply$/ }));
    expect(onPatch.mock.calls[0][0].officer_reply_localized).toBe('Obanjibwa UGX 50,000.');
  });

  it('says translation is unavailable rather than calling English text verified', async () => {
    vi.spyOn(analyticsApi, 'translate').mockResolvedValue({
      text: 'You owe UGX 50,000.', source_lang: 'en', target_lang: 'lg', latency_s: 0, backend: 'unavailable',
      error: 'translation backend unavailable', figures_survived: true,
    });
    const onPatch = renderComposer();
    fireEvent.change(reply(), { target: { value: 'You owe UGX 50,000.' } });
    fireEvent.click(screen.getByRole('button', { name: /Preview/ }));
    await waitFor(() => expect(screen.getByText(/Translation is unavailable/)).toBeInTheDocument());
    expect(screen.queryByText(/amounts verified/i)).toBeNull();
    expect(screen.queryByText(/Figure check passed/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Send reply$/ }));
    expect(onPatch.mock.calls[0][0].officer_reply_localized).toBeUndefined();
  });
});

// G636: a canned reply that states a check's result is a template; it cannot
// go to the taxpayer until the officer has written what they found.
describe('TicketComposer templates', () => {
  it('will not send a template with parts left to fill in', () => {
    const onPatch = renderComposer();
    fireEvent.click(screen.getByRole('button', { name: 'EFRIS invoice check' }));
    const send = screen.getByRole('button', { name: /^Send reply$/ });
    expect(send).toBeDisabled();
    expect(screen.getByText(/Fill in or delete the parts in \[\[double brackets\]\]/)).toBeInTheDocument();
    fireEvent.change(reply(), { target: { value: 'I checked invoice 123 in EFRIS: the signature did not match.' } });
    expect(send).toBeEnabled();
    fireEvent.click(send);
    expect(onPatch).toHaveBeenCalledTimes(1);
  });
});
