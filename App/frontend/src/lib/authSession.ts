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

/**
 * Strip a pasted token down to what a JWT can legally contain.
 *
 * `String.prototype.trim()` removes ordinary whitespace and nothing else, so a
 * token copied out of a terminal, a chat client or a PDF can arrive carrying a
 * zero-width space (U+200B), a soft hyphen, a BOM, or an ellipsis from a
 * truncated display. Every one of those is above U+00FF, and the moment such a
 * string reaches an `Authorization` header `fetch` throws
 *
 *     Failed to read the 'headers' property from 'RequestInit':
 *     String contains non ISO-8859-1 code point.
 *
 * which says nothing about the real problem. A JWT is base64url plus two dots,
 * so anything outside that set is not part of the token and can be dropped.
 */
/**
 * Reduce a pasted token to the characters a JWT can legally contain.
 *
 * `String.prototype.trim()` removes ordinary whitespace and nothing else, so a
 * token copied out of a terminal, a chat client or a PDF can arrive carrying a
 * zero-width space, a soft hyphen, a BOM, or an ellipsis from a truncated
 * display. Every one of those is above U+00FF, and the moment such a string
 * reaches an Authorization header `fetch` throws
 *
 *     Failed to read the 'headers' property from 'RequestInit':
 *     String contains non ISO-8859-1 code point.
 *
 * which says nothing about the real problem, and — because the token was
 * already in storage — repeats on every later request rather than just the one
 * that stored it.
 *
 * An allowlist rather than a denylist: a JWT is base64url plus two dots, so
 * anything outside that set cannot be part of the token and is dropped. That
 * cannot miss an invisible character the way a denylist can.
 */
export function sanitizeAuthToken(raw: string): string {
  let out = '';
  // Deliberately NOT normalised first. NFKC maps an ellipsis to three ASCII
  // dots, and a dot is legal in a JWT — so normalising turns a truncated paste
  // ("eyJ…") into something that passes the allowlist and then fails at the
  // server with a far less obvious message. Compatibility folding has nothing
  // to offer a base64url string.
  for (const ch of raw) {
    const cp = ch.codePointAt(0) ?? 0;
    const keep =
      (cp >= 0x30 && cp <= 0x39) || // 0-9
      (cp >= 0x41 && cp <= 0x5a) || // A-Z
      (cp >= 0x61 && cp <= 0x7a) || // a-z
      cp === 0x2d || // -
      cp === 0x5f || // _
      cp === 0x2e;   // .
    if (keep) out += ch;
  }
  return out;
}

/** Shape check only - proves nothing about the signature, which is the server's job. */
export function looksLikeJwt(token: string): boolean {
  const parts = token.split('.');
  return parts.length === 3 && parts[0].length > 0 && parts[1].length > 0;
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
  // Sanitise at the boundary rather than at each call site. A token that has
  // picked up an invisible character breaks not just the request that stored
  // it but every request afterwards, because it is read back out of storage
  // and put straight into a header.
  const clean = token ? sanitizeAuthToken(token) : '';
  try {
    if (clean) {
      window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, clean);
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
