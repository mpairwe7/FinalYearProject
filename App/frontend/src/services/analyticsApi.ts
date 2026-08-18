/**
 * Analytics API client — fetches dashboard, evaluation, and comparison
 * data from the backend for visualization.
 *
 * All endpoints proxy through Next.js /api/* rewrite (same-origin).
 */

import { authHeaders } from '@/lib/authSession';

export interface DashboardData {
  uptime_seconds: number;
  requests: {
    counters: Record<string, number>;
    latency: Record<string, { p50: number; p95: number; p99: number; avg: number; count: number }>;
  };
  chat: { event_counts: Record<string, number> };
  sessions: {
    period_days: number;
    total_sessions: number;
    avg_messages_per_session: number;
    max_messages_in_session: number;
  };
  conversations: {
    period_days: number;
    total_conversations: number;
    avg_response_time_ms: number;
    avg_confidence: number;
    top_topics: { tag: string; count: number }[];
  };
  // Inline feedback from the dashboard endpoint (mirrors FeedbackSummary)
  feedback: {
    period_days: number;
    total: number;
    thumbs_up: number;
    thumbs_down: number;
    satisfaction_pct: number;
    recent: {
      id: string;
      rating: string;
      comment: string;
      user_query: string;
      created_at: number;
    }[];
  };
}

export interface EvalMetric {
  name: string;
  value: number;
  threshold: number;
  passed: boolean;
}

export interface EvalReport {
  sample_size: number;
  started_at: number;
  duration_ms: number;
  backend: string;
  metrics: EvalMetric[];
  regressions: string[];
  passed: boolean;
  by_segment: Record<string, Record<string, EvalMetric[]>>;
}

export interface TicketStats {
  total: number;
  open: number;
  assigned: number;
  resolved: number;
  wontfix: number;
  by_priority: Record<string, number>;
}

export interface TicketQueueItem {
  id: string;
  conversation_id?: string;
  status: string;
  priority: string;
  reason: string;
  user_query: string;
  bot_reply: string;
  created_at: number;
  updated_at: number;
  /** Owning team, routed from the handoff topic at escalation time. */
  team?: string;
  assignee?: string;
  first_response_at?: number;
  reply_at?: number;
  officer_reply?: string;
  viewers?: string[];
  handoff?: {
    summary?: string;
    topic?: string;
    priority?: string;
    required_details?: string[];
    sources_reviewed?: string[];
    /** Taxpayer's state at the point of transfer. */
    sentiment?: string;
    /** "warm" = brief the officer before they engage; "cold" = go ahead. */
    transfer_style?: string;
    turns_before_handoff?: number;
    opening_guidance?: string;
    /** False when the ticket could not be persisted — treat as unqueued. */
    ticket_persisted?: boolean;
    delivery_warning?: string;
  };
  response_judge?: {
    decision?: string;
    final_decision?: string;
    applied_revision?: boolean;
    reasons?: string[];
    confidence_band?: string;
  };
}

/** One turn of the conversation captured when the ticket was raised. */
export interface TicketTranscriptTurn {
  user_message: string;
  bot_reply: string;
  created_at: number;
  sources?: string[];
  topic_tag?: string;
}

/**
 * A single ticket with everything an officer needs to act.
 *
 * The transcript is the snapshot taken at escalation, not a live join —
 * `conversations` is purged after CONVERSATION_TTL_DAYS while a ticket
 * has no TTL, so the queue's oldest entries would otherwise arrive with
 * nothing attached.
 */
export interface TicketDetail extends TicketQueueItem {
  transcript?: TicketTranscriptTurn[];
  /** Shown to the taxpayer on their next turn. Distinct from staff_note. */
  officer_reply?: string;
  /** Internal. Never reaches the taxpayer. */
  staff_note?: string;
  assignee?: string;
  session_id?: string;
  first_response_at?: number;
  resolved_at?: number;
  reply_delivered_at?: number;
}

export interface TicketSla {
  period_days: number;
  tickets: number;
  responded: number;
  resolved: number;
  awaiting_first_response: number;
  awaiting_next_response?: number;
  median_response_seconds: number | null;
  median_resolution_seconds: number | null;
  median_next_reply_seconds?: number | null;
  breaching_first_response?: number;
  breaching_next_reply?: number;
  breaching?: number;
}

export interface TicketPatch {
  status?: string;
  assignee?: string;
  staff_note?: string;
  priority?: string;
  officer_reply?: string;
}

export interface TicketQueueResponse {
  count: number;
  status_filter: string;
  priority_filter?: string;
  team_filter?: string;
  /** Team names in effect, so the UI does not hardcode the org chart. */
  teams?: string[];
  limit: number;
  offset: number;
  tickets: TicketQueueItem[];
}

export interface FeedbackSummary {
  total: number;
  thumbs_up: number;
  thumbs_down: number;
  satisfaction_pct: number;
  recent: {
    id: string;
    rating: string;
    comment: string;
    user_query: string;
    created_at: number;
  }[];
}

export interface FlagRecord {
  name: string;
  default: boolean;
  description: string;
  enabled: boolean;
  overridden?: boolean;
  protected?: boolean;
  rollout?: { percent: number; cohorts: string[]; allowlist_size: number } | null;
}

export interface AnswerOverride {
  id: string;
  match_query: string;
  reply: string;
  source_url?: string;
  created_by?: string;
  enabled?: boolean;
}

const BASE = "/api";

async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    ...init,
    headers: authHeaders(init.headers as Record<string, string> | undefined),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const analyticsApi = {
  dashboard: (days = 30) => fetchJson<DashboardData>(`/v1/analytics/dashboard?days=${days}`),
  feedbackSummary: (days = 30) => fetchJson<FeedbackSummary>(`/v1/feedback/summary?days=${days}`),
  ticketStats: (days = 30) => fetchJson<TicketStats>(`/v1/admin/tickets/stats?days=${days}`),
  tickets: (status = "open", limit = 8, priority = "", team = "") =>
    fetchJson<TicketQueueResponse>(
      `/v1/admin/tickets?status=${encodeURIComponent(status)}&limit=${limit}&offset=0` +
        (priority ? `&priority=${encodeURIComponent(priority)}` : "") +
        (team ? `&team=${encodeURIComponent(team)}` : ""),
    ),
  ticket: (id: string) => fetchJson<TicketDetail>(`/v1/admin/tickets/${encodeURIComponent(id)}`),
  ticketSla: (days = 30) => fetchJson<TicketSla>(`/v1/admin/tickets/sla?days=${days}`),
  heartbeatPresence: (id: string) =>
    fetchJson<{ status: string; viewers: string[] }>(
      `/v1/admin/tickets/${encodeURIComponent(id)}/presence`,
      { method: "POST" },
    ),
  flags: () => fetchJson<{ flags: FlagRecord[]; overrides_are_ephemeral: boolean }>("/v1/admin/flags"),
  setFlag: (name: string, enabled: boolean) =>
    fetchJson<{ name: string; enabled: boolean; ephemeral: boolean }>(
      `/v1/admin/flags/${encodeURIComponent(name)}?enabled=${enabled}`,
      { method: "PATCH" },
    ),
  overrides: () => fetchJson<{ overrides: AnswerOverride[] }>("/v1/admin/overrides"),
  putOverride: (body: { query: string; reply: string; source_url?: string; enabled?: boolean }) =>
    fetchJson<AnswerOverride>("/v1/admin/overrides", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteOverride: (id: string) =>
    fetchJson<{ ok: boolean }>(`/v1/admin/overrides/${encodeURIComponent(id)}`, { method: "DELETE" }),
  outbox: () =>
    fetchJson<{ items: { id: string; channel: string; provider: string; status: string }[]; live: boolean }>(
      "/v1/admin/outbox",
    ),
  updateTicket: async (id: string, patch: TicketPatch): Promise<{ status: string }> => {
    // The backend takes these as query parameters, not a JSON body.
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(patch)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    const res = await fetch(
      `${BASE}/v1/admin/tickets/${encodeURIComponent(id)}?${params.toString()}`,
      { method: "PATCH", headers: authHeaders(), signal: AbortSignal.timeout(15000) },
    );
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  metrics: () => fetch(`${BASE}/metrics`, { headers: authHeaders() }).then((r) => r.text()),
};
