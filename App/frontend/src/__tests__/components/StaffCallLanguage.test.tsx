import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CallRow } from '@/components/staff/calls/CallRow';
import { CallTranscript } from '@/components/staff/calls/CallTranscript';
import { CallMetricsCard } from '@/components/staff/calls/CallMetricsCard';
import { callLanguageName, callLanguageSourceLabel } from '@/lib/callLanguage';
import type { CallRecord, CallTurn } from '@/services/callsApi';

const call = (overrides: Partial<CallRecord> = {}): CallRecord => ({
  call_id: 'call_abcdef123456',
  conversation_id: 'conv_1',
  user_id: '',
  tenant_id: 'default',
  channel: 'browser_sim',
  locale: 'lg',
  status: 'ai',
  started_at: 1_700_000_000,
  ended_at: 1_700_000_060,
  end_reason: '',
  transferred: 0,
  transfer_reason: '',
  ticket_id: null,
  officer_id: null,
  officer_rating: null,
  officer_note: null,
  ...overrides,
});

describe('staff call language', () => {
  it('names languages in plain English', () => {
    expect(callLanguageName('lg')).toBe('Luganda');
    expect(callLanguageName('sw')).toBe('Swahili');
    expect(callLanguageName(undefined)).toBe('English');
    expect(callLanguageName('xx')).toBe('xx');
    expect(callLanguageSourceLabel('auto')).toBe('detected');
    expect(callLanguageSourceLabel('override')).toBe('chosen on screen');
  });

  it('badges the queue row with the call language', () => {
    render(<CallRow call={call()} isSelected={false} onSelect={vi.fn()} />);
    expect(screen.getByTitle('Call language').textContent).toBe('Luganda');
  });

  it('shows a language switch as a note between turns, not a bubble', () => {
    const turns: CallTurn[] = [
      {
        id: 't1', call_id: 'c', seq: 1, speaker: 'system', kind: 'language',
        text: 'Language: Luganda (detected, 93%)', low_conf_words: [], mean_word_prob: null,
        faithfulness: null, latencies: {}, created_at: 0,
      },
    ];
    render(<CallTranscript turns={turns} />);
    const note = screen.getByRole('note');
    expect(note.textContent).toBe('Language: Luganda (detected, 93%)');
    expect(screen.queryByText('System')).toBeNull();
  });

  it('adds language tiles when the call recorded them', () => {
    render(
      <CallMetricsCard
        metrics={{ duration_s: 60, language: { final: 'lg', switches: 1, detection_latency_ms_p95: 420 } }}
      />,
    );
    expect(screen.getByText('Luganda')).toBeDefined();
    expect(screen.getByText('Language switches')).toBeDefined();
    expect(screen.getByText('420ms')).toBeDefined();
  });

  it('shows no language tiles for a call without them', () => {
    render(<CallMetricsCard metrics={{ duration_s: 60 }} />);
    expect(screen.queryByText('Language switches')).toBeNull();
  });
});
