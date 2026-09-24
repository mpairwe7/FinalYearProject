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
  // Officer Call Desk (docs/plans/officer-call-desk-plan.md §5). Optional:
  // rows written before the columns existed read back with their defaults.
  /** Handoff-packet topic key, e.g. `objection_or_dispute`; '' until transferred. */
  topic?: string;
  priority?: 'low' | 'normal' | 'high' | 'urgent';
  transfer_requested_at?: number | null;
  target_team?: string;
  claimed_by?: string;
  claimed_at?: number | null;
  bridged_at?: number | null;
  hold_started_at?: number | null;
  hold_total_s?: number;
  outcome?: string;
  wrapup_note?: string | null;
  wrapup_at?: number | null;
  /** 1 when nobody answered the transfer (or the caller left waiting) and a callback is owed. */
  needs_callback?: number | boolean;
  callback_reason?: string;
  callback_done_at?: number | null;
  callback_done_by?: string | null;
  brief_json?: string | null;
  brief_updated_at?: number | null;
  risk_json?: string | null;
  /** The officer's brief, when one has been written (per-call routes only). */
  brief?: CallBrief | null;
  summary?: CallSummary;
  metrics?: Record<string, unknown>;
  turns?: CallTurn[];
  ticket?: Record<string, unknown>;
}

/** One claim in an officer's brief, with the transcript turns it rests on. */
export interface BriefClaim {
  text: string;
  turn_seqs: number[];
}

/** The officer's brief (docs/plans/officer-call-desk-plan.md §6.2). Always English. */
export interface CallBrief {
  why_officer: BriefClaim | null;
  caller_goal: BriefClaim;
  ai_already_said: BriefClaim[];
  still_open: BriefClaim[];
  details_given: Array<{ label: string; value: string; turn_seqs: number[] }>;
  sentiment: 'calm' | 'confused' | 'frustrated' | 'distressed';
  topic: string;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  suggested_opener: string;
  language: string;
  turns_covered: number;
  generated_at: number;
  model: string;
  fallback: boolean;
}

export interface ClaimResult {
  claimed: boolean;
  call_id: string;
  officer_name: string;
  claim_expires_at: number;
}

/** Someone else already has the call. */
export class ClaimConflictError extends Error {
  constructor(
    message: string,
    public readonly claimedBy: string,
    public readonly officerName: string,
  ) {
    super(message);
    this.name = 'ClaimConflictError';
  }
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

/** A Call Desk action: a 409 comes back as the typed conflict, not a bare error. */
async function deskAction<T>(callId: string, action: string): Promise<T> {
  const res = await fetch(`${BASE}/v1/admin/calls/${encodeURIComponent(callId)}/${action}`, {
    method: 'POST',
    headers: authHeaders(),
    signal: AbortSignal.timeout(15000),
  });
  const body = await res.json().catch(() => ({}));
  if (res.status === 409) {
    throw new ClaimConflictError(
      String(body.detail || 'Another officer has this call'),
      String(body.claimed_by || ''),
      String(body.officer_name || ''),
    );
  }
  if (!res.ok) {
    throw new Error(String(body.detail || `${res.status} ${res.statusText}`));
  }
  return body as T;
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

  /** The brief, or `null` while the first one is being written (202). */
  getBrief: async (callId: string, refresh = false): Promise<CallBrief | null> => {
    const res = await fetch(
      `${BASE}/v1/admin/calls/${encodeURIComponent(callId)}/brief${refresh ? '?refresh=1' : ''}`,
      { headers: authHeaders(), signal: AbortSignal.timeout(20000) },
    );
    if (res.status === 202) return null;
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },

  claimCall: (callId: string) => deskAction<ClaimResult>(callId, 'claim'),
  releaseCall: (callId: string) => deskAction<{ released: boolean }>(callId, 'release'),
  endCall: (callId: string) => deskAction<{ ended: boolean }>(callId, 'end'),
};
