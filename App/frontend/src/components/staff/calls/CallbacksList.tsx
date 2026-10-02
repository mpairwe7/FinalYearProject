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

  // Filter only those needing callback that are not yet done, oldest first
  const pending = calls
    .filter((c) => Boolean(c.needs_callback) && !c.callback_done_at)
    .sort((a, b) => a.started_at - b.started_at);

  if (pending.length === 0) {
    return <EmptyState title="No pending callbacks" body="All callback requests have been resolved." />;
  }

  const markDone = async (callId: string) => {
    setSubmitting(true);
    try {
      await callsApi.markCallbackDone(callId, note.trim());
      setMarkingId(null);
      setNote('');
      onRefresh();
    } finally {
      setSubmitting(false);
    }
  };

  const reasonLabel = (reason?: string) => {
    switch (reason) {
      case 'no_officer_available':
        return 'Timed out waiting for officer';
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
                  onClick={() => setMarkingId(null)}
                  disabled={submitting}
                >
                  Cancel
                </button>
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
