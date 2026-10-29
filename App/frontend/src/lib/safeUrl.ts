/** Parse untrusted server/model URLs before putting them in navigation sinks. */
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const raw = value.trim();
  if (!/^https?:\/\//i.test(raw) || /[\s\\\u0000-\u001f\u007f]/.test(raw)) return null;
  try {
    const url = new URL(raw);
    if (!url.hostname || url.username || url.password) return null;
    return raw;
  } catch {
    return null;
  }
}

/** Taxpayer account actions may navigate only to HTTPS URA-owned hosts. */
export function safePortalUrl(value: unknown): string | null {
  const href = safeHttpUrl(value);
  if (!href) return null;
  const url = new URL(href);
  return url.protocol === 'https:' && (!url.port || url.port === '443') &&
    (url.hostname === 'ura.go.ug' || url.hostname.endsWith('.ura.go.ug')) ? href : null;
}
