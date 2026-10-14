/**
 * The chat session: what ties a signed-out taxpayer's server-side history to
 * this browser. Their attachments and an escalation's transcript use it too.
 *
 * It used to be the analytics session id, which lives in sessionStorage. A new
 * tab, or the same tab after a restart, began a new session, so a conversation
 * reopened from the sidebar went on without its server-side history. The page
 * still showed the thread the assistant had forgotten (G122).
 *
 * This id lives in localStorage beside the conversations. Each use moves its
 * expiry forward. It is replaced after `CONVERSATION_TTL_DAYS` without use,
 * when the server has already deleted everything bound to it (the backend's
 * `CONVERSATION_TTL_DAYS`, 7 by default), and whenever the taxpayer clears
 * their chat history. Analytics keep their own per-tab id. A signed-in
 * taxpayer's history follows their account, not this id.
 */

const CHAT_SESSION_KEY = 'ura_chat_session';
const DAY_MS = 86_400_000;
const ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/** How long the server keeps a conversation; the browser keeps them no longer. */
export const CONVERSATION_TTL_DAYS = (() => {
  const raw = Number(process.env.NEXT_PUBLIC_CONVERSATION_TTL_DAYS);
  return Number.isFinite(raw) && raw > 0 ? raw : 7;
})();

export function conversationTtlMs(): number {
  return CONVERSATION_TTL_DAYS * DAY_MS;
}

interface StoredSession {
  id: string;
  lastUsedAt: number;
}

// Used when storage is blocked (a private window, or site data disabled): the
// session then lasts as long as the page, as the analytics id would have.
let pageSession: StoredSession | null = null;

function storage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function readStored(): StoredSession | null {
  try {
    const raw = storage()?.getItem(CHAT_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredSession>;
    if (typeof parsed.id !== 'string' || !ID_PATTERN.test(parsed.id)) return null;
    if (typeof parsed.lastUsedAt !== 'number' || !Number.isFinite(parsed.lastUsedAt)) return null;
    return { id: parsed.id, lastUsedAt: parsed.lastUsedAt };
  } catch {
    return null;
  }
}

function writeStored(session: StoredSession): void {
  try {
    storage()?.setItem(CHAT_SESSION_KEY, JSON.stringify(session));
  } catch {
    // Storage full or blocked: pageSession still holds it for this page.
  }
}

/**
 * The chat session id to send as `X-Session-ID` on chat-bound requests, and as
 * `chat_session` when a call starts from the chat. Empty during server rendering.
 */
export function getChatSessionId(now: number = Date.now()): string {
  if (typeof window === 'undefined') return '';
  const current = readStored() ?? pageSession;
  const live = current && now - current.lastUsedAt < conversationTtlMs() ? current : null;
  const session = { id: live?.id ?? crypto.randomUUID(), lastUsedAt: now };
  pageSession = session;
  writeStored(session);
  return session.id;
}

/** Start a new chat session: the old one's history can no longer be resumed here. */
export function resetChatSession(): void {
  pageSession = null;
  try {
    storage()?.removeItem(CHAT_SESSION_KEY);
  } catch {
    // Nothing stored to remove.
  }
}
