/**
 * The controls of a reply being read aloud (G200).
 *
 * A reply could only be stopped from its own Listen button, which a long
 * conversation scrolls out of view; with narration on, every reply started
 * reading by itself and nothing on screen paused it (WCAG 2.2 SC 1.4.2 asks for
 * a way to pause or stop audio that plays by itself). This bar sits on the
 * composer while anything is read: pause and resume, stop (also Escape), and
 * the sentence being heard. A reply that could not be read aloud says so here,
 * with a retry, instead of staying silent.
 */
import React from 'react';

import { useNarration } from '../hooks/useNarration';
import { useTranslation } from '../lib/i18n';
import { pausePlayback, resumePlayback } from '../services/voiceService';
import { CloseIcon, PauseIcon, PlayIcon, StopIcon } from './Icons';

interface NarrationBarProps {
  /** A reply is being read aloud, or its audio is being fetched. */
  active: boolean;
  /** Fetching: nothing to pause yet. */
  loading: boolean;
  onStop: () => void;
  /** The last read-aloud could not play at all. */
  failed: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}

export default function NarrationBar({ active, loading, onStop, failed, onRetry, onDismiss }: NarrationBarProps) {
  const t = useTranslation();
  const { paused, sentence } = useNarration();

  if (failed && !active) {
    return (
      <div className="narration-bar narration-bar-failed" data-testid="narration-failed">
        <p className="narration-label" role="status">{t('narration.failed')}</p>
        <button type="button" className="narration-btn narration-btn-text" onClick={onRetry}>
          {t('narration.retry')}
        </button>
        <button type="button" className="narration-btn" onClick={onDismiss} aria-label={t('narration.dismiss')}>
          <CloseIcon />
        </button>
      </div>
    );
  }
  if (!active) return null;

  return (
    <div className="narration-bar" role="region" aria-label={t('narration.region')} data-testid="narration-bar">
      <div className="narration-text">
        <span className="narration-label" aria-live="polite">
          {loading ? t('narration.loading') : paused ? t('narration.paused') : t('narration.reading')}
        </span>
        {/* The audio already says it; read out here as well, it would talk over itself. */}
        {sentence && !loading && (
          <span className="narration-sentence" aria-hidden="true">
            {sentence}
          </span>
        )}
      </div>
      <button
        type="button"
        className="narration-btn"
        onClick={() => void (paused ? resumePlayback() : pausePlayback())}
        disabled={loading}
        aria-label={paused ? t('narration.resume') : t('narration.pause')}
        data-tip={paused ? t('narration.resume') : t('narration.pause')}
      >
        {paused ? <PlayIcon /> : <PauseIcon />}
      </button>
      <button
        type="button"
        className="narration-btn"
        onClick={onStop}
        aria-label={t('narration.stop')}
        aria-keyshortcuts="Escape"
        data-tip={t('narration.stop')}
      >
        <StopIcon />
      </button>
    </div>
  );
}
