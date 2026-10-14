"use client";

/**
 * What the auditor decided about one Needs review item: Reviewed, or Action
 * needed, with a note — or reopen it.
 *
 * A decision is an audit note about the item; it changes nothing in the case
 * it is about, and every decision is recorded on the audit trail
 * (`staff.quality_reviewed`). Who decided, and when, is shown with it.
 */
import React, { useState } from "react";
import { useSetReview } from "../../hooks/useQualityAudit";
import type { NeedsReviewItem, ReviewStatus } from "../../services/analyticsApi";

export const REVIEW_LABEL: Record<ReviewStatus, string> = {
  reviewed: "Reviewed",
  action_needed: "Action needed",
};

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ReviewControl({ item }: { item: NeedsReviewItem }) {
  const review = item.review;
  const [note, setNote] = useState(review?.note ?? "");
  const [writing, setWriting] = useState(Boolean(review?.note));
  const save = useSetReview();
  const noteId = `qa-note-${item.item_type}-${item.item_id}`;

  const decide = (status: ReviewStatus | "open") =>
    save.mutate({ item, status, note: status === "open" ? "" : note.trim() });

  return (
    <div className="qa-review" role="group" aria-label={`Decision on: ${item.title}`}>
      {review ? (
        <p className={`qa-review-state is-${review.status}`}>
          <strong>{REVIEW_LABEL[review.status]}</strong>
          {review.reviewer_name ? ` by ${review.reviewer_name}` : ""} · {when(review.updated_at)}
          {review.note ? <span className="qa-review-note">“{review.note}”</span> : null}
        </p>
      ) : null}
      {writing ? (
        <label className="qa-review-field" htmlFor={noteId}>
          <span>Note</span>
          <textarea
            id={noteId}
            className="ops-textarea"
            rows={2}
            maxLength={1000}
            value={note}
            placeholder="What was found, and what should happen next"
            onChange={(event) => setNote(event.target.value)}
            disabled={save.isPending}
          />
        </label>
      ) : null}
      <div className="qa-review-actions">
        <button
          type="button"
          className="ops-btn is-sm"
          aria-pressed={review?.status === "reviewed"}
          disabled={save.isPending}
          onClick={() => decide("reviewed")}
        >
          Mark reviewed
        </button>
        <button
          type="button"
          className="ops-btn is-sm"
          aria-pressed={review?.status === "action_needed"}
          disabled={save.isPending}
          onClick={() => decide("action_needed")}
        >
          Action needed
        </button>
        {!writing ? (
          <button type="button" className="ops-btn is-ghost is-sm" onClick={() => setWriting(true)}>
            Add a note
          </button>
        ) : null}
        {review ? (
          <button type="button" className="ops-btn is-ghost is-sm" disabled={save.isPending} onClick={() => decide("open")}>
            Reopen
          </button>
        ) : null}
        {save.isPending ? <span className="qa-review-saving">Saving…</span> : null}
        {save.isError ? (
          <span className="st-save-err" role="alert">
            Could not save the decision — try again.
          </span>
        ) : null}
      </div>
    </div>
  );
}
