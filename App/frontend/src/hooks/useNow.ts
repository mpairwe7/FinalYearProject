/**
 * One shared clock for every timer on the console — wait rings, call timers,
 * "updated 4 s ago". A single interval ticks while anything is subscribed;
 * no row runs its own.
 */

import { useSyncExternalStore } from 'react';

const TICK_MS = 1000;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let now = Date.now();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!timer) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function snapshot(): number {
  return now;
}

/** Milliseconds since the epoch, updated once a second. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** "0:42", "12:07", "1:02:05" — for waits and call durations. */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
