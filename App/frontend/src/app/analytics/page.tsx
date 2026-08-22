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
import TopicBarChart from "../../components/charts/TopicBarChart";
import FeedbackPieChart from "../../components/charts/FeedbackPieChart";
import RetrievalModeChart from "../../components/charts/RetrievalModeChart";
import LatencyChart from "../../components/charts/LatencyChart";
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
  const chatP95 = data?.requests?.latency?.["POST|/v1/chat"]?.p95;
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
      description="How the assistant is performing: service levels, what taxpayers ask about, how answers are produced, and what they think of them."
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
          <SkeletonStats count={5} />
        </>
      ) : null}

      {data ? (
        <>
          <section aria-label="Service levels">
            <div className="ops-chart-grid is-3">
              <SloGaugeCard
                label="Chat p95 latency"
                value={chatP95 ?? 0}
                target={2000}
                unit="ms"
                invert
                note="Since this replica started — request histograms are not period-scoped."
              />
              <SloGaugeCard
                label="Average confidence"
                value={(data.conversations?.avg_confidence ?? 0) * 100}
                target={70}
                unit="%"
              />
              <SloGaugeCard
                label="Answer satisfaction"
                value={feedback.data?.satisfaction_pct ?? 0}
                target={80}
                unit="%"
              />
            </div>

            {/* Named as targets, not drawn as measurements. */}
            <div className="ops-note" role="note">
              <span className="ops-note-mark" aria-hidden="true">
                ⓘ
              </span>
              <div>
                <p className="ops-note-title">Three more targets are verified in the pipeline, not here</p>
                <p className="ops-note-body">
                  Availability (99.9%), Lighthouse accessibility (≥ 90) and test coverage (≥ 80%)
                  are gated in CI on every push. They used to be drawn on this row as gauges with
                  hardcoded values, which made three constants look like live telemetry.
                </p>
              </div>
            </div>
          </section>

          <section aria-label="Volume" className="ops-stat-grid an-stats">
            <StatCard
              label="Conversations"
              value={compact(conversations)}
              hint={`over ${days} days`}
            />
            <StatCard
              label="Sessions"
              value={compact(data.sessions?.total_sessions)}
              hint={`${data.sessions?.avg_messages_per_session?.toFixed(1) ?? "—"} messages each`}
            />
            <StatCard
              label="Avg response time"
              value={`${Math.round(data.conversations?.avg_response_time_ms ?? 0)}ms`}
              hint="mean across answered turns"
            />
            <StatCard
              label="Rated answers"
              value={compact(rated)}
              hint={ratedShare != null ? `${ratedShare}% of conversations` : "no ratings yet"}
            />
            <StatCard
              label="Replica uptime"
              value={formatUptime(data.uptime_seconds ?? 0)}
              hint="this process, not the period"
            />
          </section>

          <section aria-label="Latency" className="ops-chart-grid">
            <LatencyChart latency={data.requests?.latency ?? {}} />
          </section>

          <section aria-label="Demand and quality" className="ops-chart-grid is-3">
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
            <section aria-label="Escalations" className="ops-chart-grid is-2">
              <TicketStatusChart stats={tickets.data} />

              <OpsPanel
                id="an-queue"
                title="Open escalations"
                end={
                  <a className="ops-btn is-ghost is-sm" href="/admin/tickets?status=open">
                    Work the queue
                  </a>
                }
                flush
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
                            {new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(
                              ticket.updated_at * 1000,
                            )}
                          </span>
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState title="Nothing open" body="Every escalation raised in this period has been picked up." />
                )}
              </OpsPanel>
            </section>
          ) : null}

          {feedback.data && feedback.data.recent && feedback.data.recent.length > 0 ? (
            <OpsPanel
              id="an-feedback"
              title="Recent feedback"
              note="What taxpayers said about specific answers. Queries are shown as they were typed."
              flush
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
                          {new Intl.DateTimeFormat("en-GB", { dateStyle: "medium" }).format(
                            f.created_at * 1000,
                          )}
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
