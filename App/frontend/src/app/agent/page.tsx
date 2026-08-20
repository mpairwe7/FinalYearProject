"use client";

/**
 * Tax agent queue — one case at a time.
 *
 * `/admin/tickets` is the full console. This page answers: what should I
 * pick up next, and what do I need before I reply.
 */
import React, { useMemo, useRef } from "react";
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

const QUEUE_TABS = ["next", "mine", "resolved"] as const;
type QueueTab = (typeof QUEUE_TABS)[number];

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

  // The case the officer explicitly opened, or arrived on via ?ticket=.
  // Below 960px the queue and the case are alternating views, so this — and
  // not the fallback below — decides which of the two is on screen; keeping
  // them separate is what lets "Back to queue" actually go back.
  const openedId =
    view.ticket && tickets.some((ticket) => ticket.id === view.ticket) ? view.ticket : null;

  // Land on the top of the queue rather than an empty pane: above 960px both
  // panes are on screen together, so an empty one is just wasted space on a
  // work queue. (/admin/tickets is the browse-everything console and starts
  // empty on purpose — this page does not.)
  const activeId = openedId ?? tickets[0]?.id ?? null;

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
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const selectTab = (next: QueueTab) => {
    if (next === "next") setView({ status: "open", mine: false, ticket: "" });
    if (next === "mine") setView({ status: "assigned", mine: true, ticket: "" });
    if (next === "resolved") setView({ status: "resolved", mine: false, ticket: "" });
  };

  const onTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % QUEUE_TABS.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index - 1 + QUEUE_TABS.length) % QUEUE_TABS.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = QUEUE_TABS.length - 1;
    if (next === null) return;
    event.preventDefault();
    selectTab(QUEUE_TABS[next]);
    tabRefs.current[next]?.focus();
  };

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
        {QUEUE_TABS.map((queueTab, index) => (
          <button
            key={queueTab}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`ag-tab-${queueTab}`}
            aria-controls="ag-panel"
            aria-selected={tab === queueTab}
            tabIndex={tab === queueTab ? 0 : -1}
            className={tab === queueTab ? "active" : ""}
            onClick={() => selectTab(queueTab)}
            onKeyDown={(event) => onTabKeyDown(event, index)}
          >
            {queueTab === "next" ? "Next up" : queueTab === "mine" ? "Mine" : "Resolved"}
          </button>
        ))}
      </div>

      <div
        className={`ag-split${openedId ? " is-open" : ""}`}
        id="ag-panel"
        role="tabpanel"
        aria-labelledby={`ag-tab-${tab}`}
      >
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
