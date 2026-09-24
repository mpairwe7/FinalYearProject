'use client';

import React from 'react';
import { useShallow } from 'zustand/react/shallow';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel } from '@/lib/callTopic';
import { formatClock, useNow } from '@/hooks/useNow';
import { dismissSessionError, endCall, toggleMute } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { LevelMeter } from './LevelMeter';

/**
 * The officer's call, pinned to the bottom of every staff page while they are
 * not looking at it on the Desk (plan §3.1) — the call follows the officer.
 */
export function ActiveCallDock({ onBackToCall }: { onBackToCall: (callId: string) => void }) {
  const { activeCall, calls, deskFocus, confirmEnd, setConfirmEnd } = useCallConsoleStore(
    useShallow((s) => ({
      activeCall: s.activeCall,
      calls: s.calls,
      deskFocus: s.deskFocus,
      confirmEnd: s.confirmEnd,
      setConfirmEnd: s.setConfirmEnd,
    })),
  );
  const now = useNow();
  if (!activeCall) return null;

  if (activeCall.state === 'idle') {
    // A call that ended on its own, or could not start: say why, once.
    return activeCall.error ? (
      <div className="cc-dock cc-dock--notice" role="status">
        <span>{activeCall.error}</span>
        <button type="button" className="cc-btn cc-btn--quiet" onClick={dismissSessionError}>
          OK
        </button>
      </div>
    ) : null;
  }
  if (deskFocus === activeCall.callId) return null; // the Desk shows it in full

  const lobby = calls[activeCall.callId];
  const topic = callTopicLabel(lobby?.topic) || 'Call';
  const label =
    activeCall.state === 'bridged'
      ? `On call ${formatClock((now - activeCall.since) / 1000)}`
      : activeCall.state === 'ending'
        ? 'Ending…'
        : 'Connecting…';

  return (
    <div className="cc-dock" role="region" aria-label="Your call">
      <span className={`cc-dot cc-dot--${activeCall.state}`} aria-hidden="true" />
      <span className="cc-dock-label">
        {label} · {topic}
        {lobby && ` · ${callLanguageName(lobby.language)}`}
      </span>
      {activeCall.state === 'bridged' && <LevelMeter source="input" label="Your microphone" />}
      <div className="cc-dock-actions">
        {activeCall.state === 'bridged' && (
          <>
            <button type="button" className="cc-btn" aria-pressed={activeCall.muted} onClick={toggleMute}>
              {activeCall.muted ? 'Unmute' : 'Mute'} <kbd>M</kbd>
            </button>
            {confirmEnd ? (
              <span className="cc-confirm" role="group" aria-label="End the call?">
                End the call?
                <button type="button" className="cc-btn cc-btn--danger" onClick={() => void endCall()}>
                  End
                </button>
                <button type="button" className="cc-btn cc-btn--quiet" onClick={() => setConfirmEnd(false)}>
                  Cancel
                </button>
              </span>
            ) : (
              <button type="button" className="cc-btn cc-btn--danger" onClick={() => setConfirmEnd(true)}>
                End <kbd>E</kbd>
              </button>
            )}
          </>
        )}
        <button type="button" className="cc-btn cc-btn--primary" onClick={() => onBackToCall(activeCall.callId)}>
          Back to call
        </button>
      </div>
    </div>
  );
}
