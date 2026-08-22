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
  sources?: unknown[];
  detail?: string;
}

function useAuthorityStatus() {
  const [state, setState] = useState<{ loading: boolean; data?: AuthorityStatus; error?: string }>({
    loading: true,
  });
  useEffect(() => {
    let cancelled = false;
    fetch("/api/v1/authority/status", { headers: authHeaders() })
      .then((r) => r.json())
      .then((d) => !cancelled && setState({ loading: false, data: d }))
      .catch((e) => !cancelled && setState({ loading: false, error: (e as Error).message }));
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}

const PRIORITY_ORDER = ["urgent", "high", "normal", "low"] as const;

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
  const urgent = allOpen.filter((t) => t.priority === "urgent").length;
  const awaiting = sla.data?.awaiting_first_response ?? 0;
  const breaching = sla.data?.breaching ?? 0;
  const unassigned = allOpen.filter((t) => !t.assignee).length;

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

  return (
    <OpsPage
      eyebrow="Work"
      title="Operations overview"
      description={`Escalations, service levels and answer authority across the last ${days} days. Every tile links into the queue it counts.`}
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
        {stats.isLoading && sla.isLoading ? (
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
            <StatCard
              label="Awaiting first response"
              value={String(awaiting)}
              hint="nobody has replied yet"
              tone={awaiting > 0 ? "warn" : "good"}
              href="/admin/tickets?status=open"
            />
            <StatCard
              label="Past 24-hour SLA"
              value={String(breaching)}
              hint="open or in progress, first- or next-reply over 24h"
              tone={breaching > 0 ? "danger" : "good"}
              href="/admin/tickets?status=open"
            />
            <StatCard
              label="Unassigned"
              value={String(unassigned)}
              hint="waiting to be claimed"
              tone={unassigned > 0 ? "warn" : "good"}
              href="/admin/tickets?status=open"
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
          {arrivals && !arrivals.truncated ? (
            <Sparkline
              points={arrivals.points}
              label={`Escalations raised per day over the last ${days} days, from ${Math.min(
                ...arrivals.points,
              )} to ${Math.max(...arrivals.points)} a day`}
              height={54}
            />
          ) : (
            <p className="ops-stat-hint">
              {all.isLoading
                ? "Loading the arrival history…"
                : arrivals?.truncated
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
          end={
            <>
              {urgent > 0 ? (
                <span className="ops-chip is-danger">{urgent} urgent</span>
              ) : null}
              <a className="ops-btn is-ghost is-sm" href="/admin/tickets?status=open">
                {allOpen.length > items.length ? `All ${allOpen.length} open` : "All open"}
              </a>
            </>
          }
        >
          {queue.isLoading ? (
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
            note="Rate answers are refused outright when this manifest is stale, so it is the first thing to check when the assistant starts declining figures."
            end={
              authority.loading ? null : (
                <span className={`ops-chip ${authority.data?.fresh ? "is-good" : "is-warn"}`}>
                  {authority.data?.fresh ? "Fresh" : "Stale or missing"}
                </span>
              )
            }
          >
            {authority.loading ? (
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
                  <dd>{authority.data?.sources?.length ?? "—"}</dd>
                </div>
              </dl>
            )}
          </OpsPanel>

          <OpsPanel id="w-h" title="Workload">
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
