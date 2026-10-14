'use client';

/**
 * "How was your chat with the officer?" — shown once, when an officer ends a
 * live chat.
 *
 * The rating is the officer's service, read by administrators in the quality
 * audit, so it is asked at the one moment the taxpayer has just experienced
 * it and never again: Skip is a real answer (the audit counts it), and closing
 * the dialog skips. A comment is optional and only sent with a rating.
 */
import React, { useState } from 'react';
import { ModalDialog } from './ModalDialog';
import { SatisfactionScale } from './rating/SatisfactionScale';
import { useTranslation } from '../lib/i18n';
import './serviceRatingDialog.css';

const LEVEL_KEYS = ['rating.level1', 'rating.level2', 'rating.level3', 'rating.level4', 'rating.level5'] as const;

export function ServiceRatingDialog({
  officerName,
  onSubmit,
  onSkip,
}: {
  officerName?: string;
  /** Resolves when the rating is saved; rejects to show the failure. */
  onSubmit: (score: number, comment: string) => Promise<void>;
  onSkip: () => void;
}) {
  const t = useTranslation();
  const [score, setScore] = useState<number | null>(null);
  const [comment, setComment] = useState('');
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState(false);

  const submit = async () => {
    if (score == null || sending) return;
    setSending(true);
    setFailed(false);
    try {
      await onSubmit(score, comment.trim());
    } catch {
      setFailed(true);
      setSending(false);
    }
  };

  return (
    <ModalDialog labelledBy="service-rating-title" className="service-rating-dialog" onClose={onSkip}>
      <form
        className="service-rating-body"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h2 id="service-rating-title" className="service-rating-title">
          {officerName ? t('rating.titleNamed', { name: officerName }) : t('rating.title')}
        </h2>
        <p className="service-rating-intro">{t('rating.intro')}</p>
        <SatisfactionScale
          value={score}
          onChange={setScore}
          labels={LEVEL_KEYS.map((key) => t(key))}
          groupLabel={t('rating.scaleLabel')}
          disabled={sending}
          name="service-rating"
        />
        {score != null ? (
          <label className="service-rating-comment">
            <span>{t('rating.commentLabel')}</span>
            <textarea
              rows={3}
              maxLength={1000}
              value={comment}
              placeholder={t('rating.commentPlaceholder')}
              onChange={(event) => setComment(event.target.value)}
              disabled={sending}
            />
          </label>
        ) : null}
        {failed ? (
          <p className="service-rating-error" role="alert">
            {t('rating.failed')}
          </p>
        ) : null}
        <div className="service-rating-actions">
          <button type="button" className="service-rating-skip" onClick={onSkip} disabled={sending}>
            {t('rating.skip')}
          </button>
          <button type="submit" className="service-rating-submit" disabled={score == null || sending}>
            {sending ? t('rating.sending') : t('rating.submit')}
          </button>
        </div>
      </form>
    </ModalDialog>
  );
}
