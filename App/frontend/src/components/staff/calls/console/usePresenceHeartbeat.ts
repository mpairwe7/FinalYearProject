'use client';

import { useEffect } from 'react';
import { callsApi } from '@/services/callsApi';
import { getActiveCall, resumeOwedWrapUp } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

/**
 * The latest pulse sent, from any mount: answers are not ordered, and only
 * the latest one's speaks for the officer as they are now.
 */
let latestPulse = 0;

/**
 * Periodically beats presence heartbeat to /v1/admin/officers/me/presence every 20s
 * while tab is visible (docs/plans/officer-call-desk-plan.md §6.4, §8.2).
 * An answer naming a wrap-up the officer owes (`wrap_up_call_id`) brings it up
 * when this tab has no call and the officer is available: the dock offers
 * "Finish wrap-up", and the Desk shows the wrap-up form for that call. While
 * they have chosen away or offline it waits until they come back.
 */
export function usePresenceHeartbeat(userId?: string): void {
  const availability = useCallConsoleStore((s) => s.availability);
  const activeCall = useCallConsoleStore((s) => s.activeCall);

  useEffect(() => {
    if (!userId) return;

    const pulse = () => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
      // Every state between taking a call and finishing it is on a call: the
      // claim, the media connecting or reconnecting, the closing line. Only
      // bridged counted, so an officer in any of the others was offered to
      // the next caller (G610).
      const state = activeCall?.state ?? 'idle';
      const effectiveStatus = state === 'wrap_up' ? 'wrap_up' : state !== 'idle' ? 'on_call' : availability;
      const sentWith = getActiveCall();
      const thisPulse = ++latestPulse;
      callsApi
        .updatePresence({ status: effectiveStatus })
        .then((presence) => {
          // A call that ended under the officer on an error, or before a
          // reload, still owes its wrap-up: the server names it. Only if this
          // tab's call has not changed since (a wrap-up saved meanwhile is
          // not reopened), and only from the latest pulse's answer: an older
          // one can arrive later and predate a wrap-up saved since (G610,
          // ADR-0045). Only while the officer offered themselves as
          // available: the server keeps away or offline and still names the
          // call, and opening it would send wrap_up over that choice.
          if (
            presence?.wrap_up_call_id &&
            effectiveStatus === 'available' &&
            thisPulse === latestPulse &&
            getActiveCall() === sentWith
          ) {
            resumeOwedWrapUp(presence.wrap_up_call_id);
          }
        })
        .catch(() => {});
    };

    // Immediate first pulse
    pulse();

    const interval = window.setInterval(pulse, 20000);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') pulse();
    };

    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [userId, availability, activeCall]);
}
