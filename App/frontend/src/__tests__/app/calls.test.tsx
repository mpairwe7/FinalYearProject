import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffCalls } from '../../app/calls/page';
import { resetLobbyForTests } from '@/services/callLobbySocket';
import { callsApi, CallRecord, ClaimConflictError } from '@/services/callsApi';
import { resetSessionForTests } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { brief, callRecord, FakeSocket } from '../helpers/callConsole';

const ENDED: CallRecord = callRecord({
  call_id: 'call_ended_1',
  status: 'ended',
  ended_at: Date.now() / 1000 - 10,
  summary: {
    subject: 'Group Import Tax',
    summary: 'Caller inquired about group import customs tariffs.',
    caller_intent: 'Customs clarification',
    resolution: 'transferred',
    key_facts: [],
    follow_ups: [],
    sentiment: 'neutral',
    ai_handling_notes: '',
  },
});

function mockCalls(live: CallRecord[]) {
  vi.spyOn(callsApi, 'listCalls').mockImplementation(async (status = 'all') => {
    const calls = status === 'live' ? live : [ENDED];
    return { count: calls.length, calls };
  });
  vi.spyOn(callsApi, 'getCall').mockImplementation(
    async (id: string) => [...live, ENDED].find((c) => c.call_id === id) || ENDED,
  );
}

function renderPage(role = 'ura_staff') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  return render(
    <QueryClientProvider client={client}>
      <StaffCalls who={{ authenticated: true, email: 'okello@ura.go.ug', role }} />
    </QueryClientProvider>,
  );
}

describe('The Call Desk (/calls)', () => {
  beforeEach(() => {
    useCallConsoleStore.getState().reset();
    resetLobbyForTests();
    FakeSocket.reset();
    vi.stubGlobal('WebSocket', FakeSocket);
    window.history.replaceState(null, '', '/calls');
    Element.prototype.scrollIntoView = vi.fn();
    mockCalls([callRecord()]);
    vi.spyOn(callsApi, 'getBrief').mockResolvedValue(brief());
    vi.spyOn(callsApi, 'getMetrics').mockResolvedValue({
      period_days: 7, total_calls: 14, containment_rate: 0.65, transfer_rate: 0.28,
      transfers_by_reason: {}, clarification_rate: 0.22, clarification_first_try_rate: 0.75,
      mean_word_prob: 0.88, avg_duration_s: 145, avg_officer_rating: 4.8, latency_p50_ms: 1200, latency_p95_ms: 2200,
    });
  });

  afterEach(() => {
    resetSessionForTests();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('puts a waiting caller at the top and briefs the officer before they take it', async () => {
    renderPage();
    const queue = await screen.findByRole('navigation', { name: 'Live calls' });
    expect(within(queue).getByRole('region', { name: 'Waiting for an officer' })).toBeInTheDocument();
    expect(within(queue).getByText('Objection or dispute')).toBeInTheDocument();
    expect(await screen.findByText(/Object to a tax assessment/)).toBeInTheDocument();
    expect(screen.getByText('Caller waiting')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Take call/ })).toBeInTheDocument();
    // The transcript waits behind a toggle until the officer wants it.
    expect(screen.getByRole('button', { name: /Transcript \(3 turns\)/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('opens the transcript at the turn a line of the brief rests on', async () => {
    renderPage();
    fireEvent.click((await screen.findAllByRole('button', { name: 'Show turn 3 in the transcript' }))[0]);
    const turn = document.getElementById('turn-3');
    expect(turn).not.toBeNull();
    expect(turn!.className).toContain('is-flash');
  });

  it('shows who won when another officer took the call first', async () => {
    vi.spyOn(callsApi, 'claimCall').mockRejectedValue(new ClaimConflictError('taken', 'nakato', 'Officer Nakato'));
    renderPage();
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /Take call/ }));
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Taken by Officer Nakato');
  });

  it('lets an auditor read everything and take nothing', async () => {
    renderPage('ura_auditor');
    expect(await screen.findByText(/Object to a tax assessment/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Take call/ })).not.toBeInTheDocument();
  });

  it('follows a call the AI is still handling, and offers to take it over', async () => {
    mockCalls([callRecord({ call_id: 'call_ai_1', status: 'ai', transferred: false })]);
    renderPage();
    expect(await screen.findByText(/AI handling ·/)).toBeInTheDocument();
    const mark = await screen.findByTitle('heard with 31% confidence');
    expect(mark.tagName.toLowerCase()).toBe('mark');
    expect(screen.getByRole('button', { name: /Take over/ })).toBeInTheDocument();
  });

  it('keeps the performance overview', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: /Performance/ }));
    expect(await screen.findByText('Containment rate')).toBeInTheDocument();
    expect(screen.getByText('p95 first audio latency')).toBeInTheDocument();
  });

  it('opens a past call from History with its summary', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: 'History' }));
    expect(await screen.findByText('Caller inquired about group import customs tariffs.')).toBeInTheDocument();
  });

  it('opens the call a link names', async () => {
    mockCalls([callRecord(), callRecord({ call_id: 'call_ai_2', status: 'ai', transferred: false })]);
    window.history.replaceState(null, '', '/calls?call=call_ai_2');
    renderPage();
    expect(await screen.findByText(/AI handling ·/)).toBeInTheDocument();
  });
});
