"use client";

import React, { useState, useCallback, useOptimistic, useTransition } from 'react';
import { submitFeedback, updateFeedbackComment, trackFeedbackGiven } from '../store/useAnalyticsStore';

interface FeedbackButtonsProps {
  messageId: string;
  userQuery: string;
  botReply: string;
}

const ThumbsUpIcon = ({ filled }: { filled?: boolean }) => (
  <svg width="14" height="14" viewBox="0 0 24 24"
    fill={filled ? "currentColor" : "none"}
    stroke="currentColor" strokeWidth="1.8"
    aria-hidden="true">
    <path d="M7 22V11l5-10 1.5.5a2.5 2.5 0 0 1 1.5 2.3V8h5.5a2 2 0 0 1 2 2.3l-1.5 9A2 2 0 0 1 19 21H7Z" />
    <path d="M2 11h3v11H2z" />
  </svg>
);

const ThumbsDownIcon = ({ filled }: { filled?: boolean }) => (
  <svg width="14" height="14" viewBox="0 0 24 24"
    fill={filled ? "currentColor" : "none"}
    stroke="currentColor" strokeWidth="1.8"
    aria-hidden="true">
    <path d="M17 2v11l-5 10-1.5-.5A2.5 2.5 0 0 1 9 20.2V16H3.5a2 2 0 0 1-2-2.3l1.5-9A2 2 0 0 1 5 3h12Z" />
    <path d="M19 2h3v11h-3z" />
  </svg>
);

type FeedbackState = 'idle' | 'submitting' | 'submitted' | 'error';

export default function FeedbackButtons({ messageId, userQuery, botReply }: FeedbackButtonsProps) {
  const [rating, setRating] = useState<'up' | 'down' | null>(null);
  const [feedbackState, setFeedbackState] = useState<FeedbackState>('idle');
  const [showComment, setShowComment] = useState(false);
  const [comment, setComment] = useState('');
  const [commentSent, setCommentSent] = useState(false);
  const [, startTransition] = useTransition();

  // React 19 useOptimistic: flip the rating visually before the network
  // request resolves, then roll back automatically if submitFeedback()
  // fails.  Gives the user an instant "thumbs up received" affordance
  // even on slow networks.
  const [optimisticRating, setOptimisticRating] = useOptimistic<
    'up' | 'down' | null,
    'up' | 'down' | null
  >(rating, (_current, next) => next);

  const handleRate = useCallback((value: 'up' | 'down') => {
    if (feedbackState === 'submitted' || feedbackState === 'submitting') return;
    // Optimistic flip must live inside a transition in React 19
    startTransition(async () => {
      setOptimisticRating(value);
      setFeedbackState('submitting');
      trackFeedbackGiven(value);

      const result = await submitFeedback(messageId, value, '', userQuery, botReply);
      if (result) {
        setRating(value);
        setFeedbackState('submitted');
        if (value === 'down') {
          setShowComment(true);
        }
      } else {
        // Roll back: clearing the optimistic state on error
        setOptimisticRating(null);
        setFeedbackState('error');
      }
    });
  }, [feedbackState, messageId, userQuery, botReply, setOptimisticRating]);

  const handleCommentSubmit = useCallback(async () => {
    if (!comment.trim() || !rating) return;
    // Use PATCH to update existing feedback comment (no duplicate entry)
    const ok = await updateFeedbackComment(messageId, comment.trim());
    if (ok) {
      setCommentSent(true);
      setShowComment(false);
      setComment('');
    }
  }, [comment, rating, messageId]);

  const handleRetry = useCallback(() => {
    setFeedbackState('idle');
    setRating(null);
  }, []);

  const isDisabled = feedbackState === 'submitted' || feedbackState === 'submitting';

  return (
    <div className="feedback-container" role="group" aria-label="Response feedback">
      <div className="feedback-buttons">
        <button
          className={`feedback-btn ${optimisticRating === 'up' ? 'active up' : ''}`}
          onClick={() => handleRate('up')}
          disabled={isDisabled}
          aria-label="Helpful response"
          aria-pressed={optimisticRating === 'up'}
          title="Helpful"
        >
          <ThumbsUpIcon filled={optimisticRating === 'up'} />
        </button>
        <button
          className={`feedback-btn ${optimisticRating === 'down' ? 'active down' : ''}`}
          onClick={() => handleRate('down')}
          disabled={isDisabled}
          aria-label="Unhelpful response"
          aria-pressed={optimisticRating === 'down'}
          title="Not helpful"
        >
          <ThumbsDownIcon filled={optimisticRating === 'down'} />
        </button>

        {feedbackState === 'submitting' && (
          <span className="feedback-status" aria-live="polite">Sending...</span>
        )}
        {feedbackState === 'submitted' && !showComment && (
          <span className="feedback-thanks" aria-live="polite">
            {commentSent ? 'Thanks for your detailed feedback' : 'Thanks for your feedback'}
          </span>
        )}
        {feedbackState === 'error' && (
          <span className="feedback-error" aria-live="assertive">
            Failed to send{' '}
            <button className="feedback-retry" onClick={handleRetry}>Retry</button>
          </span>
        )}
      </div>

      {showComment && (
        <div className="feedback-comment" role="form" aria-label="Additional feedback">
          <input
            className="feedback-comment-input"
            placeholder="What could be improved? (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleCommentSubmit();
              }
            }}
            maxLength={500}
            aria-label="Feedback comment"
            autoFocus
          />
          <div className="feedback-comment-actions">
            <button className="feedback-btn small" onClick={handleCommentSubmit} disabled={!comment.trim()}>
              Send
            </button>
            <button
              className="feedback-btn small"
              onClick={() => { setShowComment(false); setComment(''); }}
            >
              Skip
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
