'use client';

import React, { forwardRef } from 'react';
import { endCall, toggleMute } from '@/services/officerCallSession';
import type { ActiveCall } from '@/store/useCallConsoleStore';

/**
 * My call's controls (plan §3.3 C). Mute and End for now; hold and transfer
 * arrive with the Call Desk's Phase 2. End asks first — inline, not a modal.
 */
export const CallControls = forwardRef<
  HTMLButtonElement,
  { call: ActiveCall; confirmEnd: boolean; onConfirmEnd: (open: boolean) => void }
>(function CallControls({ call, confirmEnd, onConfirmEnd }, muteRef) {
  const live = call.state === 'bridged';
  return (
    <div className="cw-controls" role="group" aria-label="Call controls">
      <button ref={muteRef} type="button" className="cc-btn" aria-pressed={call.muted} onClick={toggleMute} disabled={!live}>
        {call.muted ? 'Unmute' : 'Mute'} <kbd>M</kbd>
      </button>
      <span className="cw-controls-spacer" />
      {confirmEnd ? (
        <span className="cc-confirm" role="group" aria-label="End the call?">
          End the call? The caller hears a goodbye.
          <button type="button" className="cc-btn cc-btn--danger" onClick={() => void endCall()} autoFocus>
            End call
          </button>
          <button type="button" className="cc-btn cc-btn--quiet" onClick={() => onConfirmEnd(false)}>
            Cancel
          </button>
        </span>
      ) : (
        <button type="button" className="cc-btn cc-btn--danger" onClick={() => onConfirmEnd(true)} disabled={!live}>
          {call.state === 'ending' ? 'Ending…' : 'End call'} <kbd>E</kbd>
        </button>
      )}
    </div>
  );
});
