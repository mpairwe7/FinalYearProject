/**
 * Session-scoped continuation storage for Step-Up Authentication (IETF RFC 9470)
 * and Redundant Entry prevention (W3C WCAG 2.2 AA SC 3.3.7).
 *
 * When an unauthenticated taxpayer triggers a Tier 2/3/4 portal task or clicks
 * an authenticated portal link in chat, their collected fields and conversation
 * ID are persisted in `sessionStorage` for up to 30 minutes across the OIDC
 * sign-in redirect so zero data is re-entered after login.
 */

export const PORTAL_CONTINUATION_STORAGE_KEY = "ura_pending_portal_continuation";
export const PORTAL_CONTINUATION_TTL_MS = 30 * 60 * 1000; // 30 minutes

export interface PendingPortalContinuation {
  conversationId: string;
  taskId: string;
  portalKey: string;
  collectedFields: Record<string, string>;
  returnMode: "chat" | "portal";
  savedAt?: number;
}

export function savePendingPortalContinuation(state: PendingPortalContinuation): void {
  if (typeof window === "undefined") return;
  try {
    const payload: PendingPortalContinuation = {
      ...state,
      savedAt: Date.now(),
    };
    window.sessionStorage.setItem(PORTAL_CONTINUATION_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Ignore storage quota or private-browsing exceptions
  }
}

export function peekPendingPortalContinuation(): PendingPortalContinuation | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(PORTAL_CONTINUATION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingPortalContinuation;
    if (!parsed || typeof parsed !== "object" || !parsed.taskId) {
      window.sessionStorage.removeItem(PORTAL_CONTINUATION_STORAGE_KEY);
      return null;
    }
    if (parsed.savedAt && Date.now() - parsed.savedAt > PORTAL_CONTINUATION_TTL_MS) {
      window.sessionStorage.removeItem(PORTAL_CONTINUATION_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function consumePendingPortalContinuation(): PendingPortalContinuation | null {
  const item = peekPendingPortalContinuation();
  if (typeof window !== "undefined" && item) {
    try {
      window.sessionStorage.removeItem(PORTAL_CONTINUATION_STORAGE_KEY);
    } catch {
      // Ignore
    }
  }
  return item;
}
