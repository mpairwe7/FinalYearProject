'use client';

import React from 'react';
import { formatClock, useNow } from '@/hooks/useNow';

/** How long a caller waits before the AI takes them back (RECEPTIONIST_TRANSFER_TIMEOUT_S). */
export const OFFICER_WAIT_S = 90;
const R = 9;
const CIRCUMFERENCE = 2 * Math.PI * R;

/**
 * A caller's wait for an officer: a ring that drains over the timeout and the
 * time waited beside it. Warn colour until the last 30 s, then bad.
 */
export function WaitRing({ since, totalS = OFFICER_WAIT_S }: { since: number | null; totalS?: number }) {
  const now = useNow();
  const waited = since ? Math.max(0, now / 1000 - since) : 0;
  const left = Math.max(0, totalS - waited);
  const urgent = left <= 30;
  return (
    <span className={`cc-wait${urgent ? ' is-urgent' : ''}`} title={`Waiting ${formatClock(waited)}`}>
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
        <circle className="cc-wait-track" cx="12" cy="12" r={R} />
        <circle
          className="cc-wait-fill"
          cx="12"
          cy="12"
          r={R}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - left / totalS)}
          transform="rotate(-90 12 12)"
        />
      </svg>
      <span className="cc-wait-time">{formatClock(waited)}</span>
    </span>
  );
}
