/**
 * One staff lobby socket (`WS /v1/admin/calls/stream`) for the whole console.
 *
 * Every staff page mounts its own `StaffGuard`, so a socket owned by a
 * component would close and reopen on each navigation — and a transfer that
 * arrived in the gap would be missed. This module owns it instead,
 * reference-counted: pages `acquireLobby()` on mount and release on unmount,
 * and a release only closes the socket after a short grace, which the next
 * page's acquire cancels.
 *
 * Each (re)connect resyncs the live calls over HTTP, so nothing that happened
 * while the socket was down is lost. Close codes that mean "you will never be
 * let in" (feature off, not signed in, not staff) stop it for the session.
 */

import { appendAuthToken } from '@/lib/authSession';
import { callsApi } from '@/services/callsApi';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

/** Closed by the server for good: 1001 flag off, 4401 not signed in, 4403 not staff. */
const FINAL_CLOSE_CODES = new Set([1001, 4401, 4403]);
const RELEASE_GRACE_MS = 5000;
const MAX_BACKOFF_MS = 30000;

let socket: WebSocket | null = null;
let refs = 0;
let closeTimer: ReturnType<typeof setTimeout> | null = null;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let backoffMs = 1000;
let disabled = false;

function lobbyUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return appendAuthToken(`${protocol}//${window.location.host}/api/v1/admin/calls/stream`);
}

async function resync(): Promise<void> {
  try {
    const { calls } = await callsApi.listCalls('live', 100);
    useCallConsoleStore.getState().resync(calls);
  } catch {
    /* the next event or reconnect tries again */
  }
}

function connect(): void {
  if (socket || disabled || typeof window === 'undefined' || typeof WebSocket === 'undefined') return;
  const store = useCallConsoleStore.getState();
  store.setLobbyStatus('connecting');
  let ws: WebSocket;
  try {
    ws = new WebSocket(lobbyUrl());
  } catch {
    scheduleRetry();
    return;
  }
  socket = ws;
  ws.onopen = () => {
    backoffMs = 1000;
    useCallConsoleStore.getState().setLobbyStatus('open');
    void resync();
  };
  ws.onmessage = (event: MessageEvent) => {
    try {
      const payload = JSON.parse(String(event.data));
      if (payload && typeof payload.type === 'string' && payload.type !== 'ping') {
        useCallConsoleStore.getState().applyLobbyEvent(payload);
      }
    } catch {
      /* not ours */
    }
  };
  ws.onclose = (event: CloseEvent) => {
    if (socket === ws) socket = null;
    if (FINAL_CLOSE_CODES.has(event.code)) {
      disabled = true;
      useCallConsoleStore.getState().setLobbyStatus('disabled');
      return;
    }
    if (refs > 0) scheduleRetry();
    else useCallConsoleStore.getState().setLobbyStatus('idle');
  };
  ws.onerror = () => {
    /* onclose follows and decides */
  };
}

function scheduleRetry(): void {
  if (retryTimer || disabled) return;
  useCallConsoleStore.getState().setLobbyStatus('retrying');
  retryTimer = setTimeout(() => {
    retryTimer = null;
    if (refs > 0) connect();
  }, backoffMs);
  backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
}

/** Keep the lobby open while the returned release has not been called. */
export function acquireLobby(): () => void {
  refs += 1;
  if (closeTimer) {
    clearTimeout(closeTimer);
    closeTimer = null;
  }
  connect();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    refs = Math.max(0, refs - 1);
    if (refs === 0 && !closeTimer) {
      closeTimer = setTimeout(() => {
        closeTimer = null;
        if (refs === 0) closeLobby();
      }, RELEASE_GRACE_MS);
    }
  };
}

function closeLobby(): void {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
  const ws = socket;
  socket = null;
  if (ws) {
    try {
      ws.close();
    } catch {
      /* already closing */
    }
  }
}

/** The lobby refused us for this session (flag off / not staff): hide the call layer. */
export function isLobbyDisabled(): boolean {
  return disabled;
}

/** Tests only: forget everything, as a fresh page load would. */
export function resetLobbyForTests(): void {
  closeLobby();
  if (closeTimer) clearTimeout(closeTimer);
  closeTimer = null;
  refs = 0;
  backoffMs = 1000;
  disabled = false;
}
