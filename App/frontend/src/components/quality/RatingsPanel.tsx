"use client";

/**
 * Satisfaction with the service officers gave: how taxpayers rated their
 * chats, and what callers told the officer at the end of a call, on the one
 * 5-level scale the service asks with.
 *
 * Headline figures with the period before, the five levels, a per-officer
 * table that leads with the lowest average, then the ratings themselves,
 * lowest first — the ones to read. A rating opens its case read-only.
 * `?ticket=` (a chat) or `?call=` (a call) makes an opened case a link.
 */
import React, { useCallback, useEffect, useState } from "react";
import { OpsPanel, TableScroll } from "../ops/OpsPage";
import { StatCard } from "../ops/StatCard";
import { EmptyState, ErrorState, SkeletonRows, SkeletonStats } from "../ops/States";
import { SATISFACTION_FACES, SATISFACTION_LABELS_EN } from "../rating/SatisfactionScale";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useRatingsAudit } from "../../hooks/useQualityAudit";
import type { RatingSource, RatingsAudit, ServiceRating } from "../../services/analyticsApi";
import { SatisfactionDistribution } from "./SatisfactionDistribution";
import { OfficerChatDetail } from "./OfficerChatDetail";
import { OfficerCallDetail } from "./OfficerCallDetail";

/** The words that differ between the chat and the call streams. */
const COPY = {
  chat: {
    param: "ticket",
    summary: "Officer chat satisfaction summary",
    unit: "chats",
    distribution: "How satisfied taxpayers were with their chat",
    handled: "Chats ended",
    answered: "Answered the rating",
    list: "Rated chats",
    note: "Lowest first, then newest. Open one to read the chat as the taxpayer and the officer wrote it.",
    empty: "No officer chat was rated in this period",
    emptyBody: "When an officer ends a chat, the taxpayer is asked to rate it; their answers appear here.",
    close: "Close chat",
    noAnswer: "No answer",
  },
  call: {
    param: "call",
    summary: "Officer call satisfaction summary",
    unit: "calls",
    distribution: "How satisfied callers said they were",
    handled: "Calls wrapped up",
    answered: "Caller gave an answer",
    list: "Rated calls",
    note: "Lowest first, then newest. Open one to read the call and the officer's wrap-up.",
    empty: "No officer call was rated in this period",
    emptyBody: "At wrap-up the officer records how satisfied the caller said they were; those answers appear here.",
    close: "Close call",
    noAnswer: "Caller didn't say",
  },
} as const;

type Kind = keyof typeof COPY;

const PAGE = 25;

function readParam(name: string): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(name);
}

function writeParam(name: string, value: string | null): void {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams(window.location.search);
  if (value) params.set(name, value);
  else params.delete(name);
  const qs = params.toString();
  try {
    window.history.replaceState(null, "", qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
  } catch {
    /* jsdom without a writable Location */
  }
}

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value}%`;
}

function when(ts: number | null | undefined): string {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** "up 0.4 from 3.8 in the period before", or what there was to compare. */
function averageHint(audit: RatingsAudit): string {
  const now = audit.summary.average;
  const before = audit.previous.average;
  if (now == null || before == null) return "nothing rated in the period before";
  const change = Math.round((now - before) * 100) / 100;
  const direction = change === 0 ? "unchanged" : change > 0 ? `up ${change}` : `down ${-change}`;
  return `${direction} from ${before} in the period before`;
}

export function ratingText(rating: Pick<ServiceRating, "status" | "score">, kind: Kind = "chat"): string {
  if (rating.status === "answered" && rating.score != null) {
    return `${rating.score}/5 · ${SATISFACTION_LABELS_EN[rating.score - 1]}`;
  }
  if (rating.status === "skipped") return "Skipped the rating";
  if (rating.status === "no_answer") return COPY[kind].noAnswer;
  return "Not answered yet";
}

function caseOf(rating: ServiceRating, kind: Kind): string {
  return kind === "call" ? rating.call_id : rating.ticket_id;
}

function RatingRow({
  rating,
  kind,
  selected,
  onOpen,
}: {
  rating: ServiceRating;
  kind: Kind;
  selected: boolean;
  onOpen: (id: string) => void;
}) {
  const low = rating.score != null && rating.score <= 2;
  const body = (
    <>
      <span className="qa-conv-top">
        <span className="qa-rating-face" aria-hidden="true">
          {rating.score != null ? SATISFACTION_FACES[rating.score - 1] : "–"}
        </span>
        <span className={`qa-rating-score${low ? " is-low" : ""}`}>{ratingText(rating, kind)}</span>
        {rating.officer_name ? <span className="qa-rating-officer">{rating.officer_name}</span> : null}
      </span>
      {rating.comment ? (
        <span className="qa-conv-break">
          <q>{rating.comment}</q>
        </span>
      ) : null}
      <span className="qa-conv-meta">
        {rating.team ? <span>{rating.team.replaceAll("_", " ")}</span> : null}
        {rating.topic ? <span>{rating.topic.replaceAll("_", " ")}</span> : null}
        <span>{when(rating.answered_at || rating.created_at)}</span>
      </span>
    </>
  );
  const id = caseOf(rating, kind);
  if (!id) return <div className="qa-conv is-static">{body}</div>;
  return (
    <button
      type="button"
      className={`qa-conv${selected ? " is-selected" : ""}`}
      aria-current={selected || undefined}
      onClick={() => onOpen(id)}
    >
      {body}
    </button>
  );
}

export function RatingsPanel({
  source = "chat_officer_csat",
  days,
  onUpdated,
}: {
  source?: RatingSource;
  days: number;
  onUpdated?: (at: number) => void;
}) {
  const kind: Kind = source === "call_officer_csat" ? "call" : "chat";
  const copy = COPY[kind];
  const [score, setScore] = useState<number | null>(null);
  const [officer, setOfficer] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [selected, setSelected] = useState<string | null>(() => readParam(copy.param));

  useEffect(() => writeParam(copy.param, selected), [copy.param, selected]);
  useEffect(() => setLimit(PAGE), [score, officer, days]);

  const audit = useRatingsAudit({ source, days, score, officer, limit });
  const loading = useSkeletonVisible(audit.isLoading);
  const data = audit.data;
  const summary = data?.summary;

  useEffect(() => {
    if (audit.dataUpdatedAt) onUpdated?.(audit.dataUpdatedAt);
  }, [audit.dataUpdatedAt, onUpdated]);

  const open = useCallback((id: string) => setSelected(id), []);
  const officerName = officer ? summary?.by_officer.find((o) => o.officer_id === officer)?.officer_name || officer : "";

  if (audit.isError && !data) {
    return (
      <ErrorState
        title="The ratings did not load"
        body={`GET /v1/admin/quality/ratings answered ${(audit.error as Error).message}. Nothing below is current.`}
        onRetry={() => void audit.refetch()}
      />
    );
  }

  return (
    <div className="qa-section">
      {data?.truncated ? (
        <p className="qa-warning" role="status">
          More ratings were asked for in this period than one view reads, so these figures cover the most recent
          ones only. Choose a shorter period for the full picture.
        </p>
      ) : null}

      <section aria-label={copy.summary}>
        {loading || !data || !summary ? (
          <SkeletonStats count={4} cols={4} />
        ) : (
          <div className="ops-stat-grid qa-stats">
            <StatCard
              label="Average satisfaction"
              value={summary.average == null ? "—" : `${summary.average}/5`}
              hint={`${summary.answered} rated · ${averageHint(data)}`}
            />
            <StatCard
              label="Satisfied (4 or 5)"
              value={pct(summary.csat_pct)}
              hint={
                data.previous.csat_pct == null
                  ? `share of rated ${copy.unit}`
                  : `share of rated ${copy.unit} · ${data.previous.csat_pct}% in the period before`
              }
            />
            <StatCard
              label={copy.answered}
              value={pct(summary.response_rate)}
              hint={
                kind === "call"
                  ? `${summary.answered} of ${summary.asked} recorded · ${summary.no_answer} didn't say`
                  : `${summary.answered} of ${summary.asked} asked · ${summary.skipped} skipped${summary.pending ? ` · ${summary.pending} not answered yet` : ""}`
              }
            />
            <StatCard
              label="Low ratings (1 or 2)"
              value={String(summary.low)}
              hint="dissatisfied or very dissatisfied — read these first"
            />
          </div>
        )}
      </section>

      {summary && summary.answered > 0 ? (
        <section className="ops-chart-grid is-2" aria-label="Satisfaction by level and by officer">
          <SatisfactionDistribution figures={summary} title={copy.distribution} />
          <div className="ops-chart-card">
            <div className="ops-chart-head">
              <h3 className="ops-chart-title">By officer</h3>
              <span className="ops-chart-sub">lowest average first · choose one to filter</span>
            </div>
            <TableScroll label="Satisfaction by officer">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th scope="col">Officer</th>
                    <th scope="col" className="is-num">{copy.handled}</th>
                    <th scope="col" className="is-num">Rated</th>
                    <th scope="col" className="is-num">Average</th>
                    <th scope="col" className="is-num">Satisfied</th>
                    <th scope="col" className="is-num">Low</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.by_officer.map((o) => (
                    <tr key={o.officer_id} className={officer === o.officer_id ? "is-active" : undefined}>
                      <td>
                        <button
                          type="button"
                          className="qa-link-btn"
                          aria-pressed={officer === o.officer_id}
                          onClick={() => setOfficer((current) => (current === o.officer_id ? "" : o.officer_id))}
                        >
                          {o.officer_name}
                        </button>
                      </td>
                      <td className="is-num">{o.handled}</td>
                      <td className="is-num">{o.rated}</td>
                      <td className="is-num">{o.average == null ? "—" : `${o.average}/5`}</td>
                      <td className="is-num">{pct(o.csat_pct)}</td>
                      <td className="is-num">{o.low}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          </div>
        </section>
      ) : null}

      <OpsPanel
        id="qa-ratings-h"
        title={copy.list}
        bare
        note={copy.note}
        end={
          <div className="qa-filters">
            <div className="ops-segmented" role="group" aria-label="Show ratings">
              <button type="button" aria-pressed={score === null} className={score === null ? "is-active" : undefined} onClick={() => setScore(null)}>
                All
              </button>
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={score === n}
                  aria-label={`${n} of 5, ${SATISFACTION_LABELS_EN[n - 1]}`}
                  className={score === n ? "is-active" : undefined}
                  onClick={() => setScore(n)}
                >
                  <span aria-hidden="true">{SATISFACTION_FACES[n - 1]}</span> {n}
                </button>
              ))}
            </div>
            {officer ? (
              <button type="button" className="ops-btn is-ghost is-sm" onClick={() => setOfficer("")}>
                {officerName} ✕
              </button>
            ) : null}
          </div>
        }
      >
        <div className={`qa-split${selected ? " is-open" : ""}`}>
          <div className="qa-list-pane">
            {loading && !data ? (
              <SkeletonRows rows={5} height={64} />
            ) : data && data.ratings.length ? (
              <>
                <p className="qa-count" aria-live="polite">
                  {data.total} {data.total === 1 ? "rating" : "ratings"}
                  {score ? ` · ${score}/5` : ""}
                  {officerName ? ` · ${officerName}` : ""}
                </p>
                <ul className="qa-convs">
                  {data.ratings.map((r) => (
                    <li key={r.id}>
                      <RatingRow rating={r} kind={kind} selected={caseOf(r, kind) === selected} onOpen={open} />
                    </li>
                  ))}
                </ul>
                {data.total > data.ratings.length ? (
                  <button
                    type="button"
                    className="ops-btn is-ghost"
                    onClick={() => setLimit((n) => Math.min(n + PAGE, 100))}
                    disabled={limit >= 100 || audit.isFetching}
                  >
                    {limit >= 100 ? "Showing the first 100 — narrow the filter" : "Show more"}
                  </button>
                ) : null}
              </>
            ) : (
              <EmptyState
                title={score || officer ? "No rating matches" : copy.empty}
                body={
                  score || officer
                    ? "Clear the filter to see every rating."
                    : copy.emptyBody
                }
              />
            )}
          </div>
          {selected ? (
            <div className="qa-detail-pane">
              <button type="button" className="ops-btn is-ghost is-sm qa-back" onClick={() => setSelected(null)}>
                {copy.close}
              </button>
              {kind === "call" ? (
                <OfficerCallDetail
                  callId={selected}
                  rating={data?.ratings.find((r) => r.call_id === selected) ?? null}
                />
              ) : (
                <OfficerChatDetail ticketId={selected} />
              )}
            </div>
          ) : null}
        </div>
      </OpsPanel>
    </div>
  );
}
