/**
 * API client for staff call management, live streaming, metrics, and reviews.
 */

import { authHeaders } from '@/lib/authSession';

const BASE = '/api';

export interface CallTurn {
  id: string;
  call_id: string;
  seq: number;
  speaker: 'caller' | 'assistant' | 'officer' | 'system';
  kind: 'utterance' | 'clarify' | 'confirm' | 'answer' | 'filler' | 'handoff' | 'notice' | 'language';
  text: string;
  low_conf_words: Array<{ word: string; prob: number }>;
  mean_word_prob: number | null;
  faithfulness: number | null;
  latencies: Record<string, number>;
  created_at: number;
}

export interface CallSummary {
  subject: string;
  summary: string;
  caller_intent: string;
  resolution: 'answered' | 'transferred' | 'unresolved' | 'abandoned';
  key_facts: string[];
  follow_ups: string[];
  sentiment: string;
  ai_handling_notes: string;
  /** The call's (final) language, e.g. `lg`. The summary text itself is always English. */
  language?: string;
  languages_used?: string[];
}

export interface CallRecord {
  call_id: string;
  conversation_id: string;
  user_id: string;
  tenant_id: string;
  channel: string;
  locale: string;
  status: 'ai' | 'transferring' | 'bridged' | 'ended';
  started_at: number;
  ended_at: number | null;
  end_reason: string;
  transferred: number | boolean;
  transfer_reason: string;
  ticket_id: string | null;
  officer_id: string | null;
  officer_rating: number | null;
  officer_note: string | null;
  summary?: CallSummary;
  metrics?: Record<string, unknown>;
  turns?: CallTurn[];
  ticket?: Record<string, unknown>;
}

export interface CallAggregates {
  period_days: number;
  total_calls: number;
  containment_rate: number;
  transfer_rate: number;
  transfers_by_reason: Record<string, number>;
  clarification_rate: number;
  clarification_first_try_rate: number;
  mean_word_prob: number;
  avg_duration_s: number;
  avg_officer_rating: number;
  latency_p50_ms: number;
  latency_p95_ms: number;
}

async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(15000);
  const signal =
    init.signal && typeof AbortSignal.any === 'function'
      ? AbortSignal.any([init.signal, timeoutSignal])
      : init.signal || timeoutSignal;

  const res = await fetch(`${BASE}${url}`, {
    ...init,
    headers: authHeaders(init.headers as Record<string, string> | undefined),
    signal,
  });

  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const callsApi = {
  listCalls: (status = 'all', limit = 50, offset = 0) =>
    fetchJson<{ calls: CallRecord[]; count: number }>(
      `/v1/admin/calls?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`,
    ),

  getCall: (callId: string) =>
    fetchJson<CallRecord>(`/v1/admin/calls/${encodeURIComponent(callId)}`),

  getMetrics: (days = 7) =>
    fetchJson<CallAggregates>(`/v1/admin/calls/metrics?days=${days}`),

  submitReview: (callId: string, rating: number, note = '') =>
    fetchJson<{ ok: boolean; call_id: string; rating: number }>(
      `/v1/admin/calls/${encodeURIComponent(callId)}/review`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, note }),
      },
    ),
};
