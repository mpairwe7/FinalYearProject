'use client';

import React, { useEffect, useState } from 'react';
import { formatClock, useNow } from '@/hooks/useNow';
import { wrapUpCall } from '@/services/officerCallSession';
import type { WrapUpPayload } from '@/services/callsApi';

export function WrapUpPanel({
  callId: _callId,
  initialNote = '',
  ticketId,
  onFinished,
}: {
  callId: string;
  initialNote?: string;
  ticketId?: string | null;
  onFinished: () => void;
}) {
  const [startedAt] = useState(() => Date.now());
  const now = useNow();
  const [note, setNote] = useState(initialNote);
  const [outcome, setOutcome] = useState<WrapUpPayload['outcome']>('resolved');
  const [ticketAction, setTicketAction] = useState<'resolve' | 'keep_open' | 'create'>(
    ticketId ? 'resolve' : 'keep_open',
  );
  const [rating, setRating] = useState<number>(4);
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
    setSubmitting(true);
    setError(null);
    try {
      await wrapUpCall({
        outcome,
        note: note.trim(),
        ticket_action: ticketId ? ticketAction : ticketAction === 'create' ? 'create' : undefined,
        rating,
        rating_note: ratingNote.trim() || undefined,
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

        <div className="cc-field-group">
          <label className="cc-label">AI handling review</label>
          <div className="cc-rating-row">
            {[1, 2, 3, 4, 5].map((star) => (
              <button
                key={star}
                type="button"
                className={`cc-star-btn ${star <= rating ? 'is-active' : ''}`}
                onClick={() => setRating(star)}
                disabled={submitting}
              >
                ★
              </button>
            ))}
            <input
              type="text"
              className="cc-input"
              placeholder="Optional rating notes..."
              value={ratingNote}
              onChange={(e) => setRatingNote(e.target.value)}
              disabled={submitting}
            />
          </div>
        </div>

        <footer className="cw-wrapup-actions">
          <button type="submit" className="cc-btn cc-btn--primary" disabled={submitting}>
            {submitting ? 'Saving…' : 'Save & become available'} <kbd>⌘↵</kbd>
          </button>
        </footer>
      </form>
    </section>
  );
}
