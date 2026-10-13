"use client";

/**
 * Needs review — the items across the four streams that call for a person
 * to look, and what was decided about each.
 *
 * An AI chat that went wrong or was never found helpful, or a 👎 with a
 * comment; an officer chat or call rated 1 or 2; a caller who said the AI did
 * not help. Newest first. Each opens where it is read in full (its tab and
 * drill-down), and each takes a decision: Reviewed, or Action needed, with a
 * note. The list exports as CSV for the record.
 */
import React, { useEffect, useState } from "react";
import { EmptyState, ErrorState, SkeletonRows } from "../ops/States";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useNeedsReview } from "../../hooks/useQualityAudit";
import { callLanguageName } from "../../lib/callLanguage";
import { csvFilename, csvTime, downloadCsv, toCsv } from "../../lib/csv";
import type { NeedsReviewItem, NeedsReviewQuery, NeedsReviewStream } from "../../services/analyticsApi";
import { REVIEW_LABEL, ReviewControl } from "./ReviewControl";

export const STREAM_LABEL: Record<NeedsReviewStream, string> = {
  ai_chat: "AI answer · chat",
  ai_call: "AI on a call",
  officer_chat: "Officer chat",
  officer_call: "Officer call",
};

const STATUS_FILTERS: Array<{ value: NonNullable<NeedsReviewQuery["status"]>; label: string }> = [
  { value: "open", label: "Open" },
  { value: "action_needed", label: "Action needed" },
  { value: "reviewed", label: "Reviewed" },
  { value: "all", label: "All" },
];

const PAGE = 25;

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Where the item is read in full on /admin/quality. */
export function itemHref(item: Pick<NeedsReviewItem, "link">): string {
  const params = new URLSearchParams();
  if (item.link.tab && item.link.tab !== "ai-chat") params.set("tab", item.link.tab);
  if (item.link.param && item.link.value) params.set(item.link.param, item.link.value);
  const qs = params.toString();
  return qs ? `/admin/quality?${qs}` : "/admin/quality";
}

export function needsReviewCsv(items: NeedsReviewItem[]): string {
  return toCsv(
    ["stream", "title", "question", "comment", "topic", "language", "officer", "occurred_utc", "decision", "note", "decided_by", "decided_utc", "item_type", "item_id"],
    items.map((i) => [
      STREAM_LABEL[i.stream] ?? i.stream,
      i.title,
      i.quote,
      i.comment,
      i.topic,
      i.locale,
      i.officer_name,
      csvTime(i.occurred_at),
      i.review ? REVIEW_LABEL[i.review.status] : "Open",
      i.review?.note ?? "",
      i.review?.reviewer_name ?? "",
      csvTime(i.review?.updated_at),
      i.item_type,
      i.item_id,
    ]),
  );
}

function Item({ item }: { item: NeedsReviewItem }) {
  return (
    <li className="qa-review-item">
      <div className="qa-conv-top">
        <span className={`qa-stream-chip is-${item.stream}`}>{STREAM_LABEL[item.stream] ?? item.stream}</span>
        <strong className="qa-review-title">{item.title}</strong>
      </div>
      {item.quote ? (
        <p className="qa-conv-break">
          Asked: <q>{item.quote}</q>
        </p>
      ) : null}
      {item.comment ? (
        <p className="qa-conv-break">
          Said: <q>{item.comment}</q>
        </p>
      ) : null}
      <div className="qa-conv-meta">
        {item.topic ? <span>{item.topic.replaceAll("_", " ")}</span> : null}
        {item.locale ? <span>{callLanguageName(item.locale)}</span> : null}
        <span>{when(item.occurred_at)}</span>
        <a className="qa-review-open" href={itemHref(item)}>
          Open<span className="ops-sr-only"> {item.title}</span>
        </a>
      </div>
      <ReviewControl key={`${item.review?.status ?? "open"}-${item.review?.updated_at ?? 0}`} item={item} />
    </li>
  );
}

export function NeedsReviewPanel({ days, onUpdated }: { days: number; onUpdated?: (at: number) => void }) {
  const [status, setStatus] = useState<NonNullable<NeedsReviewQuery["status"]>>("open");
  const [stream, setStream] = useState<"" | NeedsReviewStream>("");
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => setLimit(PAGE), [status, stream, days]);
  const queue = useNeedsReview({ days, status, stream, limit });
  const loading = useSkeletonVisible(queue.isLoading);
  const data = queue.data;

  useEffect(() => {
    if (queue.dataUpdatedAt) onUpdated?.(queue.dataUpdatedAt);
  }, [queue.dataUpdatedAt, onUpdated]);

  if (queue.isError && !data) {
    return (
      <ErrorState
        title="The Needs review queue did not load"
        body={`GET /v1/admin/quality/needs-review answered ${(queue.error as Error).message}. Nothing below is current.`}
        onRetry={() => void queue.refetch()}
      />
    );
  }

  return (
    <div className="qa-section">
      {data?.truncated ? (
        <p className="qa-warning" role="status">
          More answers were rated in this period than one view reads, so the AI chats here are the most recent ones
          only. Choose a shorter period for the full picture.
        </p>
      ) : null}
      <div className="qa-filters qa-review-filters">
        <div className="ops-segmented" role="group" aria-label="Show items">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={status === f.value}
              className={status === f.value ? "is-active" : undefined}
              onClick={() => setStatus(f.value)}
            >
              {f.label}
              {data ? <span className="qa-count-badge"> {data.counts[f.value]}</span> : null}
            </button>
          ))}
        </div>
        <div className="ops-segmented" role="group" aria-label="From">
          <button type="button" aria-pressed={stream === ""} className={stream === "" ? "is-active" : undefined} onClick={() => setStream("")}>
            Every stream
          </button>
          {(Object.keys(STREAM_LABEL) as NeedsReviewStream[]).map((s) => (
            <button
              key={s}
              type="button"
              aria-pressed={stream === s}
              className={stream === s ? "is-active" : undefined}
              onClick={() => setStream(s)}
            >
              {STREAM_LABEL[s]}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="ops-btn is-sm is-ghost"
          disabled={!data?.items.length}
          onClick={() => data && downloadCsv(needsReviewCsv(data.items), csvFilename("quality-needs-review"))}
        >
          Export {data?.items.length ?? 0} as CSV
        </button>
      </div>

      {loading && !data ? (
        <SkeletonRows rows={5} height={88} />
      ) : data && data.items.length ? (
        <>
          <p className="qa-count" aria-live="polite">
            {data.total} {data.total === 1 ? "item" : "items"}
          </p>
          <ul className="qa-convs" aria-label="Needs review">
            {data.items.map((item) => (
              <Item key={`${item.item_type}:${item.item_id}`} item={item} />
            ))}
          </ul>
          {data.total > data.items.length ? (
            <button
              type="button"
              className="ops-btn is-ghost"
              onClick={() => setLimit((n) => Math.min(n + PAGE, 100))}
              disabled={limit >= 100 || queue.isFetching}
            >
              {limit >= 100 ? "Showing the first 100 — narrow the filter" : "Show more"}
            </button>
          ) : null}
        </>
      ) : (
        <EmptyState
          title={status === "open" ? "Nothing waiting for a review" : "No item matches"}
          body={
            status === "open"
              ? "Items arrive here as answers go wrong, officers are rated low, or callers say the AI did not help."
              : "Choose another filter to see the other items."
          }
        />
      )}
    </div>
  );
}
