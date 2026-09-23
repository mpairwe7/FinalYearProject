'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from '@/lib/i18n';
import { useChatStore } from '@/store/useChatStore';
import type { CaptionEntry } from '@/store/useCallStore';
import { useCall } from '@/hooks/useCall';
import { CallOrb } from '@/components/call/CallOrb';
import {
  ChevronDownIcon,
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

  const live = status === 'ai' || status === 'transferring' || status === 'officer';

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
                : status === 'transferring'
                ? 'call-status-pill--transferring'
                : ''
            }`}
          >
            <span className="call-status-dot" />
            <span>
              {status === 'dialing' && t('call.dialing')}
              {status === 'ai' && t('call.connected')}
              {status === 'transferring' && t('call.transferring')}
              {status === 'officer' &&
                (officerName ? `Officer ${officerName}` : t('call.officerSpeaking'))}
              {status === 'ended' && t('call.ended')}
            </span>
          </div>
          <div className="call-header-meta">
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
