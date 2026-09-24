'use client';

import React, { useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { formatClock, useNow } from '@/hooks/useNow';
import { type Availability, useCallConsoleStore, waitingCalls } from '@/store/useCallConsoleStore';

const AVAILABILITY_LABEL: Record<Availability, string> = {
  available: 'Available',
  busy: 'Busy',
  away: 'Away',
};

/**
 * The call bar, above every staff page (plan §3.1): my availability, live and
 * waiting calls, and whether my microphone and alert sound are ready.
 */
export function CallConsoleBar({
  canTake,
  onOpenCall,
  onCheckMic,
}: {
  canTake: boolean;
  onOpenCall: (callId: string) => void;
  onCheckMic: () => void;
}) {
  const { calls, availability, activeCall, soundEnabled, micReady, setAvailability, setSoundEnabled } =
    useCallConsoleStore(
      useShallow((s) => ({
        calls: s.calls,
        availability: s.availability,
        activeCall: s.activeCall,
        soundEnabled: s.soundEnabled,
        micReady: s.micReady,
        setAvailability: s.setAvailability,
        setSoundEnabled: s.setSoundEnabled,
      })),
    );
  const now = useNow();
  const live = Object.keys(calls).length;
  const waiting = useMemo(() => waitingCalls(calls), [calls]);
  const longest = waiting.reduce<(typeof waiting)[number] | null>(
    (oldest, c) => (!oldest || (c.waiting_since ?? 0) < (oldest.waiting_since ?? 0) ? c : oldest),
    null,
  );
  const onCall = activeCall !== null && activeCall.state !== 'idle';

  return (
    <div className="cc-bar" role="region" aria-label="Phone calls">
      {canTake && (
        <div className="cc-bar-group">
          {onCall ? (
            <span className="cc-state cc-state--on-call">On call</span>
          ) : (
            <label className="cc-availability">
              <span className={`cc-dot cc-dot--${availability}`} aria-hidden="true" />
              <span className="cc-sr-only">Availability</span>
              <select
                value={availability}
                onChange={(e) => setAvailability(e.target.value as Availability)}
                aria-label="Availability"
              >
                {(Object.keys(AVAILABILITY_LABEL) as Availability[]).map((a) => (
                  <option key={a} value={a}>
                    {AVAILABILITY_LABEL[a]}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
      <div className="cc-bar-group">
        <span className="cc-count">
          Live <strong>{live}</strong>
        </span>
        {waiting.length > 0 && longest ? (
          <button type="button" className="cc-count cc-count--waiting" onClick={() => onOpenCall(longest.call_id)}>
            Waiting <strong>{waiting.length}</strong> ·{' '}
            {formatClock(now / 1000 - (longest.waiting_since ?? now / 1000))}
          </button>
        ) : (
          <span className="cc-count">
            Waiting <strong>0</strong>
          </span>
        )}
      </div>
      {canTake && (
        <div className="cc-bar-group cc-bar-group--end">
          <button type="button" className={`cc-btn cc-btn--quiet${micReady ? '' : ' is-attention'}`} onClick={onCheckMic}>
            {micReady ? 'Mic ready' : 'Check mic'}
          </button>
          <button
            type="button"
            className="cc-btn cc-btn--quiet"
            aria-pressed={soundEnabled}
            onClick={() => setSoundEnabled(!soundEnabled)}
          >
            {soundEnabled ? 'Sound on' : 'Sound off'}
          </button>
        </div>
      )}
    </div>
  );
}
