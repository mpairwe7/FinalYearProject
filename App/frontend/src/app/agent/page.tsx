"use client";

/**
 * Tax agent queue — one case at a time.
 *
 * `/admin/tickets` is the full console. This page answers: what should I
 * pick up next, and what do I need before I reply.
 */
import React, { useMemo } from "react";
import StaffGuard, { type StaffIdentity } from "../../components/StaffGuard";
import { QueueRow } from "../../components/staff/QueueRow";
import { TicketCase } from "../../components/staff/TicketCase";
import {
  useTicket,
  useTicketQueueFull,
  useTicketSla,
  useUpdateTicket,
} from "../../hooks/useAnalyticsDashboard";
import {
  isMine,
  officerHandle,
  sortQueue,
  useQueueHotkeys,
  useQueueView,
  waitTone,
} from "../../lib/ticketUi";
import "./agent.css";

function AgentQueue({ who }: { who: StaffIdentity }) {
  const handle = officerHandle(who);
  const [view, setView] = useQueueView();
  const status = view.mine ? "assigned" : view.status === "resolved" ? "resolved" : "open";
  const { data: queue, isLoading, error } = useTicketQueueFull(status, "", "", 50);
  const { data: sla } = useTicketSla(30);
  const update = useUpdateTicket();

  const tickets = useMemo(() => {
    const rows = sortQueue(queue?.tickets ?? []);
    if (view.mine) return rows.filter((ticket) => isMine(ticket, handle));
    return rows;
  }, [queue, view.mine, handle]);

  const activeId =
    view.ticket && tickets.some((ticket) => ticket.id === view.ticket)
      ? view.ticket
      : null;

  const { data: detail, isLoading: detailLoading, error: detailError } = useTicket(activeId);
  const select = (id: string) => setView({ ticket: id });

  useQueueHotkeys({
    ids: tickets.map((ticket) => ticket.id),
    selectedId: activeId,
    onSelect: select,
    onAssignMe: activeId && handle
      ? () =>
          update.mutate({
            id: activeId,
            patch: { assignee: handle, status: "assigned" },
          })
      : undefined,
    replySelector: "[data-ticket-reply]",
  });

  const urgent = tickets.filter((ticket) => ticket.priority === "urgent").length;
  const breaching = tickets.filter(
    (ticket) => waitTone(ticket.created_at, ticket.first_response_at, ticket.reply_at) === "breach",
  ).length;
  const awaiting = sla?.awaiting_first_response ?? 0;

  const tab = view.status === "resolved" ? "resolved" : view.mine ? "mine" : "next";

  return (
    <main className="ag-page" id="staff-main">
      <header className="ag-head">
        <div>
          <h1>My queue</h1>
          <p className="ag-sub">
            Signed in as {who.email || who.external_id} — claim a case, then reply
          </p>
        </div>
        <div className="ag-counts">
          <span className="ag-count">
            <strong>{tickets.length}</strong> {tab === "mine" ? "mine" : tab === "resolved" ? "resolved" : "next up"}
          </span>
          {urgent > 0 && (
            <span className="ag-count urgent">
              <strong>{urgent}</strong> urgent
            </span>
          )}
          {breaching > 0 && (
            <span className="ag-count urgent">
              <strong>{breaching}</strong> past 24h
            </span>
          )}
          {awaiting > 0 && (
            <span className="ag-count warn">
              <strong>{awaiting}</strong> awaiting first reply
            </span>
          )}
        </div>
      </header>

      <div className="ag-status-tabs" role="tablist" aria-label="Queue view">
        <button
          type="button"
          role="tab"
          id="ag-tab-next"
          aria-controls="ag-panel"
          aria-selected={tab === "next"}
          className={tab === "next" ? "active" : ""}
          onClick={() => setView({ status: "open", mine: false, ticket: "" })}
        >
          Next up
        </button>
        <button
          type="button"
          role="tab"
          id="ag-tab-mine"
          aria-controls="ag-panel"
          aria-selected={tab === "mine"}
          className={tab === "mine" ? "active" : ""}
          onClick={() => setView({ status: "assigned", mine: true, ticket: "" })}
        >
          Mine
        </button>
        <button
          type="button"
          role="tab"
          id="ag-tab-resolved"
          aria-controls="ag-panel"
          aria-selected={tab === "resolved"}
          className={tab === "resolved" ? "active" : ""}
          onClick={() => setView({ status: "resolved", mine: false, ticket: "" })}
        >
          Resolved
        </button>
      </div>

      <div className={`ag-split${activeId ? " is-open" : ""}`} id="ag-panel" role="tabpanel">
        <section className="ag-queue-pane" aria-label="Queue">
          {isLoading && <p className="ag-empty">Loading the queue…</p>}
          {error && <p className="ag-empty ag-error">Could not load the queue.</p>}
          {!isLoading && !error && tickets.length === 0 && (
            <p className="ag-empty">
              {tab === "mine"
                ? "Nothing assigned to you. Pick up a case from Next up."
                : `Nothing ${tab === "resolved" ? "resolved" : "waiting"}.`}
            </p>
          )}
          <ul className="ag-queue">
            {tickets.map((ticket) => (
              <li key={ticket.id}>
                <QueueRow
                  ticket={ticket}
                  selected={ticket.id === activeId}
                  onSelect={select}
                />
              </li>
            ))}
          </ul>
        </section>

        <section className="ag-detail" aria-label="Ticket detail">
          {!activeId && <p className="ag-empty">Pick a ticket to see the brief.</p>}
          {activeId ? (
            <TicketCase
              ticket={detail}
              loading={detailLoading}
              error={Boolean(detailError)}
              who={who}
              pending={update.isPending}
              isError={update.isError}
              isSuccess={update.isSuccess}
              onPatch={(patch) => update.mutate({ id: activeId, patch })}
              onBack={() => setView({ ticket: "" })}
            />
          ) : null}
        </section>
      </div>
    </main>
  );
}

export default function AgentPage() {
  return (
    <StaffGuard current="/agent" requireRoles={["ura_staff", "ura_admin"]}>
      {(who) => <AgentQueue who={who} />}
    </StaffGuard>
  );
}
