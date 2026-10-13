'use client';

/**
 * The officer's wrap-up after a call: the note, the outcome, the ticket — and
 * how satisfied the caller said they were.
 *
 * The caller is on the phone, so they cannot tap a rating; the officer asks
 * before ending ("Were you satisfied with the help I gave you today?") and
 * records the answer here, on the same five-level scale taxpayers rate
 * officer chats with. It is required: the answer, or that the caller didn't
 * say. Administrators audit it on /admin/quality (Officer calls).
 *
 * The officer's own rating of how the AI handled the call before the
 * transfer is separate and optional, offered only when the AI answered
 * something; nothing is sent unless a star is chosen.
 */
import React, { useEffect, useState } from 'react';
import { formatClock, useNow } from '@/hooks/useNow';
import { wrapUpCall } from '@/services/officerCallSession';
import type { WrapUpPayload } from '@/services/callsApi';
import { SatisfactionScale } from '@/components/rating/SatisfactionScale';

export function WrapUpPanel({
  callId: _callId,
  initialNote = '',
  ticketId,
  aiAnswered = false,
  onFinished,
}: {
  callId: string;
  initialNote?: string;
  ticketId?: string | null;
  /** The AI answered at least one question before the transfer. */
  aiAnswered?: boolean;
  onFinished: () => void;
}) {
  const [startedAt] = useState(() => Date.now());
  const now = useNow();
  const [note, setNote] = useState(initialNote);
  const [outcome, setOutcome] = useState<WrapUpPayload['outcome']>('resolved');
  const [ticketAction, setTicketAction] = useState<'resolve' | 'keep_open' | 'create'>(
    ticketId ? 'resolve' : 'keep_open',
  );
  const [satisfaction, setSatisfaction] = useState<number | null>(null);
  const [notSaid, setNotSaid] = useState(false);
  const [rating, setRating] = useState<number | null>(null);
  const [ratingNote, setRatingNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialNote && !note) {
      setNote(initialNote);
    }
  }, [initialNote, note]);

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!note.trim()) {
      setError('Please provide a call wrap-up note');
      return;
    }
    if (satisfaction == null && !notSaid) {
      setError("Record how satisfied the caller said they were, or mark that they didn't say");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await wrapUpCall({
        outcome,
        note: note.trim(),
        ticket_action: ticketId ? ticketAction : ticketAction === 'create' ? 'create' : undefined,
        ...(notSaid
          ? { caller_satisfaction_status: 'not_said' as const }
          : { caller_satisfaction: satisfaction as number, caller_satisfaction_status: 'answered' as const }),
        ...(rating != null ? { rating, rating_note: ratingNote.trim() || undefined } : {}),
      });
      onFinished();
    } catch (err) {
      setError((err as Error).message || 'Failed to save wrap-up');
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        void handleSubmit();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const durationSec = Math.max(0, Math.floor((now - startedAt) / 1000));

  return (
    <section className="cw-wrapup" aria-label="Wrap-up panel">
      <header className="cw-wrapup-head">
        <h3 className="cw-wrapup-title">
          Wrap-up · <span className="cw-clock">{formatClock(durationSec)}</span>
        </h3>
        <span className="cw-wrapup-hint">Save to complete call handling and become Available</span>
      </header>

      {error && <div className="cw-error" role="alert">{error}</div>}

      <form onSubmit={handleSubmit} className="cw-wrapup-form">
        <div className="cc-field">
          <label htmlFor="wrapup-note" className="cc-label">
            Call note (AI drafted — edit before saving)
          </label>
          <textarea
            id="wrapup-note"
            className="cc-textarea"
            rows={4}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={submitting}
            placeholder="Summarize taxpayer inquiry, action taken, and resolution..."
            required
          />
        </div>

        <div className="cc-field-group">
          <label className="cc-label">Outcome</label>
          <div className="cc-radio-row">
            <label>
              <input
                type="radio"
                name="outcome"
                value="resolved"
                checked={outcome === 'resolved'}
                onChange={() => setOutcome('resolved')}
              />{' '}
              Resolved
            </label>
            <label>
              <input
                type="radio"
                name="outcome"
                value="follow_up"
                checked={outcome === 'follow_up'}
                onChange={() => setOutcome('follow_up')}
              />{' '}
              Follow-up needed
            </label>
            <label>
              <input
                type="radio"
                name="outcome"
                value="callback"
                checked={outcome === 'callback'}
                onChange={() => setOutcome('callback')}
              />{' '}
              Callback
            </label>
            <label>
              <input
                type="radio"
                name="outcome"
                value="referred"
                checked={outcome === 'referred'}
                onChange={() => setOutcome('referred')}
              />{' '}
              Referred to team
            </label>
          </div>
        </div>

        <div className="cc-field-group">
          <label className="cc-label">Ticket</label>
          {ticketId ? (
            <div className="cc-row-inline">
              <span className="cw-ticket-badge">#{ticketId.slice(0, 8)}</span>
              <select
                className="cc-select"
                value={ticketAction}
                onChange={(e) => setTicketAction(e.target.value as 'resolve' | 'keep_open')}
                disabled={submitting}
              >
                <option value="resolve">Resolve ticket</option>
                <option value="keep_open">Keep ticket open with note</option>
              </select>
            </div>
          ) : (
            <div className="cc-row-inline">
              <label>
                <input
                  type="checkbox"
                  checked={ticketAction === 'create'}
                  onChange={(e) => setTicketAction(e.target.checked ? 'create' : 'keep_open')}
                  disabled={submitting}
                />{' '}
                Create tracking ticket from this call
              </label>
            </div>
          )}
        </div>

        <fieldset className="cc-field-group cw-satisfaction" aria-describedby="wrapup-satisfaction-hint">
          <legend className="cc-label">
            Caller satisfaction <span className="cw-required">required</span>
          </legend>
          <p id="wrapup-satisfaction-hint" className="cw-wrapup-hint">
            What the caller told you when you asked how satisfied they were with the help today.
          </p>
          <SatisfactionScale
            value={notSaid ? null : satisfaction}
            onChange={(score) => {
              setSatisfaction(score);
              setNotSaid(false);
            }}
            groupLabel="How satisfied the caller said they were"
            disabled={submitting}
          />
          <label className="cc-row-inline cw-not-said">
            <input
              type="checkbox"
              checked={notSaid}
              onChange={(e) => {
                setNotSaid(e.target.checked);
                if (e.target.checked) setSatisfaction(null);
              }}
              disabled={submitting}
            />{' '}
            The caller didn&apos;t say
          </label>
        </fieldset>

        {aiAnswered ? (
          <details className="cc-field-group cw-ai-review">
            <summary className="cc-label">Rate the AI&apos;s handling before the transfer (optional)</summary>
            <div className="cc-rating-row" role="group" aria-label="Your rating of the AI's handling">
              {[1, 2, 3, 4, 5].map((star) => (
                <button
                  key={star}
                  type="button"
                  className={`cc-star-btn ${rating != null && star <= rating ? 'is-active' : ''}`}
                  aria-label={`${star} of 5 stars`}
                  aria-pressed={rating === star}
                  onClick={() => setRating((current) => (current === star ? null : star))}
                  disabled={submitting}
                >
                  ★
                </button>
              ))}
              <input
                type="text"
                className="cc-input"
                aria-label="Notes on the AI's handling"
                placeholder="Optional rating notes..."
                value={ratingNote}
                onChange={(e) => setRatingNote(e.target.value)}
                disabled={submitting || rating == null}
              />
            </div>
          </details>
        ) : null}

        <footer className="cw-wrapup-actions">
          <button type="submit" className="cc-btn cc-btn--primary" disabled={submitting}>
            {submitting ? 'Saving…' : 'Save & become available'} <kbd>⌘↵</kbd>
          </button>
        </footer>
      </form>
    </section>
  );
}
