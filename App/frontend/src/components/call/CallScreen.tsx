'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { useChatStore } from '@/store/useChatStore';
import type { CallLanguage, CallLanguageSource, CaptionEntry } from '@/store/useCallStore';
import { useCall } from '@/hooks/useCall';
import { ticketRef as caseReference } from '@/lib/ticketUi';
import { CallOrb } from '@/components/call/CallOrb';
import {
  CheckIcon,
  ChevronDownIcon,
  GlobeIcon,
  MicIcon,
  MicOffIcon,
  PhoneIcon,
  PhoneOffIcon,
  UserIcon,
} from '@/components/Icons';
import '@/styles/call/call.css';

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

/** Distance from the bottom, in px, still treated as "following the call". */
const PIN_THRESHOLD = 48;

interface CallLanguageChipProps {
  language: CallLanguage;
  source: CallLanguageSource;
  languages: CallLanguage[];
  onPick: (language: CallLanguage) => void;
}

/**
 * "English · auto" — the language the assistant is answering in, and how it got
 * there. The call follows the caller's own speech on its own; the menu is for
 * a caller who would rather pin it (a choice holds for the rest of the call).
 */
function CallLanguageChip({ language, source, languages, onPick }: CallLanguageChipProps) {
  const t = useTranslation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const name = t(`call.lang.${language}`);
  const pinned = source === 'override' || source === 'explicit';
  const label = pinned ? t('call.languageChosen', { language: name }) : t('call.languageAuto', { language: name });

  return (
    <div className="call-lang" ref={rootRef}>
      <button
        type="button"
        className={`call-lang-chip${pinned ? ' call-lang-chip--pinned' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${t('call.languageMenu')}: ${label}`}
        onClick={() => setOpen((prev) => !prev)}
      >
        <GlobeIcon />
        <span>{label}</span>
        <ChevronDownIcon />
      </button>
      {open && (
        <div className="call-lang-menu" role="menu" aria-label={t('call.languageMenu')}>
          <p className="call-lang-menu-hint">{t('call.languageMenuHint')}</p>
          {languages.map((option) => (
            <button
              key={option}
              type="button"
              role="menuitemradio"
              aria-checked={option === language}
              className={`call-lang-option${option === language ? ' call-lang-option--active' : ''}`}
              onClick={() => {
                onPick(option);
                setOpen(false);
              }}
            >
              <span>{t(`call.lang.${option}`)}</span>
              {option === language && <CheckIcon />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function CallScreen() {
  const t = useTranslation();
  const locale = useChatStore((s) => s.locale);
  const {
    isOpen,
    status,
    duration,
    isMuted,
    officerName,
    ticketRef,
    captions,
    error,
    audioBlocked,
    resumeAudio,
    closeCall,
    startCall,
    hangup,
    requestOfficer,
    requestCallback,
    queuePosition,
    turnPhase,
    pendingOffer,
    speechRateSlow,
    hasMoreDetail,
    hasLastAnswer,
    toggleMute,
    languageDetection,
    languages,
    language,
    languageSource,
    setLanguage,
    sendControl,
    sendTextTurn,
    interruptCall,
  } = useCall();

  const [showTextInput, setShowTextInput] = useState(false);
  const [typedTurn, setTypedTurn] = useState('');

  const handleTypedSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const clean = typedTurn.trim();
    if (!clean) return;
    sendTextTurn(clean);
    setTypedTurn('');
    setShowTextInput(false);
  };

  const threadRef = useRef<HTMLDivElement | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  // Follow the conversation only while the caller is already at the bottom.
  // Scrolling back to re-read an earlier answer should not be yanked away by
  // the next caption — which, with live partials, now arrives twice a second.
  const pinnedToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const handleThreadScroll = useCallback(() => {
    const el = threadRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD;
    pinnedToBottom.current = atBottom;
    setShowJump(!atBottom);
  }, []);

  const jumpToLatest = useCallback(() => {
    const el = threadRef.current;
    pinnedToBottom.current = true;
    setShowJump(false);
    if (el) el.scrollTop = el.scrollHeight;
    if (threadEndRef.current?.scrollIntoView) {
      threadEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, []);

  useEffect(() => {
    if (!pinnedToBottom.current) return;
    const el = threadRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
    if (threadEndRef.current?.scrollIntoView) {
      threadEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [captions, status]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && (status === 'consent' || status === 'ended')) {
        event.preventDefault();
        closeCall();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, status, closeCall]);

  const speakerLabel = (speaker: CaptionEntry['speaker']): string => {
    if (speaker === 'caller') return t('call.you');
    if (speaker === 'officer') return officerName || t('call.officer');
    if (speaker === 'system') return t('call.systemNote');
    return t('call.assistantName');
  };

  if (!isOpen) return null;

  const live =
    status === 'ai' ||
    status === 'transferring' ||
    status === 'officer' ||
    status === 'on_hold' ||
    status === 'reconnecting';

  /* ---------------------------------------------------------------- pre-call */
  if (status === 'consent') {
    return (
      <div className="call-backdrop" role="dialog" aria-modal="true" aria-label={t('call.preTitle')}>
        <div className="call-card call-card--pre">
          <div className="call-pre-avatar">
            <PhoneIcon size={22} />
          </div>
          <h3 className="call-pre-title">{t('call.preTitle')}</h3>
          <p className="call-pre-subtitle">{t('call.preSubtitle')}</p>
          <p className="call-pre-consent" data-testid="call-consent-title" style={{ fontWeight: 600 }}>
            {t('call.consentTitle')}
          </p>
          <p className="call-pre-consent" data-testid="call-consent-text">
            {t('call.consentText')}
          </p>
          <p className="call-pre-consent">{t('call.preConsent')}</p>
          <p className="call-pre-consent" data-testid="call-tone-notice">
            {t('call.preConsentTone')}
          </p>
          <div className="call-pre-actions">
            <button type="button" className="call-btn call-btn--ghost" onClick={closeCall}>
              {t('call.consentDecline')}
            </button>
            <button
              type="button"
              className="call-btn call-btn--primary"
              onClick={() => startCall(locale)}
            >
              <PhoneIcon size={16} />
              {t('call.button')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  /* ------------------------------------------------------------- active call */
  return (
    <div
      className="call-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={t('call.assistantName')}
    >
      <div className="call-card call-card--live">
        <header className="call-header">
          <div
            className={`call-status-pill ${
              status === 'ai' || status === 'officer'
                ? 'call-status-pill--active'
                : status === 'transferring' || status === 'on_hold' || status === 'reconnecting'
                ? 'call-status-pill--transferring'
                : ''
            }`}
          >
            <span className="call-status-dot" />
            <span>
              {status === 'dialing' && t('call.dialing')}
              {status === 'ai' && t('call.connected')}
              {status === 'transferring' && t('call.transferring')}
              {status === 'on_hold' && t('call.onHold')}
              {status === 'reconnecting' && t('call.reconnecting')}
              {status === 'officer' &&
                (officerName ? t('call.officerNamed', { name: officerName }) : t('call.officerSpeaking'))}
              {status === 'ended' && t('call.ended')}
            </span>
          </div>
          <div className="call-header-meta">
            {languageDetection && (status === 'ai' || status === 'transferring') && (
              <CallLanguageChip
                language={language}
                source={languageSource}
                languages={languages}
                onPick={setLanguage}
              />
            )}
            {status === 'transferring' && ticketRef && (
              <span className="call-ticket-chip">{t('call.ticketChip', { ref: caseReference(ticketRef) })}</span>
            )}
            {status === 'transferring' && queuePosition !== null && (
              <span className="call-ticket-chip call-queue-chip" role="status" data-testid="call-queue-position">
                {t('call.queuePosition', { position: String(queuePosition) })}
              </span>
            )}
            {live && <span className="call-timer">{formatDuration(duration)}</span>}
          </div>
        </header>

        <div className="call-thread-wrap">
          <div
            className="call-thread"
            ref={threadRef}
            onScroll={handleThreadScroll}
            role="log"
            aria-label={t('call.transcriptLabel')}
            aria-live="polite"
            aria-atomic="false"
          >
            {captions.length === 0 && (
              <p className="call-thread-empty">
                {status === 'dialing' ? t('call.connecting') : t('call.listening')}
              </p>
            )}

            {captions.map((caption, index) => {
              const key = `${caption.turn_id ?? 'x'}-${index}`;
              const interim = caption.final === false;

              if (caption.speaker === 'caller') {
                return (
                  <div key={key} className="call-turn call-turn--caller">
                    <div className={`call-bubble${interim ? ' call-bubble--interim' : ''}`}>
                      {caption.text}
                      {interim && <span className="call-bubble-cursor" />}
                    </div>
                  </div>
                );
              }

              if (caption.speaker === 'system') {
                return (
                  <p key={key} className="call-system-note">
                    {caption.text}
                  </p>
                );
              }

              return (
                <div key={key} className="call-turn call-turn--agent">
                  {caption.speaker === 'officer' && (
                    <span className="call-speaker-chip">{speakerLabel(caption.speaker)}</span>
                  )}
                  <div className={`call-say${interim ? ' call-say--interim' : ''}`}>
                    {caption.text}
                  </div>
                </div>
              );
            })}

            {status === 'transferring' && (
              <p className="call-system-note">{t('call.transferring')}</p>
            )}
            {status === 'officer' && <p className="call-system-note">{t('call.bridgeActive')}</p>}
            {status === 'ended' && <p className="call-system-note">{t('call.thankYou')}</p>}

            <div ref={threadEndRef} />
          </div>

          {showJump && (
            <button type="button" className="call-jump" onClick={jumpToLatest}>
              <ChevronDownIcon />
              {t('call.jumpToLatest')}
            </button>
          )}
        </div>

        {error && <p className="call-error" role="alert">{error}</p>}
        {audioBlocked && status !== 'ended' && (
          <button type="button" className="call-audio-unlock" onClick={resumeAudio}>
            {t('call.tapToHear')}
          </button>
        )}

        {status === 'ai' && (
          <div className="call-controls-strip" role="toolbar" aria-label={t('call.assistantName')}>
            {turnPhase === 'speaking' && (
              <button
                type="button"
                className="call-chip-btn call-chip-btn--interrupt"
                onClick={interruptCall}
                data-testid="call-interrupt-btn"
              >
                {t('call.stopSpeaking')}
              </button>
            )}
            {pendingOffer && (
              <>
                <button
                  type="button"
                  className="call-chip-btn call-chip-btn--primary"
                  onClick={() => sendControl('accept_offer')}
                  data-testid="call-offer-yes"
                >
                  {t('call.offerYes')}
                </button>
                <button
                  type="button"
                  className="call-chip-btn"
                  onClick={() => sendControl('decline_offer')}
                  data-testid="call-offer-no"
                >
                  {t('call.offerNo')}
                </button>
              </>
            )}
            {hasLastAnswer && (
              <button
                type="button"
                className="call-chip-btn"
                onClick={() => sendControl('repeat')}
                data-testid="call-repeat-btn"
              >
                {t('call.repeatLast')}
              </button>
            )}
            {hasMoreDetail && pendingOffer !== 'more' && (
              <button
                type="button"
                className="call-chip-btn"
                onClick={() => sendControl('more_detail')}
                data-testid="call-more-detail-btn"
              >
                {t('call.moreDetail')}
              </button>
            )}
            <button
              type="button"
              className={`call-chip-btn${speechRateSlow ? ' is-active' : ''}`}
              aria-pressed={speechRateSlow}
              onClick={() => sendControl(speechRateSlow ? 'normal_speed' : 'slower')}
              data-testid="call-pace-btn"
            >
              {speechRateSlow ? t('call.normalPace') : t('call.slowerPace')}
            </button>
            <button
              type="button"
              className={`call-chip-btn${showTextInput ? ' is-active' : ''}`}
              aria-expanded={showTextInput}
              onClick={() => setShowTextInput((prev) => !prev)}
              data-testid="call-type-toggle-btn"
            >
              {t('call.typeQuestion')}
            </button>
          </div>
        )}

        {status === 'ai' && showTextInput && (
          <form className="call-text-form" onSubmit={handleTypedSubmit} data-testid="call-text-form">
            <input
              type="text"
              className="call-text-input"
              maxLength={500}
              value={typedTurn}
              onChange={(e) => setTypedTurn(e.target.value)}
              placeholder={t('call.typePlaceholder')}
              aria-label={t('call.typePlaceholder')}
              data-testid="call-text-input"
            />
            <button
              type="submit"
              className="call-text-send"
              disabled={!typedTurn.trim()}
              data-testid="call-text-send"
            >
              {t('call.sendTyped')}
            </button>
          </form>
        )}

        <footer className="call-dock">
          {status !== 'ended' ? (
            <>
              <div className="call-dock-side">
                <button
                  type="button"
                  className={`call-ctl${isMuted ? ' call-ctl--muted' : ''}`}
                  onClick={toggleMute}
                  aria-label={isMuted ? t('call.unmute') : t('call.mute')}
                  title={isMuted ? t('call.unmute') : t('call.mute')}
                >
                  {isMuted ? <MicOffIcon size={20} /> : <MicIcon />}
                </button>
              </div>

              <CallOrb active={live} />

              <div className="call-dock-side call-dock-side--end">
                {(status === 'ai' || status === 'transferring') && (
                  // One button that changes its job when the wait starts, so a
                  // keyboard user who asked for an officer keeps their focus.
                  <button
                    type="button"
                    className="call-ctl"
                    onClick={status === 'ai' ? requestOfficer : requestCallback}
                    aria-label={status === 'ai' ? t('call.talkOfficer') : t('call.callMeBack')}
                    title={status === 'ai' ? t('call.talkOfficer') : t('call.callMeBack')}
                  >
                    {status === 'ai' ? <UserIcon /> : <PhoneIcon size={20} />}
                  </button>
                )}
                <button
                  type="button"
                  className="call-ctl call-ctl--hangup"
                  onClick={hangup}
                  aria-label={t('call.hangup')}
                  title={t('call.hangup')}
                >
                  <PhoneOffIcon size={20} />
                </button>
              </div>
            </>
          ) : (
            <button type="button" className="call-btn call-btn--primary call-btn--block" onClick={closeCall}>
              {t('common.close')}
            </button>
          )}
        </footer>

        {status !== 'ended' && (
          <p className="call-hint">
            {isMuted
              ? t('call.mutedHint')
              : status === 'ai' && turnPhase === 'thinking'
                ? t('call.phaseThinking')
                : status === 'ai' && turnPhase === 'speaking'
                  ? t('call.phaseSpeaking')
                  : t('call.listeningHint')}
          </p>
        )}
      </div>
    </div>
  );
}
