/**
 * Client-side analytics store.
 *
 * Tracks sessions, page views, and user engagement events.
 * Sends events to the backend /v1/analytics/event endpoint.
 * Self-hosted — no third-party analytics services required.
 *
 * Features:
 * - Persistent session ID (survives page refreshes within a browser tab)
 * - Offline-resilient event queue with localStorage fallback
 * - Beacon API for reliable session_end on page unload
 * - Timeout on all fetch calls (10s) to prevent UI freezes
 */

import { hasAnalyticsConsent } from "../lib/analyticsConsent";
import { authHeaders } from "../lib/authSession";

const API_URL = '/api';
const QUEUE_KEY = 'ura_analytics_queue';
const FETCH_TIMEOUT_MS = 10_000;

// Persistent session ID (survives page refreshes within a browser tab)
function getSessionId(): string {
  if (typeof window === 'undefined') return '';
  let id = sessionStorage.getItem('ura_session_id');
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem('ura_session_id', id);
  }
  return id;
}

// Track session start time
const sessionStartTime = Date.now();

// ---------------------------------------------------------------------------
// Offline queue (localStorage-backed)
// ---------------------------------------------------------------------------
function enqueueEvent(payload: Record<string, unknown>): void {
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    const queue: Record<string, unknown>[] = raw ? JSON.parse(raw) : [];
    queue.push(payload);
    // Cap queue at 100 to prevent storage bloat
    if (queue.length > 100) queue.splice(0, queue.length - 100);
    localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
  } catch {
    // localStorage unavailable or full — drop silently
  }
}

function flushQueue(): void {
  if (!hasAnalyticsConsent()) return;
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    if (!raw) return;
    const queue: Record<string, unknown>[] = JSON.parse(raw);
    localStorage.removeItem(QUEUE_KEY);
    for (const payload of queue) {
      fetchWithTimeout(`${API_URL}/v1/analytics/event`, {
        method: 'POST',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      }).catch(() => {});
    }
  } catch {
    // Ignore flush errors
  }
}

export function clearAnalyticsQueue(): void {
  try {
    localStorage.removeItem(QUEUE_KEY);
  } catch {
    // localStorage unavailable — ignore
  }
}

// ---------------------------------------------------------------------------
// Fetch with timeout helper
// ---------------------------------------------------------------------------
async function fetchWithTimeout(url: string, init: RequestInit): Promise<globalThis.Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

/**
 * Send an analytics event to the backend (fire-and-forget).
 * Falls back to localStorage queue on network failure.
 */
export function trackEvent(eventType: string, data: Record<string, unknown> = {}): void {
  if (!hasAnalyticsConsent()) return;
  const sessionId = getSessionId();
  if (!sessionId) return;

  const payload = {
    event_type: eventType,
    event_data: {
      ...data,
      timestamp: Date.now(),
      session_duration_ms: Date.now() - sessionStartTime,
      url: typeof window !== 'undefined' ? window.location.pathname : '',
    },
    session_id: sessionId,
  };

  fetchWithTimeout(`${API_URL}/v1/analytics/event`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json', 'X-Session-ID': sessionId }),
    body: JSON.stringify(payload),
  }).catch(() => {
    enqueueEvent(payload);
  });
}

/**
 * Where a rated reply came from, and which answer of which conversation it is.
 *
 * `retrievalMode`, `workflowId` and `stepId` let the analytics journey panel
 * show which journey step taxpayers found least helpful; the API rejects
 * anything but lowercase [a-z0-9_] there. `conversationId`, `turnId` (the
 * chat response's `turn_id`) and `answerNumber` let the service-quality audit
 * read a conversation's ratings in order and find where it went wrong.
 */
export interface FeedbackContext {
  retrievalMode?: string;
  workflowId?: string;
  stepId?: string;
  conversationId?: string | null;
  turnId?: string;
  answerNumber?: number;
}

const CONTEXT_ID = /^[a-z0-9_]{1,64}$/;
const THREAD_ID = /^[a-zA-Z0-9_-]{1,64}$/;

function contextField(value: string | undefined): string {
  return value && CONTEXT_ID.test(value) ? value : '';
}

function threadField(value: string | null | undefined): string {
  return value && THREAD_ID.test(value) ? value : '';
}

/**
 * Rate one answer, 👍 or 👎.
 *
 * Sent whether or not analytics is allowed: a rating is about the answer.
 * Without analytics consent it goes without the analytics session id, and the
 * API stores it anonymously — the answer is rated, the rater is not recorded.
 */
export async function submitFeedback(
  messageId: string,
  rating: 'up' | 'down',
  comment: string = '',
  userQuery: string = '',
  botReply: string = '',
  context: FeedbackContext = {},
): Promise<{ id: string } | null> {
  const sessionId = hasAnalyticsConsent() ? getSessionId() : '';
  const answerNumber =
    context.answerNumber && Number.isInteger(context.answerNumber) && context.answerNumber > 0
      ? Math.min(context.answerNumber, 1000)
      : null;
  try {
    const res = await fetchWithTimeout(`${API_URL}/v1/feedback`, {
      method: 'POST',
      headers: authHeaders({
        'Content-Type': 'application/json',
        ...(sessionId ? { 'X-Session-ID': sessionId } : {}),
      }),
      body: JSON.stringify({
        message_id: messageId,
        rating,
        comment,
        session_id: sessionId || null,
        user_query: userQuery,
        bot_reply: botReply,
        // A malformed identifier would make the API refuse the whole rating,
        // so an unexpected value is dropped here rather than sent.
        retrieval_mode: contextField(context.retrievalMode),
        workflow_id: contextField(context.workflowId),
        step_id: contextField(context.stepId),
        conversation_id: threadField(context.conversationId),
        turn_id: threadField(context.turnId),
        turn_index: answerNumber,
      }),
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

/**
 * Update comment on existing feedback (avoids duplicate entries).
 */
export async function updateFeedbackComment(
  messageId: string,
  comment: string,
): Promise<boolean> {
  const sessionId = hasAnalyticsConsent() ? getSessionId() : '';
  try {
    const res = await fetchWithTimeout(`${API_URL}/v1/feedback/${encodeURIComponent(messageId)}/comment`, {
      method: 'PATCH',
      headers: authHeaders({
        'Content-Type': 'application/json',
        ...(sessionId ? { 'X-Session-ID': sessionId } : {}),
      }),
      body: JSON.stringify({ comment }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/**
 * Get the current session ID for API request headers.
 */
export function getAnalyticsSessionId(): string {
  return getSessionId();
}

// --- Lifecycle events ---

let _analyticsInitialised = false;

/**
 * Initialise analytics tracking. Call once on app mount.
 * Safe to call multiple times (idempotent).
 */
export function initAnalytics(): void {
  if (!hasAnalyticsConsent()) return;
  if (_analyticsInitialised) return;
  _analyticsInitialised = true;

  // Flush any queued events from previous sessions
  flushQueue();

  trackEvent('session_start', {
    referrer: typeof document !== 'undefined' ? document.referrer : '',
    screen_width: typeof window !== 'undefined' ? window.screen.width : 0,
    screen_height: typeof window !== 'undefined' ? window.screen.height : 0,
    user_agent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
    platform: typeof navigator !== 'undefined' ? (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform : '',
  });

  if (typeof document !== 'undefined') {
    // Track page visibility changes (engagement)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        trackEvent('page_hidden', { session_duration_ms: Date.now() - sessionStartTime });
      } else {
        trackEvent('page_visible');
      }
    });

    // Use Beacon API for reliable session_end on page unload
    window.addEventListener('beforeunload', () => {
      const sessionId = getSessionId();
      const payload = JSON.stringify({
        event_type: 'session_end',
        event_data: { session_duration_ms: Date.now() - sessionStartTime, timestamp: Date.now() },
        session_id: sessionId,
      });
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          `${API_URL}/v1/analytics/event`,
          new Blob([payload], { type: 'application/json' }),
        );
      }
    });
  }
}

// Convenience event helpers
export const trackChatSent = (messageLength: number) =>
  trackEvent('chat_sent', { message_length: messageLength });

export const trackChatReceived = (responseTimeMs: number, hasSources: boolean) =>
  trackEvent('chat_received', { response_time_ms: responseTimeMs, has_sources: hasSources });

export const trackVoiceUsed = () => trackEvent('voice_input_used');

export const trackStarterPromptUsed = (prompt: string) =>
  trackEvent('starter_prompt_used', { prompt });

export const trackFeedbackGiven = (rating: 'up' | 'down') =>
  trackEvent('feedback_given', { rating });

export const trackErrorOccurred = (errorType: string) =>
  trackEvent('error_occurred', { error_type: errorType });
