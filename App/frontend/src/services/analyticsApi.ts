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
  handoff?: {
    summary?: string;
    topic?: string;
    priority?: string;
    required_details?: string[];
    sources_reviewed?: string[];
  };
  response_judge?: {
    decision?: string;
    final_decision?: string;
    applied_revision?: boolean;
    reasons?: string[];
    confidence_band?: string;
  };
}

export interface TicketQueueResponse {
  count: number;
  status_filter: string;
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

const BASE = "/api";

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: authHeaders(),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const analyticsApi = {
  dashboard: (days = 30) => fetchJson<DashboardData>(`/v1/analytics/dashboard?days=${days}`),
  feedbackSummary: (days = 30) => fetchJson<FeedbackSummary>(`/v1/feedback/summary?days=${days}`),
  ticketStats: (days = 30) => fetchJson<TicketStats>(`/v1/admin/tickets/stats?days=${days}`),
  tickets: (status = "open", limit = 8) =>
    fetchJson<TicketQueueResponse>(
      `/v1/admin/tickets?status=${encodeURIComponent(status)}&limit=${limit}&offset=0`,
    ),
  metrics: () => fetch(`${BASE}/metrics`, { headers: authHeaders() }).then((r) => r.text()),
};
