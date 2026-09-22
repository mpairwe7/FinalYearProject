'use client';

import React, { useEffect, useRef } from 'react';
import { useTranslation } from '@/lib/i18n';
import { useChatStore } from '@/store/useChatStore';
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
    currentCaption,
    error,
    closeCall,
    startCall,
    hangup,
    requestOfficer,
    toggleMute,
  } = useCall();

  const captionEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (captionEndRef.current && typeof captionEndRef.current.scrollIntoView === 'function') {
      captionEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [currentCaption]);

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

              {/* Live Captions with ARIA live region */}
              <div
                className="call-captions-box"
                aria-live="polite"
                aria-atomic="false"
              >
                {currentCaption ? (
                  <div>
                    <div
                      className={`call-caption-speaker ${
                        currentCaption.speaker === 'caller'
                          ? 'call-caption-speaker--caller'
                          : currentCaption.speaker === 'officer'
                          ? 'call-caption-speaker--officer'
                          : ''
                      }`}
                    >
                      {currentCaption.speaker === 'caller'
                        ? 'You'
                        : currentCaption.speaker === 'officer'
                        ? officerName || 'Officer'
                        : t('call.assistantName')}
                    </div>
                    <div>{currentCaption.text}</div>
                  </div>
                ) : (
                  <div style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                    {status === 'dialing'
                      ? 'Connecting call…'
                      : status === 'ended'
                      ? t('call.thankYou')
                      : 'Listening… speak your tax question naturally.'}
                  </div>
                )}
                <div ref={captionEndRef} />
              </div>

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
