"use client";

/**
 * Operations overview — what needs attention right now.
 *
 * `/admin/tickets` is the workbench and `/analytics` is observability.
 * This page is the morning board: SLA, authority, and the cases waiting
 * longest — each one a deep link into the queue.
 *
 * Three things it gained in the console redesign:
 *
 * - A period. It used to hardcode 30 days in the hooks and then *tell* the
 *   reader "last 30 days" with no control to change it.
 * - A comparison. Six bare numbers with nothing to measure them against are
 *   unreadable; "12 raised" is a quiet month or a crisis depending on the last
 *   one. The delta is exact rather than estimated — see lib/trends.ts for why
 *   only the volume carries one and the medians do not.
 * - Honest loading. Every panel now reserves the space its content will take,
 *   so nothing jumps when the four queries land at four different times.
 */
import React, { useEffect, useMemo, useState } from "react";
import StaffGuard from "../../components/StaffGuard";
import { OpsPage, OpsPanel } from "../../components/ops/OpsPage";
import { StatCard } from "../../components/ops/StatCard";
import { Freshness, PeriodPicker } from "../../components/ops/Controls";
import { EmptyState, ErrorState, Skeleton, SkeletonRows, SkeletonStats } from "../../components/ops/States";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { Sparkline } from "../../components/ops/Sparkline";
import { ArrowUpRightIcon } from "../../components/ops/icons";
import { useTicketQueueFull, useTicketSla, useTicketStats } from "../../hooks/useAnalyticsDashboard";
import { useClientClock } from "../../hooks/useClientClock";
import { authHeaders } from "../../lib/authSession";
import { formatDuration, waitingFor, waitTone } from "../../lib/ticketUi";
import { dailyCounts, formatDelta, previousWindow, toDelta } from "../../lib/trends";
import "./admin.css";

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

const PRIORITY_ORDER = ["urgent", "high", "normal", "low"] as const;

/** Rows the board loads for its "waiting longest" list. Not a total — every
 *  count on this page comes from an endpoint that knows the whole queue. */
const QUEUE_PAGE = 20;

function Overview() {
  const [days, setDays] = useState(30);

  const stats = useTicketStats(days);
  // The same query over twice the window. `ticket_stats` counts by created_at,
  // so 2N minus N is exactly the previous N — the one comparison these
  // endpoints can support without inventing anything.
  const statsPrev = useTicketStats(days * 2);
  const sla = useTicketSla(days);
  const queue = useTicketQueueFull("open", "", "", 20);
  // Every status, for the arrivals shape. Ordered urgent-first server-side, so
  // a full page is a biased sample and the chart says so instead of drawing it.
  const all = useTicketQueueFull("", "", "", 200);
  const authority = useAuthorityStatus();
  const now = useClientClock();

  const allOpen = queue.data?.tickets ?? [];
  // Ten is a board, not a backlog — the panel header links to the full queue.
  const items = allOpen.slice(0, 10);
  const awaiting = sla.data?.awaiting_first_response ?? 0;
  const breaching = sla.data?.breaching ?? 0;
  // Server-side, over the live open+assigned population. Derived from
  // `allOpen` this was capped at the 20 rows this page loads, so a queue of
  // 2,500 unclaimed cases reported 20 — beside an "Open escalations" tile
  // that reported the real total.
  const unassigned = sla.data?.unassigned ?? 0;
  // The queue page is ordered urgent-first, so every urgent open case is on it
  // until there are more than a page of them. Past that the count has quietly
  // stopped counting, and the chip says "20+" rather than a number.
  const urgent = allOpen.filter((t) => t.priority === "urgent").length;
  const queueTruncated = allOpen.length >= QUEUE_PAGE;

  const raisedDelta = toDelta(
    stats.data?.total,
    previousWindow(stats.data?.total, statsPrev.data?.total),
  );

  const arrivals = useMemo(() => {
    const rows = all.data?.tickets ?? [];
    if (!now || !rows.length) return null;
    return dailyCounts(
      rows.map((t) => t.created_at),
      days,
      now,
      rows.length >= 200,
    );
  }, [all.data, days, now]);

  const priorityMix = useMemo(() => {
    const by = stats.data?.by_priority ?? {};
    const total = Object.values(by).reduce((sum, n) => sum + n, 0);
    if (!total) return [];
    return PRIORITY_ORDER.filter((p) => by[p]).map((p) => ({
      priority: p,
      count: by[p],
      share: by[p] / total,
    }));
  }, [stats.data]);

  const refresh = () => {
    void stats.refetch();
    void statsPrev.refetch();
    void sla.refetch();
    void queue.refetch();
    void all.refetch();
  };

  const anyFetching = stats.isFetching || sla.isFetching || queue.isFetching;
  const periodLabel = `previous ${days} days`;

  // The summary tiles are fed by two independent queries, and this used to read
  // `stats.isLoading && sla.isLoading` — so whichever resolved first tore the
  // skeleton down and the tiles fed by the other one rendered 0. It has to be
  // OR: the summary is loading until both halves have arrived.
  //
  // Each flag then goes through the ladder (see useLoadingPhase), so a request
  // that returns inside 300ms never flashes a skeleton at all.
  const summaryLoading = useSkeletonVisible(stats.isLoading || sla.isLoading);
  const queueLoading = useSkeletonVisible(queue.isLoading);
  const arrivalsLoading = useSkeletonVisible(all.isLoading);
  const authorityLoading = useSkeletonVisible(authority.loading);
  const workloadLoading = useSkeletonVisible(stats.isLoading || sla.isLoading);

  return (
    <OpsPage
      eyebrow="Work"
      title="Operations overview"
      description={`Escalations raised in the last ${days} days, plus the service levels and answer authority of the queue as it stands right now. Every tile links into the queue it counts.`}
      actions={
        <>
          <Freshness
            updatedAt={sla.dataUpdatedAt || stats.dataUpdatedAt}
            isFetching={anyFetching}
            onRefresh={refresh}
          />
          <PeriodPicker days={days} onChange={setDays} />
          <a className="ops-btn is-primary" href="/admin/tickets">
            Open the full queue
            <ArrowUpRightIcon />
          </a>
        </>
      }
    >
      {stats.isError && sla.isError ? (
        <ErrorState
          title="The escalation service did not answer"
          body="Service levels, counts and the queue all come from /v1/admin/tickets. Nothing below is current."
          onRetry={refresh}
        />
      ) : null}

      <section aria-label="Escalation summary">
        {summaryLoading ? (
          <SkeletonStats count={6} />
        ) : (
          <div className="ops-stat-grid">
            <StatCard
              label="Open escalations"
              value={String(stats.data?.open ?? 0)}
              hint={`${stats.data?.total ?? 0} raised in the period`}
              href="/admin/tickets?status=open"
              loading={stats.isLoading}
            />
            {/* The next three are the live queue, not the period. `sla_stats`
                bounds its medians by the window and deliberately does not bound
                the breach and awaiting counts — an SLA number that expired with
                the date range would be useless. The hints say so, and each one
                links to the population it counts rather than to a narrower
                status view that shows fewer rows than the tile promised. */}
            <StatCard
              label="Awaiting first response"
              value={String(awaiting)}
              hint="right now, nobody has replied yet"
              href="/admin/tickets?status=any"
            />
            <StatCard
              label="Past 24-hour SLA"
              value={String(breaching)}
              hint="right now, open or in progress, over 24h"
              href="/admin/tickets?status=any"
            />
            <StatCard
              label="Unassigned"
              value={String(unassigned)}
              hint="right now, waiting to be claimed"
              href="/admin/tickets?status=any"
            />
            <StatCard
              label="Median first response"
              value={formatDuration(sla.data?.median_response_seconds)}
              hint={`${sla.data?.responded ?? 0} of ${sla.data?.tickets ?? 0} answered · target 24h`}
            />
            <StatCard
              label="Median time to resolve"
              value={formatDuration(sla.data?.median_resolution_seconds)}
              hint={`${stats.data?.resolved ?? 0} resolved`}
            />
          </div>
        )}
      </section>

      {/* Volume gets its own strip rather than a sparkline bolted onto a tile
          that counts something else: the trend, the delta and the mix all
          describe the same number — escalations raised in the period. */}
      <section className="ov-volume" aria-label="Escalation volume">
        <div className="ov-volume-lead">
          <span className="ops-stat-label">Escalations raised</span>
          <span className="ops-stat-row">
            {stats.isLoading ? (
              <Skeleton width={64} height={26} />
            ) : (
              <strong className="ops-stat-value">{stats.data?.total ?? 0}</strong>
            )}
            {raisedDelta ? (
              <span
                className={`ops-delta${
                  raisedDelta.direction === "flat" ? "" : raisedDelta.direction === "up" ? " is-bad" : " is-good"
                }`}
              >
                <span className="ops-delta-glyph" aria-hidden="true">
                  {raisedDelta.direction === "up" ? "▲" : raisedDelta.direction === "down" ? "▼" : "→"}
                </span>
                {formatDelta(raisedDelta)}
                <span className="ops-sr-only"> versus the {periodLabel}</span>
              </span>
            ) : null}
          </span>
          <span className="ops-stat-hint">
            {raisedDelta
              ? `${raisedDelta.previous} in the ${periodLabel}`
              : `over the last ${days} days`}
          </span>
        </div>
        <div className="ov-volume-chart">
          {arrivalsLoading ? (
            /* Sized to the sparkline it replaces, so the strip does not jump
               when the shape arrives. */
            <Skeleton height={54} radius="var(--ops-radius-sm)" />
          ) : arrivals && !arrivals.truncated ? (
            <Sparkline
              points={arrivals.points}
              label={`Escalations raised per day over the last ${days} days, from ${Math.min(
                ...arrivals.points,
              )} to ${Math.max(...arrivals.points)} a day`}
              height={54}
            />
          ) : (
            <p className="ops-stat-hint">
              {arrivals?.truncated
                ? "More than 200 tickets are on file, and the queue endpoint returns the most urgent first — a daily shape drawn from that page would be the wrong shape, so it is not drawn."
                : "No escalations in this period."}
            </p>
          )}
        </div>
        <div className="ov-volume-mix" role="group" aria-labelledby="ov-mix-label">
          {priorityMix.length > 0 ? (
            <>
              <span className="ops-stat-label" id="ov-mix-label">
                Priority mix
              </span>
              <ul className="ov-mix">
                {priorityMix.map((row) => (
                  <li key={row.priority}>
                    <span className={`ov-pri ov-pri-${row.priority}`}>{row.priority}</span>
                    <span className="ov-mix-bar" aria-hidden="true">
                      <span
                        className={`ov-mix-fill is-${row.priority}`}
                        style={{ width: `${Math.max(2, row.share * 100)}%` }}
                      />
                    </span>
                    <span className="ov-mix-count">{row.count}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </section>

      <div className="ov-cols">
        <OpsPanel
          id="q-h"
          title="Waiting longest"
          className="ov-queue-panel"
          flush
          bare
          end={
            <>
              {urgent > 0 ? (
                <span className="ops-chip is-danger">
                  {queueTruncated ? `${urgent}+` : urgent} urgent
                </span>
              ) : null}
              {/* Was `All ${allOpen.length} open`, which named the size of the
                  page this panel had loaded — "All 20 open" on a queue of two
                  and a half thousand. The real total is one tile away; this is
                  a link, so it does not need to restate it. */}
              <a className="ops-btn is-ghost is-sm" href="/admin/tickets?status=open">
                All open
              </a>
            </>
          }
        >
          {queueLoading ? (
            <SkeletonRows rows={5} height={52} />
          ) : queue.isError ? (
            <ErrorState body="The queue did not load." onRetry={() => void queue.refetch()} />
          ) : items.length > 0 ? (
            <ul className="ov-queue">
              {items.map((t) => {
                const tone = waitTone(t.created_at, t.first_response_at, t.reply_at);
                return (
                  <li key={t.id}>
                    <a className="ov-q-link" href={`/admin/tickets?ticket=${encodeURIComponent(t.id)}`}>
                      <span className={`ov-pri ov-pri-${t.priority}`}>{t.priority}</span>
                      <span className="ov-q-body">
                        <span className="ov-q-topic">{t.handoff?.topic || t.reason || "Escalation"}</span>
                        <span className="ov-q-query">{t.user_query}</span>
                        <span className="ov-q-meta">
                          {t.team ? <span>{t.team.replace(/_/g, " ")}</span> : null}
                          <span>{t.assignee ? `Assigned ${t.assignee}` : "Unassigned"}</span>
                        </span>
                      </span>
                      <span
                        className={`ov-q-wait${tone === "ok" ? "" : ` is-${tone}`}`}
                        title="Waiting since escalation"
                      >
                        {waitingFor(t.created_at)}
                      </span>
                    </a>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState
              title="The queue is clear"
              body="No open escalation is waiting for a first reply. New arrivals appear here and announce themselves at the top of the console."
            />
          )}
        </OpsPanel>

        <div className="ov-side">
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
                    {authority.data?.sources_checked != null
                      ? `${authority.data.sources_checked} checked`
                      : "—"}
                    {authority.data?.invalid_sources?.length
                      ? ` · ${authority.data.invalid_sources.length} failed`
                      : ""}
                  </dd>
                </div>
              </dl>
            )}
          </OpsPanel>

          <OpsPanel id="w-h" title="Workload" bare>
            {workloadLoading ? (
              <SkeletonRows rows={4} height={20} />
            ) : (
            <dl className="ov-kv">
              <div>
                <dt>Assigned, in progress</dt>
                <dd>{stats.data?.assigned ?? "—"}</dd>
              </div>
              <div>
                <dt>Resolved in period</dt>
                <dd>{stats.data?.resolved ?? "—"}</dd>
              </div>
              <div>
                <dt>Awaiting next reply</dt>
                <dd>{sla.data?.awaiting_next_response ?? "—"}</dd>
              </div>
              <div>
                <dt>Median next reply</dt>
                <dd>{formatDuration(sla.data?.median_next_reply_seconds)}</dd>
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
