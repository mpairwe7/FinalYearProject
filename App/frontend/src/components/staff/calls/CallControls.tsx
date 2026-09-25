'use client';

import React, { forwardRef } from 'react';
import { endCall, toggleHold, toggleMute } from '@/services/officerCallSession';
import type { ActiveCall } from '@/store/useCallConsoleStore';

/**
 * My call's controls (plan §3.3 C): Mute (M), Hold (H), Transfer (T), and End (E).
 * End asks first — inline, not a modal.
 */
export const CallControls = forwardRef<
  HTMLButtonElement,
  {
    call: ActiveCall;
    confirmEnd: boolean;
    onConfirmEnd: (open: boolean) => void;
    onOpenTransfer?: () => void;
  }
>(function CallControls({ call, confirmEnd, onConfirmEnd, onOpenTransfer }, muteRef) {
  const live = call.state === 'bridged';
  return (
    <div className="cw-controls" role="group" aria-label="Call controls">
      <button ref={muteRef} type="button" className="cc-btn" aria-pressed={call.muted} onClick={toggleMute} disabled={!live}>
        {call.muted ? 'Unmute' : 'Mute'} <kbd>M</kbd>
      </button>

      <button
        type="button"
        className="cc-btn"
        aria-pressed={Boolean(call.onHold)}
        onClick={() => void toggleHold()}
        disabled={!live}
      >
        {call.onHold ? 'Resume' : 'Hold'} <kbd>H</kbd>
      </button>

      <button
        type="button"
        className="cc-btn"
        onClick={onOpenTransfer}
        disabled={!live || !onOpenTransfer}
      >
        Transfer <kbd>T</kbd>
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
