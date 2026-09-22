'use client';

import React, { useEffect, useRef } from 'react';
import { useTranslation } from '@/lib/i18n';
import { useChatStore } from '@/store/useChatStore';
import type { CaptionEntry } from '@/store/useCallStore';
import { useCall } from '@/hooks/useCall';
import {
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
    currentCaption,
    error,
    closeCall,
    startCall,
    hangup,
    requestOfficer,
    toggleMute,
  } = useCall();

  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  // Follow the conversation only while the caller is already at the bottom.
  // Scrolling back to re-read an earlier answer should not be yanked away by
  // the next caption — which, with live partials, now arrives twice a second.
  const pinnedToBottom = useRef(true);

  const handleTranscriptScroll = () => {
    const el = transcriptRef.current;
    if (!el) return;
    pinnedToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  useEffect(() => {
    if (!pinnedToBottom.current) return;
    const el = transcriptRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
    if (transcriptEndRef.current && typeof transcriptEndRef.current.scrollIntoView === 'function') {
      transcriptEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [captions]);

  const speakerLabel = (speaker: CaptionEntry['speaker']): string => {
    if (speaker === 'caller') return t('call.you');
    if (speaker === 'officer') return officerName || t('call.officer');
    if (speaker === 'system') return t('call.systemNote');
    return t('call.assistantName');
  };

  if (!isOpen) return null;

  return (
    <div
      className="call-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={t('call.assistantName')}
    >
      <div className="call-card">
        {status === 'consent' ? (
          <div className="call-consent-box">
            <div className="call-avatar-wrap" style={{ margin: '0 auto' }}>
              <div className="call-avatar">
                <PhoneIcon size={32} />
              </div>
            </div>
            <h3 className="call-consent-title">{t('call.consentTitle')}</h3>
            <p className="call-consent-text">{t('call.consentText')}</p>
            <div className="call-consent-buttons">
              <button
                type="button"
                className="call-btn-decline"
                onClick={closeCall}
              >
                {t('call.consentDecline')}
              </button>
              <button
                type="button"
                className="call-btn-accept"
                onClick={() => startCall(locale)}
              >
                <PhoneIcon size={16} />
                {t('call.consentAccept')}
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* Header with status pill and timer */}
            <div className="call-header">
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
                  {status === 'officer' && (officerName ? `Officer ${officerName}` : t('call.officerSpeaking'))}
                  {status === 'ended' && t('call.ended')}
                </span>
              </div>
              {(status === 'ai' || status === 'transferring' || status === 'officer') && (
                <span className="call-timer">{formatDuration(duration)}</span>
              )}
            </div>

            {/* Call Body */}
            <div className="call-body">
              <div className="call-avatar-wrap">
                <div
                  className={`call-avatar ${
                    currentCaption?.speaker === 'assistant' || currentCaption?.speaker === 'officer'
                      ? 'call-avatar--speaking'
                      : ''
                  }`}
                >
                  {status === 'officer' ? <UserIcon /> : <PhoneIcon size={28} />}
                </div>
                <div className="call-avatar-ring" />
              </div>

              <div>
                <h2 className="call-persona-title">
                  {status === 'officer'
                    ? officerName || 'URA Support Officer'
                    : t('call.assistantName')}
                </h2>
                <p className="call-persona-subtitle">
                  {status === 'dialing' && '0800 117 000 (Toll-free)'}
                  {status === 'ai' && 'AI Phone Receptionist'}
                  {status === 'transferring' &&
                    (ticketRef ? `Ticket: ${ticketRef}` : 'Holding for available officer…')}
                  {status === 'officer' && 'Live Audio Bridge Active'}
                  {status === 'ended' && t('call.thankYou')}
                </p>
              </div>

              {/* Running transcript of the call: both sides, oldest first, in
                  an ARIA live region so a screen reader announces each new
                  line as it lands. */}
              <div
                className="call-transcript"
                ref={transcriptRef}
                onScroll={handleTranscriptScroll}
                role="log"
                aria-label={t('call.transcriptLabel')}
                aria-live="polite"
                aria-atomic="false"
              >
                {captions.length === 0 ? (
                  <div className="call-transcript-empty">
                    {status === 'dialing'
                      ? t('call.connecting')
                      : status === 'ended'
                      ? t('call.thankYou')
                      : t('call.listening')}
                  </div>
                ) : (
                  captions.map((caption, index) => (
                    <div
                      key={`${caption.turn_id ?? 'x'}-${index}`}
                      className={`call-turn call-turn--${
                        caption.speaker === 'caller' ? 'caller' : 'agent'
                      }`}
                    >
                      <span className={`call-turn-speaker call-turn-speaker--${caption.speaker}`}>
                        {speakerLabel(caption.speaker)}
                      </span>
                      <div
                        className={`call-bubble call-bubble--${caption.speaker}${
                          caption.final === false ? ' call-bubble--interim' : ''
                        }`}
                      >
                        {caption.text}
                        {caption.final === false && <span className="call-bubble-cursor" />}
                      </div>
                    </div>
                  ))
                )}
                <div ref={transcriptEndRef} />
              </div>

              {status !== 'ended' && (
                <p className="call-transcript-hint">
                  {isMuted ? t('call.mutedHint') : t('call.listeningHint')}
                </p>
              )}

              {error && (
                <div style={{ color: '#dc2626', fontSize: '0.8125rem', marginTop: '0.25rem' }}>
                  {error}
                </div>
              )}

              {/* Actions bar */}
              <div className="call-actions">
                {status !== 'ended' ? (
                  <>
                    {/* Mute Button */}
                    <button
                      type="button"
                      className={`call-action-btn ${isMuted ? 'call-action-btn--muted' : ''}`}
                      onClick={toggleMute}
                      aria-label={isMuted ? t('call.unmute') : t('call.mute')}
                    >
                      <div className="call-action-btn-circle">
                        {isMuted ? <MicOffIcon size={20} /> : <MicIcon />}
                      </div>
                      <span>{isMuted ? t('call.unmute') : t('call.mute')}</span>
                    </button>

                    {/* Talk to an Officer Button */}
                    {status === 'ai' && (
                      <button
                        type="button"
                        className="call-action-btn"
                        onClick={requestOfficer}
                        aria-label={t('call.talkOfficer')}
                      >
                        <div className="call-action-btn-circle">
                          <UserIcon />
                        </div>
                        <span>{t('call.talkOfficer')}</span>
                      </button>
                    )}

                    {/* Hang Up Button */}
                    <button
                      type="button"
                      className="call-action-btn call-action-btn--hangup"
                      onClick={hangup}
                      aria-label={t('call.hangup')}
                    >
                      <div className="call-action-btn-circle">
                        <PhoneOffIcon size={20} />
                      </div>
                      <span>{t('call.hangup')}</span>
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="call-btn-accept"
                    onClick={closeCall}
                    style={{ width: '100%' }}
                  >
                    {t('common.close')}
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
