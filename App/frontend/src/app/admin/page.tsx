"use client";

/**
 * Service quality overview — where the administrator's audit starts.
 *
 * Officers handle taxpayers live (`/agent`, `/admin/tickets`, `/calls`); an
 * administrator audits the service they and the AI give, so nothing here
 * claims, replies or links into the live queue. What it shows, for a period:
 *
 * - How each of the four streams was rated — AI answers in chat, the AI on
 *   calls, officer chats and officer calls — each against the period before
 *   and each opening its tab of the quality audit.
 * - What is waiting for a person to look at: the newest open items of the
 *   Needs review queue, each opening where it is read in full.
 * - Who is on duty, whether the answer-authority manifest is fresh, and the
 *   service levels officers are keeping — figures, not queue links.
 *
 * Every panel reserves the space its content will take, so nothing jumps as
 * the queries land at different times.
 */
import React, { useEffect, useState } from "react";
import StaffGuard from "../../components/StaffGuard";
import { OpsPage, OpsPanel } from "../../components/ops/OpsPage";
import { StatCard } from "../../components/ops/StatCard";
import { Freshness, PeriodPicker } from "../../components/ops/Controls";
import { EmptyState, ErrorState, SkeletonRows, SkeletonStats } from "../../components/ops/States";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { ArrowUpRightIcon } from "../../components/ops/icons";
import { OfficersOnDuty } from "../../components/staff/OfficersOnDuty";
import { itemHref, STREAM_LABEL } from "../../components/quality/NeedsReviewPanel";
import { useTicketSla, useTicketStats } from "../../hooks/useAnalyticsDashboard";
import { useNeedsReview, useQualitySummary } from "../../hooks/useQualityAudit";
import { authHeaders } from "../../lib/authSession";
import { formatDuration } from "../../lib/ticketUi";
import type { NeedsReviewItem, QualitySummary, RatingsHeadline } from "../../services/analyticsApi";
import "./admin.css";
import "./quality/quality.css";

interface AuthorityStatus {
  fresh?: boolean;
  version?: string;
  generated_at?: string;
  age_days?: number;
  max_age_days?: number;
  /** How many manifest sources were found and hashed. The endpoint returns a
   *  count, not the list — this used to read `sources.length` against a key
   *  `get_authority_status()` has never emitted, so the row was always "—". */
  sources_checked?: number;
  invalid_sources?: unknown[];
  detail?: string;
}

function useAuthorityStatus() {
  const [state, setState] = useState<{ loading: boolean; data?: AuthorityStatus; error?: string }>({
    loading: true,
  });
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/authority/status", { headers: authHeaders() })
      .then((r) => {
        // A FastAPI error body is valid JSON, so parsing unconditionally
        // turned an expired token into `{detail: "..."}` — `fresh` undefined,
        // and the panel announcing "Stale or missing". That is the alarm this
        // page tells an operator to act on, raised by a sign-in problem.
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then((d) => !cancelled && setState({ loading: false, data: d }))
      .catch((e) => !cancelled && setState({ loading: false, error: (e as Error).message }));
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}

/** Newest open items the overview lists before "See all". */
const NEEDS_REVIEW_SHOWN = 6;

function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${value}%`;
}

function share(part: number, whole: number): number | null {
  return whole ? Math.round((part / whole) * 1000) / 10 : null;
}

/** "up 4 points from 72% in the period before" — the quality audit's own wording. */
export function pointsHint(now: number | null | undefined, before: number | null | undefined): string {
  if (now == null || before == null) return "nothing to compare in the period before";
  const change = Math.round((now - before) * 10) / 10;
  if (change === 0) return `unchanged from ${before}% in the period before`;
  return `${change > 0 ? "up" : "down"} ${Math.abs(change)} points from ${before}% in the period before`;
}

/**
 * The caller's spoken review, both moments together: the share of answered
 * reviews that said the AI helped, now and in the period before. Counts are
 * added before dividing, so a moment with few answers weighs as little as it
 * should.
 */
export function aiCallsHeadline(calls: QualitySummary["ai_calls"]) {
  const moments: RatingsHeadline[] = calls ? [calls.end, calls.transfer].filter(Boolean) : [];
  const answered = moments.reduce((n, m) => n + m.answered, 0);
  const positive = moments.reduce((n, m) => n + (m.sentiment?.positive ?? 0), 0);
  const prevAnswered = moments.reduce((n, m) => n + (m.previous?.answered ?? 0), 0);
  const prevPositive = moments.reduce(
    (n, m) => n + Math.round(((m.previous?.positive_pct ?? 0) * (m.previous?.answered ?? 0)) / 100),
    0,
  );
  return {
    asked: moments.reduce((n, m) => n + m.asked, 0),
    answered,
    positivePct: share(positive, answered),
    previousPositivePct: share(prevPositive, prevAnswered),
  };
}

function satisfaction(headline: RatingsHeadline | undefined): { value: string; hint: string } {
  if (!headline || !headline.answered) {
    return { value: "—", hint: headline?.asked ? `${headline.asked} asked, none rated yet` : "nothing rated in the period" };
  }
  const value = headline.average == null ? "—" : `${headline.average}/5`;
  return {
    value,
    hint: `${pct(headline.csat_pct)} satisfied (4 or 5) of ${headline.answered} rated · ${pointsHint(
      headline.csat_pct,
      headline.previous?.answered ? headline.previous.csat_pct : null,
    )}`,
  };
}

function when(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function ReviewItem({ item }: { item: NeedsReviewItem }) {
  const said = item.comment || item.quote;
  return (
    <li className="ov-review-item">
      <div className="ov-review-top">
        <span className={`qa-stream-chip is-${item.stream}`}>{STREAM_LABEL[item.stream] ?? item.stream}</span>
        <span className="ov-review-when">{when(item.occurred_at)}</span>
      </div>
      <a className="ov-review-title" href={itemHref(item)}>
        {item.title}
      </a>
      {said ? (
        <p className="ov-review-said">
          {item.comment ? "Said" : "Asked"}: <q>{said}</q>
        </p>
      ) : null}
    </li>
  );
}

function NeedsReview({ days }: { days: number }) {
  const queue = useNeedsReview({ days, status: "open", limit: NEEDS_REVIEW_SHOWN });
  const data = queue.data;
  const open = data?.counts.open ?? 0;
  const actionNeeded = data?.counts.action_needed ?? 0;

  return (
    <OpsPanel
      id="nr-h"
      title="Needs review"
      className="ov-review-panel"
      bare
      note="Answers that went wrong, officers rated 1 or 2, and callers who said the AI did not help — newest first. Open one to read it in full and record what was decided."
      end={
        data ? (
          <span className={`ops-chip ${open ? "is-warn" : "is-good"}`}>
            {open} open{actionNeeded ? ` · ${actionNeeded} action needed` : ""}
          </span>
        ) : null
      }
    >
      {queue.isError && !data ? (
        <ErrorState
          title="The Needs review queue did not load"
          body={`GET /v1/admin/quality/needs-review answered ${(queue.error as Error).message}.`}
          onRetry={() => void queue.refetch()}
        />
      ) : !data ? (
        // The list's space is held until it lands, so the side column does not jump.
        <SkeletonRows rows={4} height={64} />
      ) : data.items.length ? (
        <>
          <ul className="ov-review-list" aria-label="Needs review, newest first">
            {data.items.map((item) => (
              <ReviewItem key={`${item.item_type}:${item.item_id}`} item={item} />
            ))}
          </ul>
          <a className="ops-btn is-ghost ov-review-all" href="/admin/quality?tab=needs-review">
            {data.total > data.items.length ? `See all ${data.total} open items` : "Open the Needs review queue"}
            <ArrowUpRightIcon />
          </a>
        </>
      ) : (
        <EmptyState
          title="Nothing waiting for a review"
          body="Items arrive here as answers go wrong, officers are rated low, or callers say the AI did not help."
        />
      )}
    </OpsPanel>
  );
}

function Overview() {
  const [days, setDays] = useState(30);

  const summary = useQualitySummary(days);
  const stats = useTicketStats(days);
  const sla = useTicketSla(days);
  const authority = useAuthorityStatus();

  const refresh = () => {
    void summary.refetch();
    void stats.refetch();
    void sla.refetch();
  };

  const summaryLoading = useSkeletonVisible(summary.isLoading);
  const levelsLoading = useSkeletonVisible(stats.isLoading || sla.isLoading);
  const authorityLoading = useSkeletonVisible(authority.loading);

  const data = summary.data;
  const aiChat = data?.ai_chat;
  const aiCalls = aiCallsHeadline(data?.ai_calls);
  const officerChat = satisfaction(data?.officer_chat);
  const officerCalls = satisfaction(data?.officer_calls);

  return (
    <OpsPage
      eyebrow="Audit"
      title="Service quality overview"
      description={`How taxpayers and callers rated the AI and the officers over the last ${days} days, and what is waiting for a person to review. Read-only: officers handle taxpayers live, and this page audits the service they give.`}
      actions={
        <>
          <Freshness updatedAt={summary.dataUpdatedAt} isFetching={summary.isFetching || sla.isFetching} onRefresh={refresh} />
          <PeriodPicker days={days} onChange={setDays} />
          <a className="ops-btn is-primary" href="/admin/quality">
            Open quality audit
            <ArrowUpRightIcon />
          </a>
        </>
      }
    >
      {summary.isError && !data ? (
        <ErrorState
          title="The quality audit did not answer"
          body={`GET /v1/admin/quality/summary answered ${(summary.error as Error).message}. The ratings below are not current.`}
          onRetry={() => void summary.refetch()}
        />
      ) : null}

      <section aria-label="Service quality summary">
        {summaryLoading || !data ? (
          <SkeletonStats count={4} cols={4} />
        ) : (
          <div className="ops-stat-grid ov-stats">
            <StatCard
              label="AI answers found helpful"
              value={pct(aiChat?.helpful_pct)}
              hint={`${aiChat?.helpful ?? 0} of ${aiChat?.rated_answers ?? 0} rated in chat · ${pointsHint(
                aiChat?.helpful_pct,
                aiChat?.previous.rated_answers ? aiChat.previous.helpful_pct : null,
              )}`}
              href="/admin/quality"
            />
            <StatCard
              label="Callers said the AI helped"
              value={pct(aiCalls.positivePct)}
              hint={`${aiCalls.answered} of ${aiCalls.asked} callers answered the review · ${pointsHint(
                aiCalls.positivePct,
                aiCalls.previousPositivePct,
              )}`}
              href="/admin/quality?tab=ai-calls"
            />
            <StatCard
              label="Officer chat satisfaction"
              value={officerChat.value}
              hint={officerChat.hint}
              href="/admin/quality?tab=officer-chat"
            />
            <StatCard
              label="Officer call satisfaction"
              value={officerCalls.value}
              hint={officerCalls.hint}
              href="/admin/quality?tab=officer-calls"
            />
          </div>
        )}
      </section>

      <div className="ov-cols">
        <NeedsReview days={days} />

        <div className="ov-side">
          <OpsPanel
            id="d-h"
            title="Officers on duty"
            className="ov-duty-panel"
            bare
            note="Who is signed in to the console and whether they can take the next case or call. Officers set this themselves; it changes as they take and finish calls."
          >
            <OfficersOnDuty />
          </OpsPanel>

          <OpsPanel
            id="a-h"
            title="Answer authority"
            bare
            note="Rate answers are refused outright when this manifest is stale, so it is the first thing to check when the assistant starts declining figures."
            end={
              // Three states, not two. The body already renders an ErrorState
              // when the request failed; without the same branch here the
              // header kept announcing "Stale or missing" — the alarm this
              // panel exists to raise — for a request that never arrived.
              authority.loading ? null : authority.error ? (
                <span className="ops-chip">Unavailable</span>
              ) : (
                <span className={`ops-chip ${authority.data?.fresh ? "is-good" : "is-warn"}`}>
                  {authority.data?.fresh ? "Fresh" : "Stale or missing"}
                </span>
              )
            }
          >
            {authorityLoading ? (
              <SkeletonRows rows={4} height={20} />
            ) : authority.error ? (
              <ErrorState title="Authority status unavailable" body={authority.error} />
            ) : (
              <dl className="ov-kv">
                <div>
                  <dt>Status</dt>
                  <dd>
                    <span className={authority.data?.fresh ? "ov-chip good" : "ov-chip warn"}>
                      {authority.data?.fresh ? "Fresh" : "Stale or missing"}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Version</dt>
                  <dd>{authority.data?.version || "—"}</dd>
                </div>
                <div>
                  <dt>Age</dt>
                  <dd>
                    {authority.data?.age_days != null
                      ? `${authority.data.age_days} of ${authority.data.max_age_days ?? "?"} days`
                      : "—"}
                  </dd>
                </div>
                <div>
                  <dt>Sources</dt>
                  <dd>
                    {authority.data?.sources_checked != null ? `${authority.data.sources_checked} checked` : "—"}
                    {authority.data?.invalid_sources?.length ? ` · ${authority.data.invalid_sources.length} failed` : ""}
                  </dd>
                </div>
              </dl>
            )}
          </OpsPanel>

          {/* Figures, not links: the queue they describe is the officers' to work.
              The SLA breach count is the live queue, not the period — an SLA
              number that expired with the date range would be useless. */}
          <OpsPanel
            id="s-h"
            title="Service levels"
            bare
            note={`Escalations officers are keeping up with. The medians cover escalations raised in the last ${days} days; the 24-hour figure is the queue right now.`}
          >
            {levelsLoading ? (
              <SkeletonRows rows={4} height={20} />
            ) : stats.isError && sla.isError ? (
              <ErrorState
                title="Service levels unavailable"
                body="They come from /v1/admin/tickets, which did not answer."
                onRetry={() => {
                  void stats.refetch();
                  void sla.refetch();
                }}
              />
            ) : (
              <dl className="ov-kv">
                <div>
                  <dt>Open escalations</dt>
                  <dd>{stats.data?.open ?? "—"}</dd>
                </div>
                <div>
                  <dt>Past 24-hour SLA, right now</dt>
                  <dd>{sla.data?.breaching ?? "—"}</dd>
                </div>
                <div>
                  <dt>Median first response</dt>
                  <dd>{formatDuration(sla.data?.median_response_seconds)}</dd>
                </div>
                <div>
                  <dt>Median time to resolve</dt>
                  <dd>{formatDuration(sla.data?.median_resolution_seconds)}</dd>
                </div>
              </dl>
            )}
          </OpsPanel>
        </div>
      </div>
    </OpsPage>
  );
}

export default function AdminOverviewPage() {
  return (
    <StaffGuard current="/admin" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <Overview />}
    </StaffGuard>
  );
}
