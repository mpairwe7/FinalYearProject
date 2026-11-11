import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUTH_TOKEN_STORAGE_KEY, clearAuthToken, getAuthSubject, setAuthToken, subscribeAuthToken } from '@/lib/authSession';

function token(sub: string, expiresInSeconds = 600): string {
  const part = (value: object) => btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${part({ alg: 'none' })}.${part({ sub, exp: Math.floor(Date.now() / 1000) + expiresInSeconds })}.sig`;
}

describe('getAuthSubject', () => {
  afterEach(() => {
    clearAuthToken();
    vi.restoreAllMocks();
  });

  it('is the signed-in account, and follows a change of account', () => {
    setAuthToken(token('okello'));
    expect(getAuthSubject()).toBe('okello');
    setAuthToken(token('nakato'));
    expect(getAuthSubject()).toBe('nakato');
  });

  it('is empty for a key that is not a token, and when nobody is signed in', () => {
    setAuthToken('an-operator-key');
    expect(getAuthSubject()).toBe('');
    clearAuthToken();
    expect(getAuthSubject()).toBe('');
  });

  it('reads an expired token as nobody, without changing anything while React renders', () => {
    // It is a render-time snapshot: removing the token or notifying
    // subscribers here would update state in the middle of a render.
    window.localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token('okello', -60));
    const onChange = vi.fn();
    const unsubscribe = subscribeAuthToken(onChange);
    expect(getAuthSubject()).toBe('');
    expect(onChange).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)).not.toBeNull();
    unsubscribe();
  });

  it('decodes a token once, not on every render', () => {
    setAuthToken(token('okello'));
    getAuthSubject();
    const decode = vi.spyOn(window, 'atob');
    getAuthSubject();
    getAuthSubject();
    expect(decode).not.toHaveBeenCalled();
  });
});
