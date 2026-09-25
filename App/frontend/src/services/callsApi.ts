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

export interface OfficerPresence {
  user_id: string;
  display_name: string;
  status: 'available' | 'busy' | 'away' | 'on_call' | 'wrap_up' | 'offline';
  raw_status?: string;
  languages: string[];
  teams: string[];
  current_call_id: string;
  last_seen: number;
  updated_at: number;
}

export interface PresenceBoardResponse {
  officers: OfficerPresence[];
  teams: string[];
}

export interface CallerHistoryResponse {
  anonymous: boolean;
  user_id?: string;
  calls: CallRecord[];
  tickets: Array<Record<string, unknown>>;
}

export interface WrapUpPayload {
  outcome: 'resolved' | 'follow_up' | 'callback' | 'referred' | 'abandoned' | 'ai_resolved';
  note: string;
  ticket_action?: 'resolve' | 'keep_open' | 'create';
  rating?: number;
  rating_note?: string;
}

export interface TransferPayload {
  team?: string;
  officer_id?: string;
  note?: string;
}

export interface ListCallsParams {
  status?: string;
  q?: string;
  date_from?: number;
  date_to?: number;
  outcome?: string;
  language?: string;
  officer_id?: string;
  has_ticket?: boolean;
  topic?: string;
  needs_callback?: boolean;
  sort?: string;
  limit?: number;
  offset?: number;
}

export interface ListCallsResponse {
  calls: CallRecord[];
  total: number;
  count: number;
  status_filter: string;
  limit: number;
  offset: number;
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
async function deskAction<T>(callId: string, action: string, body?: unknown): Promise<T> {
  const headers = authHeaders(body ? { 'Content-Type': 'application/json' } : undefined);
  const res = await fetch(`${BASE}/v1/admin/calls/${encodeURIComponent(callId)}/${action}`, {
    method: 'POST',
    headers,
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(15000),
  });
  const resBody = await res.json().catch(() => ({}));
  if (res.status === 409) {
    throw new ClaimConflictError(
      String(resBody.detail || 'Another officer has this call'),
      String(resBody.claimed_by || ''),
      String(resBody.officer_name || ''),
    );
  }
  if (!res.ok) {
    throw new Error(String(resBody.detail || `${res.status} ${res.statusText}`));
  }
  return resBody as T;
}

export const callsApi = {
  listCalls: (params: string | ListCallsParams = 'all', limit = 50, offset = 0) => {
    let qs: string;
    if (typeof params === 'string') {
      qs = `status=${encodeURIComponent(params)}&limit=${limit}&offset=${offset}`;
    } else {
      const sp = new URLSearchParams();
      if (params.status) sp.set('status', params.status);
      if (params.q) sp.set('q', params.q);
      if (params.date_from !== undefined) sp.set('date_from', String(params.date_from));
      if (params.date_to !== undefined) sp.set('date_to', String(params.date_to));
      if (params.outcome) sp.set('outcome', params.outcome);
      if (params.language) sp.set('language', params.language);
      if (params.officer_id) sp.set('officer_id', params.officer_id);
      if (params.has_ticket !== undefined) sp.set('has_ticket', String(params.has_ticket));
      if (params.topic) sp.set('topic', params.topic);
      if (params.needs_callback !== undefined) sp.set('needs_callback', String(params.needs_callback));
      if (params.sort) sp.set('sort', params.sort);
      sp.set('limit', String(params.limit ?? limit));
      sp.set('offset', String(params.offset ?? offset));
      qs = sp.toString();
    }
    return fetchJson<ListCallsResponse>(`/v1/admin/calls?${qs}`);
  },

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
  holdCall: (callId: string, on: boolean) =>
    deskAction<{ hold: boolean; call_id: string; hold_total_s?: number }>(callId, 'hold', { on }),
  transferCall: (callId: string, payload: TransferPayload) =>
    deskAction<{ transferred: boolean; call_id: string; target_team: string }>(callId, 'transfer', payload),
  wrapUpCall: (callId: string, payload: WrapUpPayload) =>
    deskAction<{ wrapped_up: boolean; call_id: string; outcome: string }>(callId, 'wrapup', payload),
  markCallbackDone: (callId: string, note = '') =>
    deskAction<{ ok: boolean; call_id: string }>(callId, 'callback-done', { note }),
  getCallerHistory: (callId: string) =>
    fetchJson<CallerHistoryResponse>(`/v1/admin/calls/${encodeURIComponent(callId)}/caller-history`),
  getPresence: () =>
    fetchJson<PresenceBoardResponse>('/v1/admin/officers/presence'),
  updatePresence: (payload: Partial<OfficerPresence>) =>
    fetchJson<OfficerPresence>('/v1/admin/officers/me/presence', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
};
