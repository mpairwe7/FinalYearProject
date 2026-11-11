'use client';

import React from 'react';
import type { CallLiveStatus } from '@/hooks/useCalls';

/**
 * Notices above a live call's transcript (ADR-0042): it stopped updating, for
 * a while or for good, or the call's time limit is near. Nothing once the
 * call has ended.
 */
export function LiveStreamNotices({
  status,
  secondsRemaining,
  callStatus,
}: {
  status: CallLiveStatus;
  secondsRemaining: number | null;
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
      {/* Notice before the call's time limit ends it (G602): a minute, or
          half of a limit of a minute or less. */}
      {secondsRemaining !== null && (
        <p className="cc-notice" role="status">
          This call reaches its time limit in about{' '}
          {secondsRemaining >= 55 ? 'a minute' : `${secondsRemaining} seconds`} and will end then.
        </p>
      )}
    </>
  );
}
