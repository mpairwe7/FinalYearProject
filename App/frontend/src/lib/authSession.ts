const DEFAULT_AUTH_TOKEN_KEY = 'ura_auth_token';

export const AUTH_TOKEN_STORAGE_KEY =
  process.env.NEXT_PUBLIC_AUTH_TOKEN_STORAGE_KEY || DEFAULT_AUTH_TOKEN_KEY;

/**
 * How the current token was obtained.
 *
 * Recorded because signing out has to do different things for each. An OIDC
 * token means there is a session at the identity provider that must ALSO be
 * ended — without that, sign-out is a local gesture and the next sign-in is
 * answered silently from the surviving provider cookie, which is what made
 * "sign in as another user" hand back the previous account. A dev token has no
 * provider behind it, so redirecting anywhere would be nonsense.
 */
export type AuthMethod = 'oidc' | 'dev';

const AUTH_METHOD_STORAGE_KEY = `${AUTH_TOKEN_STORAGE_KEY}_method`;

export function getAuthMethod(): AuthMethod | null {
  if (typeof window === 'undefined') return null;
  try {
    const value = window.localStorage.getItem(AUTH_METHOD_STORAGE_KEY);
    return value === 'oidc' || value === 'dev' ? value : null;
  } catch {
    return null;
  }
}

export function getAuthToken(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

/**
 * Subscribers for `useSyncExternalStore`.
 *
 * The token lives in localStorage, which React cannot see. Exposing it as a
 * store lets components read it without a mount effect that calls setState —
 * and it keeps two components (or two tabs) from disagreeing about whether
 * anyone is signed in.
 */
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((listener) => listener());
}

/** Subscribe to sign-in/sign-out, including from another tab. */
export function subscribeAuthToken(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (event: StorageEvent) => {
    // A null key means the whole store was cleared.
    if (event.key === null || event.key === AUTH_TOKEN_STORAGE_KEY) onChange();
  };
  if (typeof window !== 'undefined') window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(onChange);
    if (typeof window !== 'undefined') window.removeEventListener('storage', onStorage);
  };
}

/** Server snapshot for `useSyncExternalStore` — no token exists during SSR. */
export function getServerAuthToken(): string {
  return '';
}

export function setAuthToken(token: string, method?: AuthMethod): void {
  if (typeof window === 'undefined') return;
  try {
    if (token) {
      window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
      if (method) window.localStorage.setItem(AUTH_METHOD_STORAGE_KEY, method);
    } else {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
      window.localStorage.removeItem(AUTH_METHOD_STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable in private contexts.
  }
  // Fires for same-tab writes too; the `storage` event only covers other tabs.
  notify();
}

export function clearAuthToken(): void {
  setAuthToken('');
}

export function authHeaders(base: Record<string, string> = {}): Record<string, string> {
  const token = getAuthToken();
  if (!token) return { ...base };
  return { ...base, Authorization: `Bearer ${token}` };
}

export function appendAuthToken(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}access_token=${encodeURIComponent(token)}`;
}
