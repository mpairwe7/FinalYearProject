"use client";

import React, { useState, useCallback, useOptimistic, useTransition } from 'react';
import { useTranslation } from '../lib/i18n';
import { submitFeedback, updateFeedbackComment, trackFeedbackGiven } from '../store/useAnalyticsStore';

interface FeedbackButtonsProps {
  messageId: string;
  userQuery: string;
  botReply: string;
  /** Route that produced the reply being rated (e.g. "workflow"). */
  retrievalMode?: string;
  /** Guided journey and step the reply belongs to, when it asked a step. */
  workflowId?: string;
  stepId?: string;
  /** The conversation the answer is in, and the answer's `turn_id` from the
   *  chat response: the audit reads a conversation's ratings in order. */
  conversationId?: string | null;
  turnId?: string;
  /** The answer's position in its conversation, 1 for the first. */
  answerNumber?: number;
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

export default function FeedbackButtons({
  messageId,
  userQuery,
  botReply,
  retrievalMode,
  workflowId,
  stepId,
  conversationId,
  turnId,
  answerNumber,
}: FeedbackButtonsProps) {
  const t = useTranslation();
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

      const result = await submitFeedback(messageId, value, '', userQuery, botReply, {
        retrievalMode,
        workflowId,
        stepId,
        conversationId,
        turnId,
        answerNumber,
      });
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
  }, [feedbackState, messageId, userQuery, botReply, retrievalMode, workflowId, stepId, conversationId, turnId, answerNumber, setOptimisticRating]);

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
    <div className="feedback-container" role="group" aria-label={t('message.feedbackGroup')}>
      <div className="feedback-buttons">
        <button
          className={`feedback-btn ${optimisticRating === 'up' ? 'active up' : ''}`}
          onClick={() => handleRate('up')}
          disabled={isDisabled}
          aria-label={t('message.rateHelpful')}
          aria-pressed={optimisticRating === 'up'}
          title={t('message.helpful')}
        >
          <ThumbsUpIcon filled={optimisticRating === 'up'} />
        </button>
        <button
          className={`feedback-btn ${optimisticRating === 'down' ? 'active down' : ''}`}
          onClick={() => handleRate('down')}
          disabled={isDisabled}
          aria-label={t('message.rateNotHelpful')}
          aria-pressed={optimisticRating === 'down'}
          title={t('message.notHelpful')}
        >
          <ThumbsDownIcon filled={optimisticRating === 'down'} />
        </button>

        {feedbackState === 'submitting' && (
          <span className="feedback-status" aria-live="polite">{t('message.feedbackSending')}</span>
        )}
        {feedbackState === 'submitted' && !showComment && (
          <span className="feedback-thanks" aria-live="polite">
            {commentSent ? t('message.feedbackThanksDetailed') : t('message.feedbackThanks')}
          </span>
        )}
        {feedbackState === 'error' && (
          <span className="feedback-error" aria-live="assertive">
            {t('message.feedbackFailed')}{' '}
            <button className="feedback-retry" onClick={handleRetry}>{t('message.feedbackRetry')}</button>
          </span>
        )}
      </div>

      {showComment && (
        <div className="feedback-comment" role="form" aria-label={t('message.feedbackCommentLabel')}>
          <input
            className="feedback-comment-input"
            placeholder={t('message.feedbackCommentPlaceholder')}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleCommentSubmit();
              }
            }}
            maxLength={500}
            aria-label={t('message.feedbackCommentLabel')}
            autoFocus
          />
          <div className="feedback-comment-actions">
            <button className="feedback-btn small" onClick={handleCommentSubmit} disabled={!comment.trim()}>
              {t('message.feedbackSend')}
            </button>
            <button
              className="feedback-btn small"
              onClick={() => { setShowComment(false); setComment(''); }}
            >
              {t('message.feedbackSkip')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
