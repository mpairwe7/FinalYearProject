const DEFAULT_AUTH_TOKEN_KEY = 'ura_auth_token';

export const AUTH_TOKEN_STORAGE_KEY =
  process.env.NEXT_PUBLIC_AUTH_TOKEN_STORAGE_KEY || DEFAULT_AUTH_TOKEN_KEY;

export function getAuthToken(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

export function setAuthToken(token: string): void {
  if (typeof window === 'undefined') return;
  try {
    if (token) {
      window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY);
    }
  } catch {
    // Storage can be unavailable in private contexts.
  }
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
