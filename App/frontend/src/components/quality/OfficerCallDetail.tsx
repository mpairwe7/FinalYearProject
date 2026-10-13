"use client";

/**
 * One officer call as the audit reads it: how satisfied the caller said they
 * were, the officer's wrap-up, the call summary and the transcript.
 *
 * Read-only — the administrator audits the service and does not join calls.
 * Opening a call is a read of what the caller said and is recorded as such
 * (`voice_staff_viewed_call`), so it is read once, not re-polled.
 */
import React from "react";
import { EmptyState, ErrorState, SkeletonRows } from "../ops/States";
import { SATISFACTION_FACES, SATISFACTION_LABELS_EN } from "../rating/SatisfactionScale";
import { CallSummaryCard } from "../staff/calls/CallSummaryCard";
import { CallTranscript } from "../staff/calls/CallTranscript";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useAuditedCall } from "../../hooks/useQualityAudit";
import { callLanguageName } from "../../lib/callLanguage";
import type { ServiceRating } from "../../services/analyticsApi";
import "../../app/calls/calls.css";
import "../staff/staffTickets.css";

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "Resolved",
  follow_up: "Follow-up needed",
  callback: "Callback",
  referred: "Referred to a team",
  abandoned: "Caller left",
  ai_resolved: "Resolved by the AI",
};

function satisfactionText(rating: ServiceRating): string {
  if (rating.status === "answered" && rating.score != null) {
    return `${rating.score}/5 · ${SATISFACTION_LABELS_EN[rating.score - 1]}`;
  }
  return rating.status === "no_answer" ? "the caller didn't say" : "not recorded";
}

export function OfficerCallDetail({ callId, rating }: { callId: string; rating: ServiceRating | null }) {
  const call = useAuditedCall(callId);
  const loading = useSkeletonVisible(call.isLoading);

  if (loading) return <SkeletonRows rows={4} height={72} />;
  if (call.isError) {
    const gone = /^404\b/.test((call.error as Error).message);
    return gone ? (
      <EmptyState
        title="This call's record has expired"
        body="Call transcripts are kept for a limited time; the satisfaction rating is kept for the audit period."
      />
    ) : (
      <ErrorState title="This call did not load" body={(call.error as Error).message} onRetry={() => void call.refetch()} />
    );
  }
  const data = call.data;
  if (!data) return null;
  const officer = rating?.officer_name || "";

  return (
    <article className="qa-chat-detail" aria-label="Officer call">
      <header className="qa-timeline-head">
        <p className="qa-timeline-pattern">
          <strong>Call {data.call_id.slice(-8)}</strong>
          {officer ? <span> · {officer}</span> : null}
          {data.locale ? <span> · {callLanguageName(data.locale)}</span> : null}
          {data.outcome ? <span> · {OUTCOME_LABEL[data.outcome] ?? data.outcome}</span> : null}
        </p>
        {rating ? (
          <p className={`st-chat-rating${rating.score != null && rating.score <= 2 ? " is-low" : ""}`}>
            {rating.score != null ? (
              <span aria-hidden="true" className="st-chat-rating-face">
                {SATISFACTION_FACES[rating.score - 1]}
              </span>
            ) : null}
            Caller satisfaction: {satisfactionText(rating)}
          </p>
        ) : null}
        {data.wrapup_note ? (
          <p className="qa-timeline-note">
            <strong>Officer&apos;s wrap-up:</strong> {data.wrapup_note}
          </p>
        ) : null}
      </header>
      {data.summary ? <CallSummaryCard summary={data.summary} /> : null}
      <div className="qa-call-transcript">
        <CallTranscript turns={data.turns ?? []} startedAt={data.started_at} />
      </div>
    </article>
  );
}
