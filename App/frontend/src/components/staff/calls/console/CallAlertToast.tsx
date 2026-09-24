'use client';

import React from 'react';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel, isRaisedPriority } from '@/lib/callTopic';
import type { LobbyCall } from '@/store/useCallConsoleStore';
import { WaitRing } from './WaitRing';

/** Why the AI handed the caller over, in an officer's words. */
export function transferReasonLabel(reason: string): string {
  if (!reason) return 'Asked for an officer';
  if (/caller_requested|human|officer|person/i.test(reason)) return 'Asked for a person';
  if (reason === 'officer_takeover') return 'Taken over from the AI';
  if (reason === 'clarification_failed') return 'The AI could not understand them';
  if (reason === 'timeout' || reason === 'system_error') return 'The AI could not answer';
  return reason.replace(/_/g, ' ');
}

export function CallAlertToast({
  call,
  busy,
  error,
  canTake,
  onTake,
  onPreview,
  onDismiss,
}: {
  call: LobbyCall;
  busy: boolean;
  error: string | null;
  canTake: boolean;
  onTake: () => void;
  onPreview: () => void;
  onDismiss: () => void;
}) {
  const topic = callTopicLabel(call.topic) || 'General tax support';
  return (
    <div className="cc-alert" role="status" aria-label={`Caller waiting: ${topic}`}>
      <div className="cc-alert-head">
        <WaitRing since={call.waiting_since} />
        <span className="cc-alert-title">Caller waiting for an officer</span>
        {isRaisedPriority(call.priority) && (
          <span className={`call-chip call-chip--${call.priority}`}>{call.priority}</span>
        )}
      </div>
      <div className="cc-alert-topic">{topic}</div>
      <div className="cc-alert-meta">
        {callLanguageName(call.language)} · {transferReasonLabel(call.reason)}
        {call.ticket_ref && ` · Ticket ${call.ticket_ref.slice(0, 8)}`}
        {call.attempt > 1 && ` · Passed on by an officer`}
      </div>
      {error && <div className="cc-alert-error">{error}</div>}
      <div className="cc-alert-actions">
        {canTake && (
          <button type="button" className="cc-btn cc-btn--primary" onClick={onTake} disabled={busy}>
            {busy ? 'Connecting…' : 'Take call'}
          </button>
        )}
        <button type="button" className="cc-btn" onClick={onPreview}>
          Preview
        </button>
        <button type="button" className="cc-btn cc-btn--quiet" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
