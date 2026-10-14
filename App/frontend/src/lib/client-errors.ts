/**
 * Client error reporting: uncaught errors, unhandled promise rejections and
 * error-boundary renders, sent to `/api/v1/telemetry/errors`.
 *
 * Before this, a client-side crash reached only the visitor's own console.
 * Each report is the error class, the Next.js digest (when a boundary caught
 * it), the script location and the pathname. The message is never sent: it
 * can quote what the taxpayer typed. Reports carry no identifiers and are
 * operational, so they are not gated on analytics consent; a page sends at
 * most a handful, and each distinct error once.
 */

export const ERRORS_ENDPOINT = '/api/v1/telemetry/errors';
const MAX_REPORTS_PER_PAGE = 10;

export type ClientErrorKind = 'error' | 'unhandledrejection' | 'boundary' | 'global-boundary';

let sent = 0;
const seen = new Set<string>();

function errorName(error: unknown): string {
  if (error instanceof Error) return error.name || 'Error';
  return typeof error;
}

export function reportClientError(kind: ClientErrorKind, error: unknown, digest = '', source = ''): void {
  if (typeof window === 'undefined' || sent >= MAX_REPORTS_PER_PAGE) return;
  const name = errorName(error).slice(0, 80);
  const key = `${kind}|${name}|${digest}|${source}`;
  if (seen.has(key)) return;
  seen.add(key);
  sent += 1;
  const body = JSON.stringify({
    kind,
    name,
    digest: digest.slice(0, 64),
    source: source.split('?')[0].slice(0, 200),
    route: window.location.pathname,
  });
  try {
    if (navigator.sendBeacon?.(ERRORS_ENDPOINT, new Blob([body], { type: 'application/json' }))) return;
  } catch {
    // fall through to fetch
  }
  void fetch(ERRORS_ENDPOINT, {
    method: 'POST',
    body,
    headers: { 'Content-Type': 'application/json' },
    keepalive: true,
  }).catch(() => undefined);
}

/** Report uncaught errors and unhandled rejections; returns an uninstaller. */
export function installGlobalErrorHandlers(): () => void {
  const onError = (event: ErrorEvent) => {
    const location = event.filename ? `${event.filename}:${event.lineno}:${event.colno}` : '';
    reportClientError('error', event.error ?? new Error('script error'), '', location);
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    reportClientError('unhandledrejection', event.reason);
  };
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  return () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  };
}

/** Test seam. */
export function resetClientErrorsForTests(): void {
  sent = 0;
  seen.clear();
}
