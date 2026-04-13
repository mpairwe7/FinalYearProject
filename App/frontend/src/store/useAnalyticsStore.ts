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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
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
  try {
    const raw = localStorage.getItem(QUEUE_KEY);
    if (!raw) return;
    const queue: Record<string, unknown>[] = JSON.parse(raw);
    localStorage.removeItem(QUEUE_KEY);
    for (const payload of queue) {
      fetchWithTimeout(`${API_URL}/v1/analytics/event`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).catch(() => {});
    }
  } catch {
    // Ignore flush errors
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
    headers: { 'Content-Type': 'application/json', 'X-Session-ID': sessionId },
    body: JSON.stringify(payload),
  }).catch(() => {
    enqueueEvent(payload);
  });
}

/**
 * Submit feedback for a specific bot message.
 */
export async function submitFeedback(
  messageId: string,
  rating: 'up' | 'down',
  comment: string = '',
  userQuery: string = '',
  botReply: string = '',
): Promise<{ id: string } | null> {
  const sessionId = getSessionId();
  try {
    const res = await fetchWithTimeout(`${API_URL}/v1/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-ID': sessionId },
      body: JSON.stringify({
        message_id: messageId,
        rating,
        comment,
        session_id: sessionId,
        user_query: userQuery,
        bot_reply: botReply,
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
  try {
    const res = await fetchWithTimeout(`${API_URL}/v1/feedback/${encodeURIComponent(messageId)}/comment`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-Session-ID': getSessionId() },
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
