"use client";

/**
 * One conversation, answer by answer, with where it went wrong.
 *
 * Read-only: the administrator audits the answers; nothing here replies to the
 * taxpayer. The break point — the first unhelpful answer after a helpful one —
 * is marked "Things went wrong here", and the helpful answer before it is
 * marked too, because the useful comparison is between the two: what the
 * assistant got right, then what changed.
 */
import React from "react";
import { patternLabel, retrievalModeLabel } from "../charts/chartTheme";
import { EmptyState, ErrorState, SkeletonRows } from "../ops/States";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useAiChatTimeline } from "../../hooks/useQualityAudit";
import type { AiChatTimelineAnswer } from "../../services/analyticsApi";
import { RatingStrip } from "./RatingStrip";

function when(ts: number | null): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function RatingBadge({ rating }: { rating: AiChatTimelineAnswer["rating"] }) {
  if (rating === "up") return <span className="ops-chip is-good">✓ Helpful</span>;
  if (rating === "down") return <span className="ops-chip is-danger">✕ Not helpful</span>;
  return <span className="ops-chip">Not rated</span>;
}

export function ConversationTimeline({ conversationId }: { conversationId: string }) {
  const timeline = useAiChatTimeline(conversationId);
  const loading = useSkeletonVisible(timeline.isLoading);

  if (loading) return <SkeletonRows rows={4} height={96} />;
  if (timeline.isError) {
    return <ErrorState title="This conversation did not load" body={(timeline.error as Error).message} onRetry={() => void timeline.refetch()} />;
  }
  const data = timeline.data;
  if (!data) return null;
  if (!data.answers.length) {
    return <EmptyState title="Nothing left to show" body="The conversation and its ratings have both expired." />;
  }

  const breakIndex = data.answers.findIndex((a) => a.is_break);
  const lastHelpfulBefore =
    breakIndex > 0 ? [...data.answers.slice(0, breakIndex)].reverse().find((a) => a.rating === "up") : undefined;
  const rated = data.answers.filter((a) => a.rating);

  return (
    <article className="qa-timeline" aria-label="Conversation timeline">
      <header className="qa-timeline-head">
        <p className="qa-timeline-pattern">
          <span className={`qa-pattern-name is-${data.pattern}`}>{patternLabel(data.pattern)}</span>
          {data.break_answer ? <span> · went wrong at answer {data.break_answer}</span> : null}
        </p>
        <RatingStrip
          ratings={rated.map((a) => ({ answer: a.answer, rating: a.rating }))}
          breakAnswer={data.break_answer}
        />
        {!data.transcript_complete ? (
          <p className="qa-timeline-note">
            The conversation log is kept {data.transcript_kept_days} days. Answers nobody rated are no longer on
            file; each rated answer is shown from the copy kept with its rating.
          </p>
        ) : null}
      </header>
      <ol className="qa-answers">
        {data.answers.map((a, i) => {
          const isLastHelpful = lastHelpfulBefore === a;
          return (
            <li
              key={`${a.turn_id || "a"}-${i}`}
              className={`qa-answer${a.is_break ? " is-break" : ""}${isLastHelpful ? " is-last-good" : ""}`}
            >
              {a.is_break ? <p className="qa-answer-flag is-break">Things went wrong here</p> : null}
              {isLastHelpful ? <p className="qa-answer-flag is-good">Last helpful answer before it</p> : null}
              <div className="qa-answer-head">
                <h4 className="qa-answer-title">{a.answer ? `Answer ${a.answer}` : "Answer"}</h4>
                <RatingBadge rating={a.rating} />
                <span className="qa-answer-time">{when(a.created_at)}</span>
              </div>
              <p className="qa-answer-q">
                <span className="qa-answer-who">Taxpayer asked</span>
                {a.question || "—"}
              </p>
              <div className="qa-answer-a">
                <span className="qa-answer-who">Assistant answered</span>
                <p>{a.reply || "—"}</p>
              </div>
              {a.comment ? (
                <p className="qa-answer-comment">
                  <span className="qa-answer-who">Taxpayer&apos;s comment</span>“{a.comment}”
                </p>
              ) : null}
              <dl className="qa-answer-meta">
                {a.retrieval_mode ? (
                  <div>
                    <dt>How it was answered</dt>
                    <dd>{retrievalModeLabel(a.retrieval_mode)}</dd>
                  </div>
                ) : null}
                {a.sources.length ? (
                  <div>
                    <dt>Built from</dt>
                    <dd>{a.sources.join(" · ")}</dd>
                  </div>
                ) : null}
                {a.topic ? (
                  <div>
                    <dt>Topic</dt>
                    <dd>{a.topic.replaceAll("_", " ")}</dd>
                  </div>
                ) : null}
              </dl>
            </li>
          );
        })}
      </ol>
    </article>
  );
}
