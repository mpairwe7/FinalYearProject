import React from 'react';
import Link from 'next/link';
import { CallRecord } from '@/services/callsApi';
import { useCallLive, useOfficerAudio, useReviewCall } from '@/hooks/useCalls';
import { CallTranscript } from './CallTranscript';
import { CallSummaryCard } from './CallSummaryCard';
import { CallMetricsCard } from './CallMetricsCard';
import { CallReviewForm } from './CallReviewForm';
import { PhoneIcon, MicIcon, MicOffIcon } from '@/components/Icons';
import { callLanguageName } from '@/lib/callLanguage';

interface CallCaseProps {
  initialCall: CallRecord;
  userRole?: string;
}

export function CallCase({ initialCall, userRole = 'ura_staff' }: CallCaseProps) {
  const callId = initialCall.call_id;
  const { call: liveCall, turns: liveTurns, interimCaption } = useCallLive(callId);
  const call = liveCall || initialCall;
  const turns = liveTurns.length > 0 ? liveTurns : initialCall.turns || [];

  const { isBridged, isMuted, error: audioError, takeCall, leaveAudio, toggleMute } =
    useOfficerAudio(callId);
  const reviewMutation = useReviewCall();

  const canAct = userRole !== 'ura_auditor';
  const languageName = callLanguageName(call.locale);
  const languagesUsed = (call.summary?.languages_used ?? []).map(callLanguageName);
  const isTransferring = call.status === 'transferring';
  const isEnded = call.status === 'ended';

  const handleReviewSubmit = async (rating: number, note: string) => {
    await reviewMutation.mutateAsync({ callId, rating, note });
  };

  return (
    <div className="st-case-container" style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      {/* Case Header */}
      <div className="st-case-header">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700 }}>
              Call #{call.call_id.slice(0, 10)}
            </h3>
            <span
              className={`st-pill ${
                isTransferring
                  ? 'st-pill--urgent st-pill--pulse'
                  : call.status === 'bridged'
                  ? 'st-pill--assigned'
                  : isEnded
                  ? 'st-pill--resolved'
                  : 'st-pill--new'
              }`}
            >
              {call.status}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', fontSize: '0.8125rem', color: 'var(--text-secondary)' }}>
            <span>Channel: {call.channel}</span>
            <span className="st-chip--language" title="Call language">
              {`${languageName} caller`}
            </span>
            {languagesUsed.length > 1 && <span>Languages: {languagesUsed.join(' → ')}</span>}
            {call.ticket_id && (
              <Link
                href={`/admin/tickets?ticket=${encodeURIComponent(call.ticket_id)}`}
                style={{ color: '#2563eb', fontWeight: 600, textDecoration: 'underline' }}
              >
                View Ticket #{call.ticket_id.slice(0, 8)} →
              </Link>
            )}
          </div>
        </div>

        {/* Action Controls for Staff */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {canAct && !isEnded && (
            <>
              {!isBridged ? (
                <button
                  type="button"
                  onClick={takeCall}
                  className="st-btn-take-call"
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.375rem',
                    background: '#059669',
                    color: '#ffffff',
                    border: 'none',
                    borderRadius: '6px',
                    padding: '0.5rem 1rem',
                    fontWeight: 600,
                    fontSize: '0.875rem',
                    cursor: 'pointer',
                  }}
                >
                  <PhoneIcon size={16} />
                  Take Call
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={toggleMute}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.375rem',
                      background: isMuted ? '#fee2e2' : '#f3f4f6',
                      color: isMuted ? '#dc2626' : '#374151',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      padding: '0.45rem 0.75rem',
                      fontSize: '0.8125rem',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    {isMuted ? <MicOffIcon size={16} /> : <MicIcon />}
                    {isMuted ? 'Unmute' : 'Mute'}
                  </button>
                  <button
                    type="button"
                    onClick={leaveAudio}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.375rem',
                      background: '#dc2626',
                      color: '#ffffff',
                      border: 'none',
                      borderRadius: '6px',
                      padding: '0.45rem 0.875rem',
                      fontSize: '0.8125rem',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    Leave Call
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {audioError && (
        <div style={{ padding: '0.5rem 1rem', background: '#fee2e2', color: '#dc2626', fontSize: '0.8125rem' }}>
          {audioError}
        </div>
      )}

      {/* Handoff Brief if Transferring or Transferred */}
      {call.transferred ? (
        <div className="st-brief" style={{ margin: '0.75rem 1rem 0', padding: '0.75rem', borderRadius: '6px', background: '#fffbeb', border: '1px solid #fde68a' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#92400e', textTransform: 'uppercase' }}>
            Handoff Briefing: {call.transfer_reason || 'Taxpayer requested human escalation'}
          </div>
          <div style={{ fontSize: '0.8125rem', color: '#78350f', marginTop: '0.25rem' }}>
            {`${languageName} caller. `}
            The taxpayer was escalated to human staff. AI has held caller audio and prepared context below.
          </div>
        </div>
      ) : null}

      {/* Main Transcript Body */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <CallTranscript turns={turns} interimCaption={interimCaption} />
      </div>

      {/* Post-Call Summary, Metrics, and Review */}
      {isEnded && (
        <div style={{ padding: '1rem', borderTop: '1px solid var(--border-default, #e5e7eb)', maxHeight: '40%', overflowY: 'auto' }}>
          {call.summary && <CallSummaryCard summary={call.summary} />}
          {call.metrics && <CallMetricsCard metrics={call.metrics} />}
          {canAct && (
            <CallReviewForm
              callId={callId}
              initialRating={call.officer_rating}
              initialNote={call.officer_note}
              onSubmit={handleReviewSubmit}
              isSubmitting={reviewMutation.isPending}
            />
          )}
        </div>
      )}
    </div>
  );
}
