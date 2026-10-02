import React, { useState } from 'react';

interface CallReviewFormProps {
  _callId?: string;
  callId?: string;
  initialRating?: number | null;
  initialNote?: string | null;
  onSubmit: (rating: number, note: string) => Promise<void>;
  isSubmitting?: boolean;
}

export function CallReviewForm({
  _callId,
  callId: _cid,
  initialRating = 5,
  initialNote = '',
  onSubmit,
  isSubmitting = false,
}: CallReviewFormProps) {
  const [rating, setRating] = useState<number>(initialRating || 5);
  const [note, setNote] = useState<string>(initialNote || '');
  const [submitted, setSubmitted] = useState<boolean>(Boolean(initialRating));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSubmit(rating, note);
    setSubmitted(true);
  };

  return (
    <div className="st-case-card">
      <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.9375rem', fontWeight: 600 }}>Officer Call Review</h4>
      <p style={{ margin: '0 0 0.75rem 0', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
        Rate the accuracy and quality of the AI receptionist&apos;s handling for evaluation metrics.
      </p>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.8125rem', fontWeight: 500 }}>Rating:</span>
          {[1, 2, 3, 4, 5].map((star) => (
            <button
              key={star}
              type="button"
              className={`st-rating-star ${rating >= star ? 'st-rating-star--active' : ''}`}
              onClick={() => setRating(star)}
              aria-label={`Rate ${star} star`}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                fontSize: '1.25rem',
                color: rating >= star ? '#eab308' : '#d1d5db',
                padding: '0 2px',
              }}
            >
              ★
            </button>
          ))}
          <span style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>({rating}/5)</span>
        </div>

        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Officer observations, terminology corrections, or handling notes..."
          rows={3}
          maxLength={1000}
          className="st-review-textarea"
          style={{
            width: '100%',
            padding: '0.5rem',
            borderRadius: '6px',
            border: '1px solid var(--border-default, #e5e7eb)',
            fontSize: '0.8125rem',
            fontFamily: 'inherit',
          }}
        />

        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="submit"
            disabled={isSubmitting}
            className="st-btn-primary"
            style={{
              padding: '0.4rem 1rem',
              borderRadius: '6px',
              background: '#2563eb',
              color: '#ffffff',
              fontSize: '0.8125rem',
              fontWeight: 600,
              border: 'none',
              cursor: isSubmitting ? 'not-allowed' : 'pointer',
            }}
          >
            {isSubmitting ? 'Saving…' : submitted ? 'Update Review' : 'Submit Review'}
          </button>
        </div>
      </form>
    </div>
  );
}
