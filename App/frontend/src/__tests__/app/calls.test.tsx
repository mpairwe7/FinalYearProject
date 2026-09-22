import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { StaffCalls } from '../../app/calls/page';
import { callsApi, CallRecord } from '../../services/callsApi';

const MOCK_CALL: CallRecord = {
  call_id: 'call-test-123',
  conversation_id: 'conv-test-123',
  user_id: 'taxpayer-1',
  tenant_id: 'default',
  channel: 'browser_sim',
  locale: 'en',
  status: 'transferring',
  started_at: Date.now() / 1000 - 120,
  ended_at: null,
  end_reason: '',
  transferred: true,
  transfer_reason: 'Taxpayer requested human escalation',
  ticket_id: 'tkt-call-1',
  officer_id: null,
  officer_rating: null,
  officer_note: null,
  summary: {
    subject: 'Group Import Tax',
    summary: 'Caller inquired about group import customs tariffs and clearance process.',
    caller_intent: 'Customs clarification',
    resolution: 'transferred',
    key_facts: ['Valuation threshold applies'],
    follow_ups: ['Officer to verify customs entry'],
    sentiment: 'neutral',
    ai_handling_notes: 'Transferred after word clarification',
  },
  metrics: {
    duration_s: 120,
    caller_turns: 3,
    clarifications_asked: 1,
    mean_word_prob: 0.85,
    latency: {
      turn_to_audio_ms_p50: 1250,
      turn_to_audio_ms_p95: 2100,
    },
  },
  turns: [
    {
      id: 'turn-1',
      call_id: 'call-test-123',
      seq: 1,
      speaker: 'caller',
      kind: 'utterance',
      text: 'I want to ask about group mid-port',
      low_conf_words: [{ word: 'mid-port', prob: 0.31 }],
      mean_word_prob: 0.65,
      faithfulness: null,
      latencies: { stt_ms: 150 },
      created_at: Date.now() / 1000 - 100,
    },
    {
      id: 'turn-2',
      call_id: 'call-test-123',
      seq: 2,
      speaker: 'assistant',
      kind: 'clarify',
      text: 'Excuse me, did you say import?',
      low_conf_words: [],
      mean_word_prob: 1.0,
      faithfulness: null,
      latencies: { brain_ms: 200 },
      created_at: Date.now() / 1000 - 90,
    },
  ],
};

function renderPage(role = 'ura_staff') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StaffCalls who={{ email: 'officer@ura.go.ug', role, name: 'Officer Sarah' }} />
    </QueryClientProvider>,
  );
}

describe('StaffCalls page', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(callsApi, 'listCalls').mockResolvedValue({
      count: 1,
      calls: [MOCK_CALL],
    });
    vi.spyOn(callsApi, 'getCall').mockResolvedValue(MOCK_CALL);
    vi.spyOn(callsApi, 'getMetrics').mockResolvedValue({
      period_days: 7,
      total_calls: 14,
      containment_rate: 0.65,
      transfer_rate: 0.28,
      transfers_by_reason: { caller_requested: 3, clarification_failed: 1 },
      clarification_rate: 0.22,
      clarification_first_try_rate: 0.75,
      mean_word_prob: 0.88,
      avg_duration_s: 145,
      avg_officer_rating: 4.8,
      latency_p50_ms: 1200,
      latency_p95_ms: 2200,
    });
    vi.spyOn(callsApi, 'submitReview').mockResolvedValue({ ok: true, call_id: 'call-test-123', rating: 5 });
  });

  it('renders queued live call with topic and status pill', async () => {
    renderPage('ura_staff');
    expect(await screen.findByText('Group Import Tax')).toBeInTheDocument();
    expect(screen.getByText('Transfer waiting')).toBeInTheDocument();
  });

  it('renders low-confidence words wrapped in mark tag with tooltip', async () => {
    renderPage('ura_staff');
    const mark = await screen.findByTitle('heard with 31% confidence');
    expect(mark).toBeInTheDocument();
    expect(mark.tagName.toLowerCase()).toBe('mark');
    expect(mark.textContent).toContain('mid-port');
  });

  it('allows staff officers to see Take Call button', async () => {
    renderPage('ura_staff');
    expect(await screen.findByRole('button', { name: /Take Call/i })).toBeInTheDocument();
  });

  it('hides Take Call button from auditor role', async () => {
    renderPage('ura_auditor');
    await screen.findByText('Group Import Tax');
    expect(screen.queryByRole('button', { name: /Take Call/i })).not.toBeInTheDocument();
  });

  it('renders performance overview SLO gauge cards on overview tab', async () => {
    renderPage('ura_staff');
    const overviewTab = screen.getByRole('tab', { name: /Performance Overview/i });
    fireEvent.click(overviewTab);

    expect(await screen.findByText('Containment rate')).toBeInTheDocument();
    expect(screen.getByText('Clarification success')).toBeInTheDocument();
    expect(screen.getByText('p95 first audio latency')).toBeInTheDocument();
  });
});
