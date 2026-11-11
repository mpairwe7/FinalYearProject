'use client';

import { useEffect } from 'react';
import { callsApi } from '@/services/callsApi';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

/**
 * Periodically beats presence heartbeat to /v1/admin/officers/me/presence every 20s
 * while tab is visible (docs/plans/officer-call-desk-plan.md §6.4, §8.2).
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
      callsApi.updatePresence({ status: effectiveStatus }).catch(() => {});
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
