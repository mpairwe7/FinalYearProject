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
  user_query_en?: string;
  locale?: string;
  modality?: "text" | "voice";
  bot_reply: string;
  created_at: number;
  updated_at: number;
  /** Owning team, routed from the handoff topic at escalation time. */
  team?: string;
  assignee?: string;
  first_response_at?: number;
  reply_at?: number;
  officer_reply?: string;
  officer_reply_localized?: string;
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
  user_message_en?: string;
  bot_reply: string;
  created_at: number;
  sources?: string[];
  topic_tag?: string;
  /** A follow-up the taxpayer sent after escalating (empty `bot_reply`). The
   *  live chat itself is `messages` on the ticket. */
  sender?: "taxpayer";
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
  officer_reply_localized?: string;
  /** Internal. Never reaches the taxpayer. */
  staff_note?: string;
  assignee?: string;
  session_id?: string;
  first_response_at?: number;
  resolved_at?: number;
  reply_delivered_at?: number;
  /** The live chat with the taxpayer, in order (taxpayer, officer, system). */
  messages?: TicketChatMessage[];
  /** Where that chat stands, who has it, and how the taxpayer rated it. */
  chat?: TicketChatInfo;
}

/** One message of the live chat, as staff read it (English, and as delivered). */
export interface TicketChatMessage {
  seq: number;
  sender: "taxpayer" | "officer" | "system";
  text: string;
  text_localized?: string;
  officer_name?: string;
  created_at: number;
}

export type OfficerChatState = "waiting" | "officer" | "ended" | "none";

export interface TicketChatInfo {
  state: OfficerChatState;
  officer_name: string;
  ended_at?: number | null;
  ended_by_name?: string;
  outcome?: "" | "resolved" | "wontfix";
  rating?: { status: RatingStatus; score: number | null; comment: string } | null;
}

/** The taxpayer's poll of their live chat (`GET /v1/escalate/{id}/messages`). */
export interface OfficerChatThread {
  ticket_id: string;
  chat_state: OfficerChatState;
  officer_name: string;
  messages: Array<{
    seq: number;
    sender: "taxpayer" | "officer" | "system";
    /** An officer's message in the taxpayer's language when it was translated. */
    text: string;
    officer_name?: string;
    created_at: number;
  }>;
  last_seq: number;
  csat: { requested: boolean; submitted: boolean };
}

export interface EndChatBody {
  closing_message?: string;
  closing_message_localized?: string;
  outcome: "resolved" | "wontfix";
}

export interface EscalationCaseDetail {
  ok: boolean;
  ticket_id: string;
  reference: string;
  status: string;
  status_label: string;
  priority: string;
  team: string;
  team_label: string;
  assignee: string;
  assignee_display: string;
  reason: string;
  user_query: string;
  user_query_en?: string;
  locale?: string;
  modality?: string;
  officer_reply: string;
  officer_reply_localized?: string;
  reply_at: number;
  reply_delivered: boolean;
  created_at: number;
  resolved_at: number;
  transcript: Array<{
    user_message?: string;
    user_message_en?: string;
    bot_reply?: string;
    created_at?: number;
    sender?: string;
  }>;
  can_reply: boolean;
}

export interface TicketSla {
  period_days: number;
  tickets: number;
  responded: number;
  resolved: number;
  awaiting_first_response: number;
  awaiting_next_response?: number;
  /** Live open+assigned cases with nobody on them. Server-side, so it is not
   *  bounded by whatever page of the queue a caller happens to have loaded. */
  unassigned?: number;
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
  officer_reply_localized?: string;
  locale?: string;
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

export interface DiscrepancyRecord {
  id: string;
  conversation_id: string;
  session_id: string;
  user_id: string;
  status: 'pending' | 'verified' | 'dismissed' | 'resolved';
  priority: 'low' | 'normal' | 'high' | 'urgent';
  discrepancy_type: string;
  bot_statement: string;
  user_correction: string;
  cited_sources: string[];
  frequency_count: number;
  dedup_key: string;
  admin_note: string;
  verified_override_id: string;
  created_at: number;
  updated_at: number;
}

export interface DiscrepancyStats {
  total: number;
  pending: number;
  verified: number;
  dismissed: number;
}

export interface VerifyDiscrepancyPayload {
  admin_note?: string;
  create_override?: boolean;
  override_query?: string;
  override_reply?: string;
  source_url?: string;
  precedence_topic?: string;
  precedence_rule?: string;
  statute_reference?: string;
  tombstone_sources?: string[];
  tombstone_chunk_ids?: string[];
}

export interface CorpusTombstone {
  id: string;
  source_uri: string;
  chunk_id: string;
  reason: string;
  superseded_by: string;
  created_at: number;
  created_by: string;
}

export interface OutboxItem {
  id: string;
  user_id: string;
  channel: string;
  provider: string;
  payload?: Record<string, unknown>;
  status: string;
  created_at: number;
  sent_at?: number | null;
  delivered_at?: number | null;
  error?: string;
  provider_msg_id?: string;
  retries?: number;
  live?: boolean;
}

export interface OutboxResponse {
  items: OutboxItem[];
  live: boolean;
  providers?: Record<string, { configured: boolean; backend?: string; status: string; live?: boolean }>;
  stats?: {
    total: number;
    queued: number;
    sent: number;
    delivered: number;
    failed: number;
  };
}

export interface StatutoryPrecedence {
  id: string;
  topic: string;
  rule_statement: string;
  statute_reference: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

/** GET /v1/analytics/journeys — one row per guided journey. */
export interface JourneyStepStats {
  step_id: string;
  title: string;
  /** Journeys cancelled or abandoned while waiting on this step. */
  stopped: number;
  helpful: number;
  not_helpful: number;
}

export interface JourneyStats {
  workflow_id: string;
  name: string;
  started: number;
  completed: number;
  cancelled: number;
  abandoned: number;
  in_progress: number;
  completion_pct: number;
  steps: JourneyStepStats[];
}

export interface JourneyFunnel {
  period_days: number;
  abandon_after_hours: number;
  journeys: JourneyStats[];
}

/** GET /v1/admin/audit/events — one row of the tamper-evident audit trail. */
export interface AuditEvent {
  seq: number;
  event_id: string;
  event_type: string;
  /** The acting user's id; "operator-key" for the break-glass key. */
  actor: string;
  ts: number;
  payload: Record<string, unknown>;
  row_hash: string;
}

export interface AuditEventsPage {
  /** Whether this deployment records new events (the audit_ledger flag). */
  ledger_enabled: boolean;
  events: AuditEvent[];
  next_before_seq: number | null;
}

/** A seal: the Merkle root of a range of events and the chain head at its end. */
export interface AuditAnchor {
  anchor_id: string;
  first_seq: number;
  last_seq: number;
  merkle_root: string;
  /** Empty on seals made before seals recorded the chain head. */
  head_hash: string;
  created_at: number;
}

export interface AuditVerification {
  ledger_enabled: boolean;
  valid: boolean;
  /** "full" walked every event and seal; "since_seal" the newest seal and what follows it. */
  scope: "full" | "since_seal";
  rows_checked: number;
  first_seq: number;
  last_seq: number;
  head_hash: string;
  breaks: { seq: number; event_id: string; reason: string }[];
  anchors_checked: number;
  anchor_breaks: { anchor_id: string; first_seq: number; last_seq: number; reason: string }[];
  latest_anchor: AuditAnchor | null;
  /** Events written after the newest seal. */
  unsealed_rows: number;
  verified_at: number;
}

export interface AuditSealResult {
  /** False when there was nothing new to seal. */
  sealed: boolean;
  anchor: AuditAnchor | null;
}

export interface AuditQuery {
  eventType?: string;
  actor?: string;
  since?: number;
  beforeSeq?: number | null;
  limit?: number;
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

/* ── Service-quality audit (GET /v1/admin/quality/*) ────────────────────── */

/** How a rated conversation went, worst first. See app/quality_audit.py. */
export type QualityPattern = "went_wrong" | "never_helpful" | "recovered" | "all_helpful";

export interface QualityBreakdownRow {
  key: string;
  rated: number;
  helpful: number;
  unhelpful: number;
  helpful_pct: number | null;
}

export interface AiChatSummary {
  period_days: number;
  /** More rated answers than one period reads; the figures are a sample. */
  truncated: boolean;
  rated_answers: number;
  helpful: number;
  unhelpful: number;
  helpful_pct: number | null;
  comments: number;
  conversations: number;
  /** Ratings from before they were linked to a conversation. */
  unlinked_answers: number;
  patterns: Record<QualityPattern, number>;
  median_break_answer: number | null;
  by_position: { position: string; rated: number; helpful: number; helpful_pct: number | null }[];
  by_topic: QualityBreakdownRow[];
  by_route: QualityBreakdownRow[];
  by_language: QualityBreakdownRow[];
  by_day: { day: string; rated: number; helpful: number; helpful_pct: number | null }[];
  previous: { rated_answers: number; helpful_pct: number | null; truncated: boolean };
}

export interface RatedConversation {
  conversation_id: string | null;
  key: string;
  linked: boolean;
  pattern: QualityPattern;
  ratings: { answer: number | null; rating: "up" | "down" }[];
  rated_answers: number;
  unhelpful: number;
  break_answer: number | null;
  break_question: string;
  helpful_before_break: boolean;
  comments: number;
  topic: string;
  locale: string;
  first_rated_at: number;
  last_rated_at: number;
}

export interface AiChatPage {
  period_days: number;
  truncated: boolean;
  total: number;
  limit: number;
  offset: number;
  conversations: RatedConversation[];
}

export interface AiChatTimelineAnswer {
  answer: number | null;
  turn_id: string;
  question: string;
  reply: string;
  created_at: number | null;
  sources: string[];
  confidence: number | null;
  topic: string;
  rating: "up" | "down" | null;
  comment: string;
  retrieval_mode: string;
  is_break: boolean;
}

export interface AiChatTimeline {
  conversation_id: string;
  pattern: QualityPattern;
  break_answer: number | null;
  /** False once the conversation log has expired: only rated answers remain. */
  transcript_complete: boolean;
  transcript_kept_days: number;
  answers: AiChatTimelineAnswer[];
}

export interface QualitySummary {
  period_days: number;
  ai_chat: AiChatSummary;
  officer_chat?: RatingsHeadline;
  /** The caller's spoken review of the AI, at goodbye and while transferring. */
  ai_calls?: { end: RatingsHeadline; transfer: RatingsHeadline };
  officer_calls?: RatingsHeadline;
}

export type RatingSentiment = "positive" | "neutral" | "negative" | "unclear";

export type RatingSource =
  | "chat_officer_csat"
  | "call_officer_csat"
  | "call_ai_review_end"
  | "call_ai_review_transfer";

export type RatingStatus = "pending" | "answered" | "skipped" | "no_answer";

/** The figures every satisfaction stream shares. */
export interface RatingsFigures {
  asked: number;
  answered: number;
  response_rate: number | null;
  average: number | null;
  /** Share of answered ratings that were 4 or 5. */
  csat_pct: number | null;
  /** Ratings of 1 or 2. */
  low: number;
  distribution: Record<"1" | "2" | "3" | "4" | "5", number>;
  sentiment: Record<RatingSentiment, number>;
  /** Share of answered ratings that were positive / negative (the words view). */
  positive_pct: number | null;
  negative_pct: number | null;
}

/** The period before, for the headline's comparison. */
export interface RatingsPrevious {
  asked: number;
  answered: number;
  response_rate: number | null;
  average: number | null;
  csat_pct: number | null;
  positive_pct: number | null;
  negative_pct: number | null;
}

/** One week, or one language: how many were rated and how they came out. */
export interface RatingsPeriodFigures {
  rated: number;
  /** Of the 1–5 levels; null for a stream rated in words. */
  average: number | null;
  csat_pct: number | null;
  /** Share of answered ratings that were positive / negative. */
  positive_pct: number | null;
  negative_pct: number | null;
}

export interface RatingsHeadline extends RatingsFigures {
  truncated: boolean;
  previous: RatingsPrevious;
}

export interface OfficerRatingRow {
  officer_id: string;
  officer_name: string;
  handled: number;
  rated: number;
  average: number | null;
  csat_pct: number | null;
  low: number;
}

export interface ServiceRating {
  id: string;
  source: RatingSource;
  channel: string;
  subject: string;
  status: RatingStatus;
  score: number | null;
  sentiment: string;
  comment: string;
  ticket_id: string;
  call_id: string;
  conversation_id: string;
  officer_id: string;
  officer_name: string;
  team: string;
  topic: string;
  locale: string;
  created_at: number;
  answered_at: number | null;
}

export interface RatingsAudit {
  source: RatingSource;
  period_days: number;
  truncated: boolean;
  summary: RatingsFigures & {
    skipped: number;
    no_answer: number;
    pending: number;
    by_officer: OfficerRatingRow[];
    by_week: Array<RatingsPeriodFigures & { week: string }>;
    by_language: Array<RatingsPeriodFigures & { locale: string; asked: number; response_rate: number | null }>;
  };
  previous: RatingsPrevious;
  total: number;
  limit: number;
  offset: number;
  ratings: ServiceRating[];
}

export interface RatingsQuery {
  source?: RatingSource;
  days?: number;
  score?: number | null;
  officer?: string;
  status?: "" | "answered" | "skipped" | "no_answer";
  sentiment?: "" | RatingSentiment;
  limit?: number;
  offset?: number;
}

export interface AiChatQuery {
  days?: number;
  pattern?: QualityPattern | "";
  topic?: string;
  locale?: string;
  withComment?: boolean;
  limit?: number;
  offset?: number;
}

const BASE = "/api";

async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const timeoutSignal = AbortSignal.timeout(15000);
  const signal = init.signal && typeof AbortSignal.any === "function"
    ? AbortSignal.any([init.signal, timeoutSignal])
    : (init.signal || timeoutSignal);

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

export const analyticsApi = {
  dashboard: (days = 30) => fetchJson<DashboardData>(`/v1/analytics/dashboard?days=${days}`),
  feedbackSummary: (days = 30) => fetchJson<FeedbackSummary>(`/v1/feedback/summary?days=${days}`),
  journeys: (days = 30) => fetchJson<JourneyFunnel>(`/v1/analytics/journeys?days=${days}`),
  ticketStats: (days = 30) => fetchJson<TicketStats>(`/v1/admin/tickets/stats?days=${days}`),
  tickets: (status = "open", limit = 8, priority = "", team = "", q?: string, locale?: string, modality?: string) =>
    fetchJson<TicketQueueResponse>(
      // "any" is a UI token; the API takes an absent status to mean all
      // statuses and 400s on anything outside the four real ones.
      `/v1/admin/tickets?status=${encodeURIComponent(status === "any" ? "" : status)}` +
        `&limit=${limit}&offset=0` +
        (priority ? `&priority=${encodeURIComponent(priority)}` : "") +
        (team ? `&team=${encodeURIComponent(team)}` : "") +
        (locale ? `&locale=${encodeURIComponent(locale)}` : "") +
        (modality ? `&modality=${encodeURIComponent(modality)}` : "") +
        (q ? `&q=${encodeURIComponent(q)}` : ""),
    ),
  translate: (text: string, sourceLang = "en", targetLang = "lg") =>
    fetchJson<{
      text: string;
      source_lang: string;
      target_lang: string;
      latency_s: number;
      backend: string;
      error?: string | null;
      figures_survived?: boolean;
    }>("/v1/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, source_lang: sourceLang, target_lang: targetLang }),
    }),
  publicTicketStatus: (ticketId: string) =>
    fetchJson<EscalationCaseDetail>(`/v1/escalate/${encodeURIComponent(ticketId)}`),
  replyToPublicTicket: (ticketId: string, message: string, locale = "en") =>
    fetchJson<{ ok: boolean; ticket_id: string; status: string; message: string; seq?: number }>(
      `/v1/escalate/${encodeURIComponent(ticketId)}/reply`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, locale }),
      },
    ),
  /** The taxpayer's live chat with the officer: messages after *afterSeq*, its state, the rating. */
  publicTicketMessages: (ticketId: string, afterSeq = 0) =>
    fetchJson<OfficerChatThread>(
      `/v1/escalate/${encodeURIComponent(ticketId)}/messages?after_seq=${Math.max(0, Math.floor(afterSeq))}`,
    ),
  /** The taxpayer rates the chat an officer ended: 1–5, or null to skip. */
  submitTicketCsat: (ticketId: string, score: number | null, comment = "") =>
    fetchJson<{ ok: boolean; ticket_id: string; status: "answered" | "skipped" }>(
      `/v1/escalate/${encodeURIComponent(ticketId)}/csat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ score, comment }),
      },
    ),
  /** An officer ends a live chat (officers only). */
  endChat: (ticketId: string, body: EndChatBody) =>
    fetchJson<{ status: string; ticket_id: string; chat_state: OfficerChatState }>(
      `/v1/admin/tickets/${encodeURIComponent(ticketId)}/end-chat`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
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
  /** Drop the override so the flag follows FLAG_* / its default again.
   *  Setting it back to the default value does not: it stays overridden. */
  clearFlag: (name: string) =>
    fetchJson<{ name: string; enabled: boolean; overridden: boolean }>(
      `/v1/admin/flags/${encodeURIComponent(name)}`,
      { method: "DELETE" },
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
  discrepancies: (params?: { status?: string; priority?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set("status", params.status);
    if (params?.priority) q.set("priority", params.priority);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return fetchJson<{ discrepancies: DiscrepancyRecord[]; stats: DiscrepancyStats }>(
      `/v1/admin/discrepancies${qs ? `?${qs}` : ""}`,
    );
  },
  verifyDiscrepancy: (id: string, payload: VerifyDiscrepancyPayload) =>
    fetchJson<{ ok: boolean; report_id: string; status: string; override_id?: string }>(
      `/v1/admin/discrepancies/${encodeURIComponent(id)}/verify`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
    ),
  dismissDiscrepancy: (id: string, payload: { admin_note?: string; reason?: string }) =>
    fetchJson<{ ok: boolean; report_id: string; status: string }>(
      `/v1/admin/discrepancies/${encodeURIComponent(id)}/dismiss`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) },
    ),
  tombstones: () => fetchJson<{ tombstones: CorpusTombstone[] }>("/v1/admin/tombstones"),
  addTombstone: (payload: { source_uri: string; chunk_id?: string; reason?: string }) =>
    fetchJson<CorpusTombstone>("/v1/admin/tombstones", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deleteTombstone: (id: string) =>
    fetchJson<{ ok: boolean }>(`/v1/admin/tombstones/${encodeURIComponent(id)}`, { method: "DELETE" }),
  precedences: () => fetchJson<{ precedences: StatutoryPrecedence[] }>("/v1/admin/precedences"),
  addPrecedence: (payload: { topic: string; rule_statement: string; statute_reference?: string; enabled?: boolean }) =>
    fetchJson<StatutoryPrecedence>("/v1/admin/precedences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  deletePrecedence: (id: string) =>
    fetchJson<{ ok: boolean }>(`/v1/admin/precedences/${encodeURIComponent(id)}`, { method: "DELETE" }),
  outbox: () => fetchJson<OutboxResponse>("/v1/admin/outbox"),
  dispatchOutbox: () =>
    fetchJson<{ ok: boolean; processed: number; sent: number; failed: number }>("/v1/admin/outbox/dispatch", {
      method: "POST",
    }),
  retryOutbox: (id: string) =>
    fetchJson<{ ok: boolean; status: string; provider?: string }>(`/v1/admin/outbox/${encodeURIComponent(id)}/retry`, {
      method: "POST",
    }),
  testNotification: (payload: { channel: string; recipient: string; message: string; subject?: string }) =>
    fetchJson<{ ok: boolean; provider: string; status: string; id: string; provider_msg_id?: string }>("/v1/admin/outbox/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  auditEvents: ({ eventType = "", actor = "", since, beforeSeq, limit = 50 }: AuditQuery = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (eventType) params.set("event_type", eventType);
    if (actor) params.set("actor", actor);
    if (since != null) params.set("since", String(Math.floor(since)));
    if (beforeSeq != null) params.set("before_seq", String(beforeSeq));
    return fetchJson<AuditEventsPage>(`/v1/admin/audit/events?${params.toString()}`);
  },
  auditVerify: () => fetchJson<AuditVerification>("/v1/admin/audit/verify"),
  qualitySummary: (days = 30) => fetchJson<QualitySummary>(`/v1/admin/quality/summary?days=${days}`),
  qualityAiChat: ({ days = 30, pattern = "", topic = "", locale = "", withComment = false, limit = 25, offset = 0 }: AiChatQuery = {}) => {
    const params = new URLSearchParams({ days: String(days), limit: String(limit), offset: String(offset) });
    if (pattern) params.set("pattern", pattern);
    if (topic) params.set("topic", topic);
    if (locale) params.set("locale", locale);
    if (withComment) params.set("with_comment", "true");
    return fetchJson<{ summary: AiChatSummary; page: AiChatPage }>(`/v1/admin/quality/ai-chat?${params.toString()}`);
  },
  qualityAiChatTimeline: (conversationId: string) =>
    fetchJson<AiChatTimeline>(`/v1/admin/quality/ai-chat/${encodeURIComponent(conversationId)}`),
  qualityRatings: ({
    source = "chat_officer_csat",
    days = 30,
    score = null,
    officer = "",
    status = "",
    sentiment = "",
    limit = 25,
    offset = 0,
  }: RatingsQuery = {}) => {
    const params = new URLSearchParams({ source, days: String(days), limit: String(limit), offset: String(offset) });
    if (score != null) params.set("score", String(score));
    if (officer) params.set("officer", officer);
    if (status) params.set("status", status);
    if (sentiment) params.set("sentiment", sentiment);
    return fetchJson<RatingsAudit>(`/v1/admin/quality/ratings?${params.toString()}`);
  },
  auditSeal: () => fetchJson<AuditSealResult>("/v1/admin/audit/seal", { method: "POST" }),
  updateTicket: async (id: string, patch: TicketPatch): Promise<{ status: string }> => {
    const res = await fetch(
      `${BASE}/v1/admin/tickets/${encodeURIComponent(id)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify(patch),
        signal: AbortSignal.timeout(15000),
      },
    );
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  },
  metrics: () => fetch(`${BASE}/metrics`, { headers: authHeaders() }).then((r) => r.text()),
};
