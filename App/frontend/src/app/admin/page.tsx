"use client";

/**
 * Operations overview — what needs attention right now.
 *
 * `/admin/tickets` is the workbench and `/analytics` is observability.
 * This page is the morning board: SLA, authority, and the cases waiting
 * longest — each one a deep link into the queue.
 */
import React, { useEffect, useState } from "react";
import StaffGuard from "../../components/StaffGuard";
import { useTicketQueueFull, useTicketSla, useTicketStats } from "../../hooks/useAnalyticsDashboard";
import { authHeaders } from "../../lib/authSession";
import { formatDuration, waitingFor, waitTone } from "../../lib/ticketUi";
import "./admin.css";

function Metric({
  label,
  value,
  hint,
  tone,
  href,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "warn" | "danger" | "good";
  href?: string;
}) {
  const inner = (
    <>
      <span className="ov-metric-label">{label}</span>
      <strong className="ov-metric-value">{value}</strong>
      {hint && <span className="ov-metric-hint">{hint}</span>}
    </>
  );
  if (href) {
    return (
      <a className={`ov-metric ov-metric-link${tone ? ` ov-${tone}` : ""}`} href={href}>
        {inner}
      </a>
    );
  }
  return <div className={`ov-metric${tone ? ` ov-${tone}` : ""}`}>{inner}</div>;
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
  const { data: queue } = useTicketQueueFull("open", "", "", 20);
  const authority = useAuthorityStatus();

  const items = queue?.tickets ?? [];
  const urgent = items.filter((t) => t.priority === "urgent").length;
  const awaiting = sla?.awaiting_first_response ?? 0;
  const breaching = sla?.breaching ?? 0;
  const unassigned = items.filter((t) => !t.assignee).length;

  return (
    <main className="ov-page" id="staff-main">
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
          href="/admin/tickets?status=open"
        />
        <Metric
          label="Awaiting first response"
          value={String(awaiting)}
          hint="nobody has replied yet"
          tone={awaiting > 0 ? "warn" : "good"}
          href="/admin/tickets?status=open"
        />
        <Metric
          label="Past 24-hour SLA"
          value={String(breaching)}
          hint="open or in progress, first- or next-reply over 24h"
          tone={breaching > 0 ? "danger" : "good"}
          href="/admin/tickets?status=open"
        />
        <Metric
          label="Unassigned"
          value={String(unassigned)}
          hint="waiting to be claimed"
          tone={unassigned > 0 ? "warn" : "good"}
          href="/admin/tickets?status=open"
        />
        <Metric
          label="Median first response"
          value={formatDuration(sla?.median_response_seconds)}
          hint={`${sla?.responded ?? 0} of ${sla?.tickets ?? 0} answered`}
        />
        <Metric
          label="Median time to resolve"
          value={formatDuration(sla?.median_resolution_seconds)}
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
              {items.map((t) => {
                const tone = waitTone(t.created_at, t.first_response_at, t.reply_at);
                return (
                  <li key={t.id}>
                    <a className="ov-q-link" href={`/admin/tickets?ticket=${encodeURIComponent(t.id)}`}>
                      <span className={`ov-pri ov-pri-${t.priority}`}>{t.priority}</span>
                      <span className="ov-q-body">
                        <span className="ov-q-topic">{t.handoff?.topic || t.reason || "Escalation"}</span>
                        <span className="ov-q-query">{t.user_query}</span>
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
            <a href="/agent">Agent queue</a>
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
