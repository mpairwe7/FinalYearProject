'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useShallow } from 'zustand/react/shallow';
import { ErrorState, SkeletonRows } from '@/components/ops/States';
import { useCall, useCallBrief, useCallLive, useReviewCall } from '@/hooks/useCalls';
import { formatClock, useNow } from '@/hooks/useNow';
import { callLanguageName } from '@/lib/callLanguage';
import { callTopicLabel, isRaisedPriority } from '@/lib/callTopic';
import { ClaimConflictError } from '@/services/callsApi';
import { takeCall } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { CallBriefCard } from './CallBriefCard';
import { CallControls } from './CallControls';
import { CallMetricsCard } from './CallMetricsCard';
import { CallReviewForm } from './CallReviewForm';
import { CallSummaryCard } from './CallSummaryCard';
import { CallTranscript } from './CallTranscript';
import { LevelMeter } from './console/LevelMeter';
import { WaitRing } from './console/WaitRing';
import { TransferDialog } from './TransferDialog';
import { WrapUpPanel } from './WrapUpPanel';

/** Which of the plan's workspace states (§3.3) a call is in, for this officer. */
export type WorkspaceState = 'mine' | 'wrap_up' | 'waiting' | 'claimed' | 'ai' | 'officer' | 'ended';

const FLASH_MS = 1200;

export function CallWorkspace({ callId, role }: { callId: string; role: string }) {
  const canTake = role === 'ura_staff' || role === 'ura_admin';
  const now = useNow();
  const { data: detail, isLoading, error, refetch } = useCall(callId);
  const live = useCallLive(callId);
  const { brief, loading: briefLoading, refresh, refreshing } = useCallBrief(callId, live.brief);
  const { lobby, activeCall, confirmEnd, setConfirmEnd, setDeskFocus } = useCallConsoleStore(
    useShallow((s) => ({
      lobby: s.calls[callId],
      activeCall: s.activeCall,
      confirmEnd: s.confirmEnd,
      setConfirmEnd: s.setConfirmEnd,
      setDeskFocus: s.setDeskFocus,
    })),
  );
  const review = useReviewCall();
  const [flashSeq, setFlashSeq] = useState<number | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [takeError, setTakeError] = useState<string | null>(null);
  const muteRef = useRef<HTMLButtonElement>(null);

  // The dock stays out of the way while the Desk shows this call.
  useEffect(() => {
    setDeskFocus(callId);
    return () => setDeskFocus(null);
  }, [callId, setDeskFocus]);

  const call = live.call || detail || null;
  const turns = live.turns.length ? live.turns : detail?.turns || [];
  const isWrapUp =
    (activeCall !== null && activeCall.callId === callId && activeCall.state === 'wrap_up') ||
    Boolean(call?.status === 'ended' && !call.wrapup_at && activeCall?.callId === callId);
  const mine = activeCall !== null && activeCall.callId === callId && activeCall.state !== 'idle' && !isWrapUp;
  const state: WorkspaceState | null = isWrapUp
    ? 'wrap_up'
    : mine
      ? 'mine'
      : !call
        ? null
        : call.status === 'ended'
          ? 'ended'
          : call.status === 'transferring'
            ? lobby?.claimed_by
              ? 'claimed'
              : 'waiting'
            : call.status === 'bridged'
              ? 'officer'
              : 'ai';

  // Joined: focus lands on the controls (plan §9, accessibility).
  const bridged = mine && activeCall?.state === 'bridged';
  useEffect(() => {
    if (bridged) muteRef.current?.focus();
  }, [bridged]);

  // Shortcut T opens Transfer dialog
  useEffect(() => {
    if (!bridged) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 't' || e.key === 'T') && !e.metaKey && !e.ctrlKey) {
        const target = e.target as HTMLElement | null;
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
        e.preventDefault();
        setTransferOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [bridged]);

  if (isLoading && !call) return <SkeletonRows rows={5} />;
  if (error && !call) return <ErrorState title="Could not load this call" onRetry={() => void refetch()} />;
  if (!call || !state) return null;

  const showEvidence = (seq: number) => {
    setTranscriptOpen(true);
    setFlashSeq(seq);
    setTimeout(() => setFlashSeq((s) => (s === seq ? null : s)), FLASH_MS);
    requestAnimationFrame(() =>
      document.getElementById(`turn-${seq}`)?.scrollIntoView({ block: 'center' }),
    );
  };

  const take = async () => {
    setBusy(true);
    setTakeError(null);
    try {
      await takeCall(callId);
    } catch (err) {
      setTakeError(
        err instanceof ClaimConflictError
          ? `Taken by ${err.officerName || 'another officer'}`
          : (err as Error).message || 'Could not take the call',
      );
    } finally {
      setBusy(false);
    }
  };

  const topic = callTopicLabel(lobby?.topic || call.topic) || brief?.topic || 'Tax question';
  const language = callLanguageName(lobby?.language || call.locale);
  const priority = lobby?.priority || call.priority;
  const officerName = lobby?.officer_name || lobby?.claimed_name || call.officer_id || 'an officer';

  const header = (() => {
    switch (state) {
      case 'waiting':
        return (
          <>
            <span className="cw-title">Caller waiting</span>
            <WaitRing since={lobby?.waiting_since ?? call.transfer_requested_at ?? call.started_at} />
          </>
        );
      case 'claimed':
        return <span className="cw-title">{officerName} is joining</span>;
      case 'officer':
        return <span className="cw-title">With {officerName}</span>;
      case 'ai':
        return (
          <span className="cw-title">
            AI handling · <span className="cw-clock">{formatClock(now / 1000 - call.started_at)}</span>
          </span>
        );
      case 'mine':
        return (
          <>
            <span className="cw-title">
              {activeCall?.state === 'bridged'
                ? `On call ${formatClock((now - (activeCall?.since ?? now)) / 1000)}`
                : activeCall?.state === 'ending'
                  ? 'Ending the call…'
                  : 'Connecting…'}{' '}
              · {topic}
            </span>
            {activeCall?.state === 'bridged' && (
              <span className="cw-levels">
                You <LevelMeter source="input" label="Your microphone" /> Caller{' '}
                <LevelMeter source="output" label="The caller" />
              </span>
            )}
          </>
        );
      default:
        return (
          <span className="cw-title">
            Ended · {formatClock((call.ended_at ?? call.started_at) - call.started_at)}
          </span>
        );
    }
  })();

  const transcript = (
    <CallTranscript turns={turns} interimCaption={live.interimCaption} startedAt={call.started_at} flashSeq={flashSeq} />
  );

  return (
    <article className={`cw cw--${state}`} aria-label={`Call ${topic}`}>
      <header className="cw-head">
        <div className="cw-head-main">{header}</div>
        <div className="cw-head-meta">
          <span className="st-chip--language">{language}</span>
          {isRaisedPriority(priority) && <span className={`call-chip call-chip--${priority}`}>{priority}</span>}
          {call.ticket_id && (
            <Link className="cw-ticket" href={`/admin/tickets?ticket=${encodeURIComponent(call.ticket_id)}`}>
              Ticket #{call.ticket_id.slice(0, 8)}
            </Link>
          )}
        </div>
      </header>

      {state === 'waiting' && (
        <>
          <CallBriefCard
            brief={brief}
            loading={briefLoading}
            refreshing={refreshing}
            onRefresh={refresh}
            onEvidence={showEvidence}
          />
          <div className="cw-actions">
            {canTake && (
              <button type="button" className="cc-btn cc-btn--primary" data-action="take" onClick={take} disabled={busy}>
                {busy ? 'Connecting…' : 'Take call'} <kbd>A</kbd>
              </button>
            )}
            {takeError && <span className="cw-error" role="alert">{takeError}</span>}
            <button
              type="button"
              className="cc-btn cc-btn--quiet"
              aria-expanded={transcriptOpen}
              onClick={() => setTranscriptOpen((o) => !o)}
            >
              Transcript ({turns.length} turns) {transcriptOpen ? '▴' : '▾'}
            </button>
          </div>
          {transcriptOpen && <div className="cw-transcript">{transcript}</div>}
        </>
      )}

      {(state === 'ai' || state === 'claimed' || state === 'officer') && (
        <>
          <CallBriefCard
            brief={brief}
            loading={briefLoading}
            refreshing={refreshing}
            onRefresh={refresh}
            onEvidence={showEvidence}
            compact
          />
          {state === 'ai' && canTake && (
            <div className="cw-actions">
              <button type="button" className="cc-btn cc-btn--primary" data-action="take" onClick={take} disabled={busy}>
                {busy ? 'Connecting…' : 'Take over'} <kbd>A</kbd>
              </button>
              {takeError && <span className="cw-error" role="alert">{takeError}</span>}
            </div>
          )}
          <div className="cw-transcript">{transcript}</div>
        </>
      )}

      {state === 'mine' && activeCall && (
        <>
          <CallBriefCard
            brief={brief}
            loading={briefLoading}
            refreshing={refreshing}
            onRefresh={refresh}
            onEvidence={showEvidence}
            compact
          />
          <div className="cw-transcript">{transcript}</div>
          <CallControls
            ref={muteRef}
            call={activeCall}
            confirmEnd={confirmEnd}
            onConfirmEnd={setConfirmEnd}
            onOpenTransfer={() => setTransferOpen(true)}
          />
          <TransferDialog
            open={transferOpen}
            callId={callId}
            onClose={() => setTransferOpen(false)}
            onTransferred={() => void refetch()}
          />
        </>
      )}

      {state === 'wrap_up' && (
        <>
          <WrapUpPanel
            callId={callId}
            initialNote={call.wrapup_note || ''}
            ticketId={call.ticket_id}
            onFinished={() => {
              void refetch();
            }}
          />
          <div className="cw-transcript">{transcript}</div>
        </>
      )}

      {state === 'ended' && (
        <>
          <div className="cw-ended">
            {call.summary && <CallSummaryCard summary={call.summary} />}
            {call.metrics && <CallMetricsCard metrics={call.metrics} />}
            {canTake && (
              <CallReviewForm
                callId={callId}
                initialRating={call.officer_rating}
                initialNote={call.officer_note}
                onSubmit={async (rating, note) => {
                  await review.mutateAsync({ callId, rating, note });
                }}
                isSubmitting={review.isPending}
              />
            )}
          </div>
          <div className="cw-transcript">{transcript}</div>
        </>
      )}
    </article>
  );
}
