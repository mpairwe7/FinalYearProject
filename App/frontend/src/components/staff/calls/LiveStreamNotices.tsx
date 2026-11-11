'use client';

import React from 'react';
import type { CallLiveStatus } from '@/hooks/useCalls';
import { useNow } from '@/hooks/useNow';

/**
 * Notices above a live call's transcript (ADR-0042): it stopped updating, for
 * a while or for good, or the call's time limit is near. Nothing once the
 * call has ended.
 */
export function LiveStreamNotices({
  status,
  limitAt,
  callStatus,
}: {
  status: CallLiveStatus;
  /** When the call's time limit falls (ms since the epoch); null until the warning. */
  limitAt: number | null;
  callStatus: string;
}) {
  if (callStatus === 'ended') return null;
  return (
    <>
      {/* The transcript used to freeze without a sign when its stream dropped (G606). */}
      {status === 'reconnecting' && (
        <p className="cc-notice" role="status">
          Live transcript paused — reconnecting…
        </p>
      )}
      {/* Closed for good: the session expired, or access was refused. */}
      {status === 'stopped' && (
        <p className="cc-notice" role="status">
          Live transcript stopped. Sign in again, then reopen this call, to see it update.
        </p>
      )}
      {limitAt !== null && <TimeLimitNotice limitAt={limitAt} />}
    </>
  );
}

/**
 * Notice before the call's time limit ends it (G602): a minute, or half of a
 * limit of a minute or less. It counts down on the console's shared clock
 * rather than keeping the estimate it was warned with, in 15-second steps:
 * a status region is read out each time its text changes, and a screen reader
 * must not read out every second.
 */
function TimeLimitNotice({ limitAt }: { limitAt: number }) {
  const left = Math.max(0, Math.round((limitAt - useNow()) / 1000));
  const about = left >= 55 ? 'a minute' : `${Math.ceil(left / 15) * 15} seconds`;
  return (
    <p className="cc-notice" role="status">
      {left === 0
        ? 'This call has reached its time limit and is ending.'
        : `This call reaches its time limit in about ${about} and will end then.`}
    </p>
  );
}
