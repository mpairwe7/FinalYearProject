"use client";

/**
 * Analytics dashboard — production observability for the URA Chatbot.
 *
 * Data source: GET /v1/analytics/dashboard, GET /v1/feedback/summary,
 * GET /v1/admin/tickets/stats — all proxied via /api/* rewrite.
 *
 * Standards: ISO/IEC 25010:2023 §4 (Interaction Capability),
 * EU AI Act Art. 13 (Transparency), ISO 42001 §9.1 (Monitoring)
 *
 * Two honesty problems this page had, both of which mattered more than how it
 * looked:
 *
 * 1. Three of its six SLO gauges were literals in the JSX — availability 99.9,
 *    accessibility 95, coverage 80 — drawn identically to the three fed by live
 *    telemetry. On a dashboard whose whole claim is monitoring evidence, a
 *    constant that renders as a measurement is worse than a missing panel. They
 *    are named below as what they are: targets verified in CI, not here.
 * 2. The period selector implied everything on the page was scoped to it. The
 *    request counters and latency histograms come from `metrics.snapshot()`,
 *    which is this replica's process lifetime and does not move when the period
 *    changes. Those panels now say so.
 *
 * And a third, which was not about honesty but about who the page is for. It
 * was captioned in the vocabulary of the system it monitors — "Chat p95
 * latency", "Average confidence", "retrieval_mode = hybrid", "Replica uptime",
 * an axis of URL paths, a unit of milliseconds. Every one of those is a fact a
 * service manager needs and none of them is a fact they can read. The panels
 * now lead with what the number means and keep the technical term as a
 * secondary line, and each chart carries one sentence stating its reading —
 * see components/charts/chartTheme's plain-language layer.
 */
import React, { useState } from "react";
import StaffGuard from "../../components/StaffGuard";
import { OpsPage, OpsPanel, TableScroll } from "../../components/ops/OpsPage";
import { Freshness, PeriodPicker } from "../../components/ops/Controls";
import { StatCard } from "../../components/ops/StatCard";
import { EmptyState, ErrorState, Skeleton, SkeletonStats } from "../../components/ops/States";
import {
  useDashboard,
  useFeedbackSummary,
  useTicketQueue,
  useTicketStats,
} from "../../hooks/useAnalyticsDashboard";
import SloGaugeCard from "../../components/charts/SloGaugeCard";
import { plainSeconds } from "../../components/charts/chartTheme";
import TopicBarChart from "../../components/charts/TopicBarChart";
import FeedbackPieChart from "../../components/charts/FeedbackPieChart";
import RetrievalModeChart from "../../components/charts/RetrievalModeChart";
import LatencyChart, { routeFromMetricKey } from "../../components/charts/LatencyChart";
import TicketStatusChart from "../../components/charts/TicketStatusChart";
import "./analytics.css";

function formatUptime(s: number): string {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return d > 0 ? `${d}d ${h}h ${m}m` : `${h}h ${m}m`;
}

function compact(n: number | undefined): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/**
 * A date, or an em-dash if the payload did not carry one.
 *
 * `Intl.DateTimeFormat().format(NaN)` THROWS — it does not return "Invalid
 * Date" the way `String(new Date(NaN))` does. So `ticket.updated_at * 1000` on
 * a record with no `updated_at` took down the whole /analytics route through
 * the error boundary, showing "Analytics unavailable — Invalid time value"
 * where a dashboard should be.
 *
 * The type says `updated_at: number`, which is why nothing caught this: the
 * field is required in TypeScript and optional in practice. Neither the axe
 * suite nor the deployment showed it, because both happen to have an empty
 * queue and the row never rendered.
 *
 * This is the same class of failure the console redesign recorded as gap 18 —
 * one field of one record missing, and the entire page is gone. A missing
 * timestamp should cost a timestamp.
 */
const DATE_FMT = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" });

function formatSeconds(seconds: number | undefined | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const ms = seconds * 1000;
  if (!Number.isFinite(ms)) return "—";
  return DATE_FMT.format(ms);
}


function Dashboard() {
  const [days, setDays] = useState(30);
  const dash = useDashboard(days);
  const feedback = useFeedbackSummary(days);
  const tickets = useTicketStats(days);
  const ticketQueue = useTicketQueue("open", 6);

  // A payload that is missing a section is treated as no payload rather than
  // rendering half a dashboard and throwing on the next property access. This
  // page used to crash outright on `dash.requests.latency` when the shape did
  // not match, which is how a stubbed backend took the whole route down.
  const raw = dash.data;
  const data = raw?.conversations && raw?.sessions && raw?.requests ? raw : undefined;
  const malformed = Boolean(raw && !data);
  // The same key-format bug the X axis had, in the page's headline gauge: this
  // read `latency["POST|/v1/chat"]`, but the backend emits Prometheus series
  // selectors, so the lookup always missed and "Answer speed" reported 0
  // milliseconds — on a service that was answering in about a second. Match on
  // the parsed route instead of on the raw key.
  const chatP95 = React.useMemo(() => {
    const rows = Object.entries(data?.requests?.latency ?? {});
    const hit = rows.find(([key]) => {
      const route = routeFromMetricKey(key);
      return route === "/v1/chat" || route === "/v1/chat/stream";
    });
    return hit?.[1]?.p95;
  }, [data]);
  const conversations = data?.conversations?.total_conversations ?? 0;
  const rated = feedback.data?.total ?? 0;
  const ratedShare = conversations > 0 ? Math.round((rated / conversations) * 100) : null;

  const refresh = () => {
    void dash.refetch();
    void feedback.refetch();
    void tickets.refetch();
    void ticketQueue.refetch();
  };

  return (
    <OpsPage
      eyebrow="Observe"
      title="Analytics Dashboard"
      description="How the assistant is performing: how fast it answers, what taxpayers ask about, where its answers come from, and whether taxpayers found them helpful. Every panel says in plain words what its number means."
      actions={
        <>
          <Freshness
            updatedAt={dash.dataUpdatedAt}
            isFetching={dash.isFetching}
            onRefresh={refresh}
          />
          <PeriodPicker days={days} onChange={setDays} />
        </>
      }
    >
      {dash.isError ? (
        <ErrorState
          title="The analytics service did not answer"
          body={
            (dash.error as Error)?.message
              ? `${(dash.error as Error).message}. Nothing on this page is current.`
              : "Nothing on this page is current."
          }
          onRetry={refresh}
        />
      ) : null}

      {malformed ? (
        <ErrorState
          title="The analytics response was not in the expected shape"
          body="The dashboard endpoint answered, but without the conversations, sessions or requests sections this page reads. Nothing is rendered rather than a partial picture."
          onRetry={refresh}
        />
      ) : null}

      {dash.isLoading ? (
        <>
          <div className="ops-chart-grid is-3">
            {[0, 1, 2].map((i) => (
              <div className="ops-chart-card" key={i}>
                <Skeleton width="50%" height={12} />
                <Skeleton height={92} radius="var(--ops-radius-sm)" />
              </div>
            ))}
          </div>
          <SkeletonStats count={5} cols={5} />
        </>
      ) : null}

      {data ? (
        <>
          <section aria-label="How well the service is doing">
            <div className="ops-chart-grid is-3">
              <SloGaugeCard
                label="Answer speed"
                term="p95 latency"
                value={chatP95 ?? 0}
                target={2000}
                unit="ms"
                invert
                format={(value) => plainSeconds(value)}
                note="19 out of every 20 answers arrive faster than this. Counted since the service last restarted, so it does not change when you change the period above."
              />
              <SloGaugeCard
                label="How sure the assistant was"
                term="mean confidence"
                value={(data.conversations?.avg_confidence ?? 0) * 100}
                target={70}
                unit="%"
                note="How well the average answer was supported by the URA documents behind it. A low figure means the assistant is answering from weak material, not that it was wrong."
              />
              <SloGaugeCard
                label="Found helpful by taxpayers"
                value={feedback.data?.satisfaction_pct ?? 0}
                target={80}
                unit="%"
                note="Of the answers taxpayers rated, this share was marked helpful. Most answers are never rated, so this reflects the people who chose to tell us."
              />
            </div>

            {/* Named as targets, not drawn as measurements. */}
            <div className="ops-note" role="note">
              <span className="ops-note-mark" aria-hidden="true">
                ⓘ
              </span>
              <div>
                <p className="ops-note-title">Three more targets are checked before release, not here</p>
                <p className="ops-note-body">
                  Uptime (99.9%), accessibility (a Lighthouse score of at least 90) and how much
                  of the code is covered by tests (at least 80%) are checked automatically every
                  time a change is released. They used to be drawn on this row as if they were
                  live measurements, which made three fixed numbers look like readings.
                </p>
              </div>
            </div>
          </section>

          <section aria-label="How much the assistant is being used" className="ops-stat-grid an-stats">
            <StatCard
              label="Questions answered"
              value={compact(conversations)}
              hint={`in the last ${days} days`}
            />
            <StatCard
              label="People served"
              value={compact(data.sessions?.total_sessions)}
              hint={`${data.sessions?.avg_messages_per_session?.toFixed(1) ?? "—"} questions each, on average`}
            />
            <StatCard
              label="Typical answer time"
              value={plainSeconds(data.conversations?.avg_response_time_ms ?? 0)}
              hint="averaged across every answer"
            />
            <StatCard
              label="Answers taxpayers rated"
              value={compact(rated)}
              hint={
                ratedShare != null
                  ? `${ratedShare}% of answers were rated`
                  : "nobody has rated an answer yet"
              }
            />
            <StatCard
              label="Running without a restart"
              value={formatUptime(data.uptime_seconds ?? 0)}
              hint="since the service last started, not the period above"
            />
          </section>

          <section aria-label="How long things take" className="ops-chart-grid">
            <LatencyChart latency={data.requests?.latency ?? {}} />
          </section>

          <section aria-label="What taxpayers ask, and how it goes" className="ops-chart-grid is-3">
            <TopicBarChart data={data.conversations?.top_topics ?? []} title="What taxpayers ask about" />
            <RetrievalModeChart counters={data.requests?.counters ?? {}} />
            {feedback.data ? (
              <FeedbackPieChart
                thumbsUp={feedback.data.thumbs_up}
                thumbsDown={feedback.data.thumbs_down}
              />
            ) : null}
          </section>

          {tickets.data && tickets.data.total > 0 ? (
            <section aria-label="Questions passed to an officer" className="ops-chart-grid is-2">
              <TicketStatusChart stats={tickets.data} />

              <OpsPanel
                id="an-queue"
                title="Waiting for an officer"
                end={
                  <a className="ops-btn is-ghost is-sm" href="/admin/tickets?status=open">
                    Work the queue
                  </a>
                }
                flush
                bare
              >
                {ticketQueue.data && ticketQueue.data.tickets.length > 0 ? (
                  <ul className="an-queue">
                    {ticketQueue.data.tickets.map((ticket) => (
                      <li key={ticket.id}>
                        <a
                          className="an-queue-link"
                          href={`/admin/tickets?ticket=${encodeURIComponent(ticket.id)}`}
                        >
                          <span
                            className={`ops-chip ops-chip-caps ${
                              ticket.priority === "urgent"
                                ? "is-danger"
                                : ticket.priority === "high"
                                  ? "is-warn"
                                  : ""
                            }`}
                          >
                            {ticket.priority}
                          </span>
                          <span className="an-queue-body">
                            <span className="an-queue-topic">
                              {ticket.handoff?.topic?.replaceAll("_", " ") || ticket.reason || "Escalation"}
                            </span>
                            <span className="an-queue-summary">
                              {ticket.handoff?.summary || ticket.user_query}
                            </span>
                          </span>
                          <span className="an-queue-time">
                            {formatSeconds(ticket.updated_at)}
                          </span>
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState
                    title="Nothing waiting"
                    body="Every question passed to an officer in this period has been picked up."
                  />
                )}
              </OpsPanel>
            </section>
          ) : null}

          {feedback.data && feedback.data.recent && feedback.data.recent.length > 0 ? (
            <OpsPanel
              id="an-feedback"
              title="What taxpayers said"
              note="Ratings and comments on specific answers. Questions are shown exactly as they were typed."
              flush
              bare
            >
              <TableScroll label="Recent feedback">
                <table className="ops-table">
                  <thead>
                    <tr>
                      <th scope="col">Rating</th>
                      <th scope="col">Taxpayer question</th>
                      <th scope="col">Comment</th>
                      <th scope="col">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feedback.data.recent.slice(0, 10).map((f) => (
                      <tr key={f.id}>
                        <td>
                          {/* The word, not a glyph: a screen reader used to read
                              the rating column aloud as "plus" and "hyphen". */}
                          <span className={`ops-chip ${f.rating === "up" ? "is-good" : "is-danger"}`}>
                            {f.rating === "up" ? "Helpful" : "Not helpful"}
                          </span>
                        </td>
                        <td>
                          <span className="ops-cell-clamp">{f.user_query}</span>
                        </td>
                        <td>
                          <span className="ops-cell-clamp ops-cell-sub">{f.comment || "—"}</span>
                        </td>
                        <td className="an-when">
                          {formatSeconds(f.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </OpsPanel>
          ) : null}
        </>
      ) : null}
    </OpsPage>
  );
}

export default function AnalyticsDashboard() {
  return (
    <StaffGuard current="/analytics" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <Dashboard />}
    </StaffGuard>
  );
}
