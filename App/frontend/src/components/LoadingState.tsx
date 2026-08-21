"use client";

import React, { useEffect, useRef, useState } from 'react';

/* ─────────────────────────────────────────────────────────
 * LOADING STATE — pixel-grid loader for long-running work
 *
 * Ported from the 21st.dev component of the same name. The pattern maths is
 * verbatim; what changed is everything the surrounding project needs it to do
 * differently:
 *
 *  - Tailwind utilities became `lds-*` classes in styles/chatv2/loading.css,
 *    because this project has no Tailwind and its preflight would land on top
 *    of globals.css.
 *  - Elapsed time is derived from a `startedAt` timestamp rather than counting
 *    setInterval ticks. The timer has to run across three label changes and
 *    then be reported as "Thought for 13.9s", and a tick counter drifts —
 *    every throttled or dropped interval is time the clock silently loses.
 *  - The label slides: out upward, in from below. The original swaps the text.
 *  - Reduced motion freezes the grid to its dim state with the timer still
 *    running, which the original's own header comment promises but its code
 *    never implemented.
 *
 * Variants (unchanged):
 *   Drive  — square cells, chevron wavefront driving right; the 650ms cycle is
 *            shorter than the sweep, so two fronts are always in flight
 *   Dots   — same wavefront, circular cells
 *   Orbit  — a comet lapping the grid perimeter
 * ───────────────────────────────────────────────────────── */

const chevron = Array.from({ length: 9 }, (_, i) => {
  const r = Math.floor(i / 3),
    c = i % 3;
  return (c + Math.abs(r - 1)) * 90;
});

const ORBIT_ORDER = [0, 1, 2, 5, 8, 7, 6, 3];
const orbit = Array.from({ length: 9 }, (_, i) => {
  const k = ORBIT_ORDER.indexOf(i);
  return k === -1 ? null : k * 110;
});

const PATTERNS: Record<
  string,
  { delays: (number | null)[]; dur: number; round: boolean }
> = {
  Drive: { delays: chevron, dur: 650, round: false },
  Dots: { delays: chevron, dur: 650, round: true },
  Orbit: { delays: orbit, dur: 950, round: false },
};

/** `12.4s`, or `2m 3.1s` past a minute. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, ms) / 1000;
  if (total < 60) return `${total.toFixed(1)}s`;
  return `${Math.floor(total / 60)}m ${(total % 60).toFixed(1)}s`;
}

/**
 * Repaint every 100ms, but read the clock rather than the tick count.
 *
 * A counter that adds 100ms per interval under-reports whenever the browser
 * throttles — a backgrounded tab can lose seconds. The displayed number is
 * also what gets reported as the final "Thought for …", so it has to be the
 * real duration, not the number of times a timer happened to fire.
 */
function useElapsedSince(startedAt: number): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    // No seed write here: `now` is initialised from the clock and the first
    // tick lands 100ms later, so there is nothing for a synchronous setState
    // to correct — it would only cost a second render on mount.
    const t = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(t);
  }, []);

  return Math.max(0, now - startedAt);
}

interface LoadingStateProps {
  label?: string;
  variant?: string;
  /** Epoch ms the turn began. Held across every phase change. */
  startedAt: number;
}

export default function LoadingState({
  label = 'Churning',
  variant = 'Drive',
  startedAt,
}: LoadingStateProps) {
  const elapsed = useElapsedSince(startedAt);
  const { delays, dur, round } = PATTERNS[variant] ?? PATTERNS.Drive;

  /**
   * Keep the outgoing label mounted for the length of the transition so it has
   * something to slide away from. `prev` is only ever set on a real change, so
   * the first paint has nothing leaving and does not animate.
   */
  const [prev, setPrev] = useState<string | null>(null);
  const labelRef = useRef(label);

  useEffect(() => {
    if (labelRef.current === label) return;
    const leaving = labelRef.current;
    labelRef.current = label;
    setPrev(leaving);
    const t = setTimeout(() => setPrev(null), 280);
    return () => clearTimeout(t);
  }, [label]);

  return (
    <div className="lds" role="status" aria-label={label}>
      <span aria-hidden="true" className="lds-grid">
        {delays.map((d, i) => (
          <span
            key={i}
            className={`lds-cell${round ? ' lds-cell-round' : ''}`}
            style={{
              opacity: d === null ? 0.07 : 0.15,
              animation:
                d === null ? 'none' : `pixel-on ${dur}ms ease-in-out ${d}ms infinite`,
            }}
          />
        ))}
      </span>

      <span className="lds-labels" aria-hidden="true">
        {/* Reserves the row's width so the timer beside it does not jump as
            the words swap. The visible copies are absolutely positioned. */}
        <span className="lds-label-ghost">{label}</span>
        {prev !== null && (
          <span key={`out-${prev}`} className="lds-label lds-label-out">
            {prev}
          </span>
        )}
        <span key={`in-${label}`} className={`lds-label${prev !== null ? ' lds-label-in' : ''}`}>
          {label}
        </span>
      </span>

      <span className="lds-time" aria-hidden="true">
        {formatElapsed(elapsed)}
      </span>
    </div>
  );
}
