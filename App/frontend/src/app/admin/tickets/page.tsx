"use client";

/**
 * Staff ticket console — every status, every team, a case to work.
 *
 * The escalation backend already carries everything an officer needs.
 * This page is the shared workbench: claim, brief, full transcript,
 * taxpayer reply vs internal note, then resolve.
 *
 * Two decisions that stay load-bearing:
 * - The transcript is shown in full and unedited.
 * - `officer_reply` and `staff_note` are separate inputs.
 */
import React, { useMemo } from "react";
import StaffGuard, { type StaffIdentity } from "../../../components/StaffGuard";
import { QueueRow } from "../../../components/staff/QueueRow";
import { TicketCase } from "../../../components/staff/TicketCase";
import {
  useTicket,
  useTicketQueueFull,
  useTicketSla,
  useUpdateTicket,
} from "../../../hooks/useAnalyticsDashboard";
import {
  formatDuration,
  officerHandle,
  PRIORITIES,
  STATUSES,
  STATUS_LABEL,
  ticketMatchesQuery,
  useQueueHotkeys,
  useQueueView,
} from "../../../lib/ticketUi";
import "./tickets.css";

export function StaffTicketQueue({ who }: { who?: StaffIdentity }) {
  const [view, setView] = useQueueView();
  const { data: queue, isLoading, error } = useTicketQueueFull(
    view.status,
    view.priority,
    view.team,
  );
  const { data: sla } = useTicketSla(30);
  const update = useUpdateTicket();

  const tickets = useMemo(() => {
    const rows = queue?.tickets ?? [];
    return rows.filter((ticket) => ticketMatchesQuery(ticket, view.q));
  }, [queue, view.q]);

  const teams = queue?.teams ?? [];
  const selected = view.ticket || null;
  const handle = officerHandle(who);

  const { data: detail, isLoading: detailLoading, error: detailError } = useTicket(selected);

  const select = (id: string | null) => setView({ ticket: id || "" });

  useQueueHotkeys({
    ids: tickets.map((ticket) => ticket.id),
    selectedId: selected,
    onSelect: select,
    onAssignMe:
      selected && handle
        ? () =>
            update.mutate({
              id: selected,
              patch: { assignee: handle, status: "assigned" },
            })
        : undefined,
    replySelector: "[data-ticket-reply]",
    searchSelector: "[data-ticket-search]",
  });

  return (
    <main className="tickets-page" id="staff-main">
      <header className="tickets-header">
        <div>
          <h1>Escalation queue</h1>
          <p className="tickets-subtitle">
            Urgent first, then longest waiting. Claim a case, read the brief, reply
            to the taxpayer, keep notes internal.
          </p>
          <p className="st-kbd">
            j / k move · / search · r reply · a assign to me
          </p>
        </div>
        {sla ? (
          <dl className="sla-strip">
            <div>
              <dt>Awaiting first response</dt>
              <dd className={sla.awaiting_first_response > 0 ? "is-warn" : ""}>
                {sla.awaiting_first_response}
              </dd>
            </div>
            <div>
              <dt>Median response</dt>
              <dd>{formatDuration(sla.median_response_seconds)}</dd>
            </div>
            <div>
              <dt>Median resolution</dt>
              <dd>{formatDuration(sla.median_resolution_seconds)}</dd>
            </div>
            <div>
              <dt>Past 24h SLA</dt>
              <dd className={(sla.breaching ?? 0) > 0 ? "is-warn" : ""}>
                {sla.breaching ?? 0}
              </dd>
            </div>
            <div>
              <dt>Awaiting next reply</dt>
              <dd>{sla.awaiting_next_response ?? 0}</dd>
            </div>
          </dl>
        ) : null}
      </header>

      <div className="tickets-filters" role="group" aria-label="Filter escalation queue">
        {STATUSES.map((value) => (
          <button
            key={value}
            type="button"
            className={`filter${view.status === value ? " is-active" : ""}`}
            aria-pressed={view.status === value}
            onClick={() => setView({ status: value, ticket: "" })}
          >
            {STATUS_LABEL[value]}
          </button>
        ))}
        <span className="filter-sep" />
        <button
          type="button"
          className={`filter${view.priority === "" ? " is-active" : ""}`}
          aria-pressed={view.priority === ""}
          onClick={() => setView({ priority: "" })}
        >
          all priorities
        </button>
        {PRIORITIES.map((value) => (
          <button
            key={value}
            type="button"
            className={`filter${view.priority === value ? " is-active" : ""}`}
            aria-pressed={view.priority === value}
            onClick={() => setView({ priority: value })}
          >
            {value}
          </button>
        ))}
        {teams.length > 0 ? (
          <>
            <span className="filter-sep" />
            <button
              type="button"
              className={`filter${view.team === "" ? " is-active" : ""}`}
              aria-pressed={view.team === ""}
              onClick={() => setView({ team: "" })}
            >
              all teams
            </button>
            {teams.map((value) => (
              <button
                key={value}
                type="button"
                className={`filter${view.team === value ? " is-active" : ""}`}
                aria-pressed={view.team === value}
                onClick={() => setView({ team: value })}
              >
                {value.replace(/_/g, " ")}
              </button>
            ))}
          </>
        ) : null}
        <input
          type="search"
          className="st-search"
          data-ticket-search="1"
          placeholder="Search reason, taxpayer, assignee…"
          value={view.q}
          onChange={(event) => setView({ q: event.target.value })}
          aria-label="Search tickets"
        />
      </div>

      <div className={`tickets-body${selected ? " is-open" : ""}`}>
        <section className="tickets-list" aria-label="Ticket queue">
          {isLoading ? <p className="st-empty">Loading queue…</p> : null}
          {error ? <p className="st-empty">Could not load the queue.</p> : null}
          {!isLoading && !error && tickets.length === 0 ? (
            <p className="st-empty">Nothing waiting.</p>
          ) : null}
          {tickets.map((ticket) => (
            <QueueRow
              key={ticket.id}
              ticket={ticket}
              selected={selected === ticket.id}
              onSelect={select}
            />
          ))}
        </section>

        <section className="tickets-detail" aria-label="Ticket detail">
          {selected ? (
            <TicketCase
              ticket={detail}
              loading={detailLoading}
              error={Boolean(detailError)}
              who={who}
              pending={update.isPending}
              isError={update.isError}
              isSuccess={update.isSuccess}
              onPatch={(patch) => update.mutate({ id: selected, patch })}
              onBack={() => select(null)}
            />
          ) : (
            <p className="st-empty">Select a ticket to see the conversation.</p>
          )}
        </section>
      </div>
    </main>
  );
}

export default function StaffTicketQueuePage() {
  return (
    <StaffGuard current="/admin/tickets">
      {(who) => <StaffTicketQueue who={who} />}
    </StaffGuard>
  );
}
