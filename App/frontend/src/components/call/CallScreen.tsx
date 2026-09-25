'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { useChatStore } from '@/store/useChatStore';
import type { CallLanguage, CallLanguageSource, CaptionEntry } from '@/store/useCallStore';
import { useCall } from '@/hooks/useCall';
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
    closeCall,
    startCall,
    hangup,
    requestOfficer,
    toggleMute,
    languageDetection,
    languages,
    language,
    languageSource,
    setLanguage,
  } = useCall();

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
          <p className="call-pre-consent">{t('call.preConsent')}</p>
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
                (officerName ? `Officer ${officerName}` : t('call.officerSpeaking'))}
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
              <span className="call-ticket-chip">{`Ticket: ${ticketRef}`}</span>
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

        {error && <p className="call-error">{error}</p>}

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
                {status === 'ai' && (
                  <button
                    type="button"
                    className="call-ctl"
                    onClick={requestOfficer}
                    aria-label={t('call.talkOfficer')}
                    title={t('call.talkOfficer')}
                  >
                    <UserIcon />
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
          <p className="call-hint">{isMuted ? t('call.mutedHint') : t('call.listeningHint')}</p>
        )}
      </div>
    </div>
  );
}
