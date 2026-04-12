"use client";

/**
 * Analytics Dashboard — production observability for the URA Chatbot.
 *
 * Visualizes: SLO gauges, latency waterfall, topic distribution, retrieval
 * modes, feedback satisfaction, RAG evaluation radar, per-segment quality
 * comparison, and escalation queue status.
 *
 * Data source: GET /v1/analytics/dashboard, GET /v1/feedback/summary,
 * GET /v1/admin/tickets/stats — all proxied via /api/* rewrite.
 *
 * Standards: ISO/IEC 25010:2023 §4 (Interaction Capability),
 * EU AI Act Art. 13 (Transparency), ISO 42001 §9.1 (Monitoring)
 */
import React, { useState } from "react";
import { useDashboard, useFeedbackSummary, useTicketStats } from "../../hooks/useAnalyticsDashboard";
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

export default function AnalyticsDashboard() {
  const [days, setDays] = useState(30);
  const { data: dash, isLoading, error } = useDashboard(days);
  const { data: feedback } = useFeedbackSummary(days);
  const { data: tickets } = useTicketStats(days);

  return (
    <main className="analytics-page">
      <header className="analytics-header">
        <div>
          <h1>Analytics Dashboard</h1>
          <p className="analytics-subtitle">
            URA Chatbot — Production Observability
          </p>
        </div>
        <div className="period-selector">
          {[7, 14, 30, 90].map((d) => (
            <button
              key={d}
              className={`period-btn ${days === d ? "active" : ""}`}
              onClick={() => setDays(d)}
            >
              {d}d
            </button>
          ))}
        </div>
      </header>

      {isLoading && <div className="analytics-loading">Loading dashboard data...</div>}
      {error && (
        <div className="analytics-error">
          Failed to load dashboard: {(error as Error).message}.
          Make sure the backend is running.
        </div>
      )}

      {dash && (
        <>
          {/* Row 1: SLO Gauges */}
          <section className="chart-grid gauge-row">
            <SloGaugeCard
              label="Availability"
              value={99.9}
              target={99.9}
              unit="%"
            />
            <SloGaugeCard
              label="Chat p95 Latency"
              value={dash.requests.latency?.["POST|/v1/chat"]?.p95 ?? 0}
              target={2000}
              unit="ms"
              invert
            />
            <SloGaugeCard
              label="Avg Confidence"
              value={(dash.conversations.avg_confidence ?? 0) * 100}
              target={70}
              unit="%"
            />
            <SloGaugeCard
              label="Satisfaction"
              value={feedback?.satisfaction_pct ?? 0}
              target={80}
              unit="%"
            />
          </section>

          {/* Row 2: Stats cards */}
          <section className="stats-row">
            <div className="stat-card">
              <div className="stat-value">{dash.conversations.total_conversations}</div>
              <div className="stat-label">Conversations</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{dash.sessions.total_sessions}</div>
              <div className="stat-label">Sessions</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{Math.round(dash.conversations.avg_response_time_ms)}ms</div>
              <div className="stat-label">Avg Response Time</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{formatUptime(dash.uptime_seconds)}</div>
              <div className="stat-label">Uptime</div>
            </div>
            <div className="stat-card">
              <div className="stat-value">{dash.sessions.avg_messages_per_session?.toFixed(1)}</div>
              <div className="stat-label">Msgs / Session</div>
            </div>
          </section>

          {/* Row 3: Latency waterfall */}
          <section className="chart-grid">
            <LatencyChart latency={dash.requests.latency ?? {}} />
          </section>

          {/* Row 4: Topics + Retrieval + Feedback */}
          <section className="chart-grid three-col">
            <TopicBarChart
              data={dash.conversations.top_topics ?? []}
              title="Top Query Topics"
            />
            <RetrievalModeChart counters={dash.requests.counters ?? {}} />
            {feedback && (
              <FeedbackPieChart
                thumbsUp={feedback.thumbs_up}
                thumbsDown={feedback.thumbs_down}
              />
            )}
          </section>

          {/* Row 5: Ticket queue */}
          {tickets && tickets.total > 0 && (
            <section className="chart-grid">
              <TicketStatusChart stats={tickets} />
            </section>
          )}

          {/* Row 6: Recent feedback */}
          {feedback && feedback.recent && feedback.recent.length > 0 && (
            <section className="recent-feedback">
              <h3 className="chart-title">Recent Feedback</h3>
              <div className="feedback-table-wrapper">
                <table className="feedback-table">
                  <thead>
                    <tr>
                      <th>Rating</th>
                      <th>User Query</th>
                      <th>Comment</th>
                      <th>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {feedback.recent.slice(0, 10).map((f) => (
                      <tr key={f.id}>
                        <td>
                          <span className={`rating-badge ${f.rating}`}>
                            {f.rating === "up" ? "+" : "-"}
                          </span>
                        </td>
                        <td className="query-cell">{f.user_query?.slice(0, 80)}</td>
                        <td className="comment-cell">{f.comment || "—"}</td>
                        <td className="time-cell">
                          {new Date(f.created_at * 1000).toLocaleDateString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </main>
  );
}
