"use client";

/**
 * Operations overview — the admin landing page.
 *
 * Deliberately does not duplicate what exists: `/admin/tickets` is a 386-line
 * staff console and `/analytics` is a full observability dashboard. This page
 * answers one question — what needs attention right now — and links to them.
 *
 * Every figure comes from an endpoint that already ships:
 * `/v1/admin/tickets/stats`, `/tickets/sla`, `/tickets` (existing hooks) and
 * `/v1/authority/status`, which had no UI at all despite gating whether the
 * assistant will quote a rate.
 *
 * Standards: ISO/IEC 25010:2023 §4 (Interaction Capability), WCAG 2.2 AA.
 */
import React, { useEffect, useState } from "react";
import StaffGuard from "../../components/StaffGuard";
import { useTicketQueue, useTicketSla, useTicketStats } from "../../hooks/useAnalyticsDashboard";
import { authHeaders } from "../../lib/authSession";
import "./admin.css";

/** Seconds → a duration an officer reads at a glance, not 4,920. */
function humanDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function waitingFor(createdAt: number): string {
  const mins = Math.max(0, Math.round((Date.now() / 1000 - createdAt) / 60));
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  if (h < 24) return `${h}h ${mins % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "warn" | "danger" | "good";
}) {
  return (
    <div className={`ov-metric${tone ? ` ov-${tone}` : ""}`}>
      <span className="ov-metric-label">{label}</span>
      <strong className="ov-metric-value">{value}</strong>
      {hint && <span className="ov-metric-hint">{hint}</span>}
    </div>
  );
}

interface AuthorityStatus {
  fresh?: boolean;
  version?: string;
  generated_at?: string;
  age_days?: number;
  max_age_days?: number;
  sources?: unknown[];
  detail?: string;
}

/** The manifest that decides whether the assistant may quote a rate at all. */
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

function Overview() {
  const { data: stats, isLoading: statsLoading } = useTicketStats(30);
  const { data: sla } = useTicketSla(30);
  const { data: queue } = useTicketQueue("open", 6);
  const authority = useAuthorityStatus();

  const items = queue?.tickets ?? [];
  const urgent = items.filter((t) => t.priority === "urgent").length;
  const awaiting = sla?.awaiting_first_response ?? 0;

  return (
    <main className="ov-page">
      <header className="ov-head">
        <div>
          <h1>Operations overview</h1>
          <p className="ov-sub">Escalations, service levels and answer authority — last 30 days</p>
        </div>
        <a className="ov-cta" href="/admin/tickets">
          Open the full queue
        </a>
      </header>

      <section className="ov-grid" aria-label="Escalation summary">
        <Metric
          label="Open escalations"
          value={statsLoading ? "…" : String(stats?.open ?? 0)}
          hint={`${stats?.total ?? 0} raised in the period`}
        />
        <Metric
          label="Awaiting first response"
          value={String(awaiting)}
          hint="nobody has replied yet"
          tone={awaiting > 0 ? "warn" : "good"}
        />
        <Metric
          label="Median first response"
          value={humanDuration(sla?.median_response_seconds)}
          hint={`${sla?.responded ?? 0} of ${sla?.tickets ?? 0} answered`}
        />
        <Metric
          label="Median time to resolve"
          value={humanDuration(sla?.median_resolution_seconds)}
          hint={`${stats?.resolved ?? 0} resolved`}
        />
      </section>

      <div className="ov-cols">
        <section className="ov-panel" aria-labelledby="q-h">
          <div className="ov-panel-head">
            <h2 id="q-h">Waiting longest</h2>
            {urgent > 0 && <span className="ov-badge-urgent">{urgent} urgent</span>}
          </div>
          {items.length > 0 ? (
            <ul className="ov-queue">
              {items.map((t) => (
                <li key={t.id}>
                  <span className={`ov-pri ov-pri-${t.priority}`}>{t.priority}</span>
                  <span className="ov-q-body">
                    <span className="ov-q-topic">{t.handoff?.topic || t.reason || "Escalation"}</span>
                    <span className="ov-q-query">{t.user_query}</span>
                  </span>
                  <span className="ov-q-wait" title="Waiting since escalation">
                    {waitingFor(t.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ov-empty">
              {queue ? "Nothing is waiting — the queue is clear." : "Loading the queue…"}
            </p>
          )}
        </section>

        <section className="ov-panel" aria-labelledby="a-h">
          <div className="ov-panel-head">
            <h2 id="a-h">Answer authority</h2>
          </div>
          <p className="ov-panel-note">
            Rate answers are refused outright when this manifest is stale, so it is
            the first thing to check when the assistant starts declining figures.
          </p>
          {authority.loading ? (
            <p className="ov-empty">Checking…</p>
          ) : authority.error ? (
            <p className="ov-empty">Unavailable: {authority.error}</p>
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
          <nav className="ov-links" aria-label="More">
            <a href="/analytics">Analytics dashboard</a>
            <a href="/analytics/evaluation">Answer evaluation</a>
          </nav>
        </section>
      </div>
    </main>
  );
}

export default function AdminOverviewPage() {
  return (
    <StaffGuard current="/admin" requireRoles={["ura_admin", "ura_auditor"]}>
      {() => <Overview />}
    </StaffGuard>
  );
}
