'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { formatClock } from '@/hooks/useNow';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel } from '@/lib/callTopic';
import { callsApi, type CallRecord } from '@/services/callsApi';
import { EmptyState } from '@/components/ops/States';

export function CallbacksList({
  calls,
  onRefresh,
  onOpenCall,
}: {
  calls: CallRecord[];
  onRefresh: () => void;
  onOpenCall: (callId: string) => void;
}) {
  const [markingId, setMarkingId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // Filter only those needing callback that are not yet done, oldest first
  const pending = calls
    .filter((c) => Boolean(c.needs_callback) && !c.callback_done_at)
    .sort((a, b) => a.started_at - b.started_at);

  if (pending.length === 0) {
    return <EmptyState title="No pending callbacks" body="All callback requests have been resolved." />;
  }

  const markDone = async (callId: string) => {
    setSubmitting(true);
    setFailure(null);
    try {
      await callsApi.markCallbackDone(callId, note.trim());
      setMarkingId(null);
      setNote('');
      onRefresh();
    } catch (err) {
      // It used to fail without a word (G611): another officer may have
      // rung back first, or the server could not save it.
      setFailure((err as Error)?.message || 'The callback could not be marked done. Try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const reasonLabel = (reason?: string) => {
    switch (reason) {
      case 'no_officer_available':
        return 'Timed out waiting for officer';
      case 'no_officer_online':
        return 'No officer was signed in';
      case 'outside_working_hours':
        return "Called outside officers' hours";
      case 'caller_requested_callback':
        return 'Caller asked to be called back';
      case 'caller_left_waiting':
        return 'Caller disconnected while waiting';
      case 'officer_outcome':
        return 'Officer requested callback';
      default:
        return 'Owed callback';
    }
  };

  return (
    <div className="cb-list" role="region" aria-label="Pending callbacks">
      {pending.map((call) => (
        <article key={call.call_id} className="cb-card">
          <div className="cb-card-main">
            <header className="cb-card-head">
              <span className="cb-reason-badge">{reasonLabel(call.callback_reason)}</span>
              <span className="st-chip--language">{callLanguageName(call.locale)}</span>
              <span className="cb-time">Started {formatClock(Date.now() / 1000 - call.started_at)} ago</span>
            </header>

            <h4 className="cb-card-topic">{callTopicLabel(call.topic) || 'Tax Inquiry'}</h4>

            {call.summary?.summary && <p className="cb-summary">{call.summary.summary}</p>}

            <div className="cb-meta-row">
              {call.ticket_id && (
                <Link href={`/admin/tickets?ticket=${encodeURIComponent(call.ticket_id)}`} className="cw-ticket">
                  Ticket #{call.ticket_id.slice(0, 8)}
                </Link>
              )}
              <button
                type="button"
                className="cc-btn cc-btn--quiet"
                onClick={() => onOpenCall(call.call_id)}
              >
                View transcript
              </button>
            </div>
          </div>

          <div className="cb-card-actions">
            {markingId === call.call_id ? (
              <div className="cb-mark-done-form">
                <input
                  type="text"
                  className="cc-input"
                  placeholder="Resolution note..."
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  autoFocus
                />
                <button
                  type="button"
                  className="cc-btn cc-btn--primary"
                  onClick={() => void markDone(call.call_id)}
                  disabled={submitting}
                >
                  {submitting ? 'Saving…' : 'Confirm'}
                </button>
                <button
                  type="button"
                  className="cc-btn cc-btn--quiet"
                  onClick={() => {
                    setMarkingId(null);
                    setFailure(null);
                  }}
                  disabled={submitting}
                >
                  Cancel
                </button>
                {failure && (
                  <p className="cc-error" role="alert">
                    {failure}
                  </p>
                )}
              </div>
            ) : (
              <button
                type="button"
                className="cc-btn cc-btn--primary"
                onClick={() => {
                  setMarkingId(call.call_id);
                  setNote('');
                }}
              >
                Mark done
              </button>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
