/**
 * A shared, once-per-minute clock for relative timestamps.
 *
 * Rendering "3h" on the server and "4h" on the client is a hydration mismatch,
 * so the server snapshot is 0 and callers treat 0 as "not known yet". One
 * interval is shared across every subscriber rather than one timer per row.
 */
import { useSyncExternalStore } from 'react';
import { formatTimestamp } from '../lib/conversationGroups';

let clockSnapshot = 0;
let clockTimer: number | null = null;
const clockListeners = new Set<() => void>();

function emitClockTick() {
  clockSnapshot = Date.now();
  for (const listener of clockListeners) listener();
}

function subscribeClock(listener: () => void) {
  if (typeof window === 'undefined') return () => {};
  clockListeners.add(listener);
  if (!clockTimer) {
    emitClockTick();
    clockTimer = window.setInterval(emitClockTick, 60_000);
  }
  return () => {
    clockListeners.delete(listener);
    if (clockListeners.size === 0 && clockTimer) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}

const getClockSnapshot = () => clockSnapshot;
const getServerClockSnapshot = () => 0;

/** Current time in ms, or 0 until the client has mounted. */
export function useClientClock(): number {
  return useSyncExternalStore(subscribeClock, getClockSnapshot, getServerClockSnapshot);
}

export function useRelativeTime(timestamp: number): string {
  const now = useClientClock();
  return now ? formatTimestamp(timestamp, now) : '';
}
