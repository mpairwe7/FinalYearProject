"use client";

/**
 * Tax agent queue — one case at a time.
 *
 * `/admin/tickets` is the full console. This page answers: what should I
 * pick up next, and what do I need before I reply.
 *
 * The redesign kept the shape (tabs, split pane, land on the top case) and
 * fixed what was around it: the queue's keyboard shortcuts were real but only
 * documented on the *other* page, in an 11px line of grey text, so they now sit
 * under the tabs as key caps; the counts strip reserves its space instead of
 * appearing mid-load and shoving the tabs down; and the case pane scrolls under
 * a sticky header, because "Back to queue", the status and the assign control
 * were the first things to disappear on a long transcript.
 */
import React, { useMemo, useRef } from "react";
import StaffGuard, { type StaffIdentity } from "../../components/StaffGuard";
import { OpsPage } from "../../components/ops/OpsPage";
import { KeyHint } from "../../components/ops/Controls";
import { EmptyState, ErrorState, SkeletonRows } from "../../components/ops/States";
import { QueueRow } from "../../components/staff/QueueRow";
import { signedInName } from "../../lib/roles";
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

const TAB_LABEL: Record<QueueTab, string> = {
  next: "Next up",
  mine: "Mine",
  resolved: "Resolved",
};

function AgentQueue({ who }: { who: StaffIdentity }) {
  const handle = officerHandle(who);
  const [view, setView] = useQueueView();
  const status = view.mine ? "assigned" : view.status === "resolved" ? "resolved" : "open";
  const { data: queue, isLoading, error, refetch } = useTicketQueueFull(status, "", "", 50);
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

  const tab: QueueTab = view.status === "resolved" ? "resolved" : view.mine ? "mine" : "next";
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
    <OpsPage
      eyebrow="Work"
      title="My queue"
      description={`Signed in as ${signedInName(who)}. Claim a case, read the brief, then reply.`}
      actions={
        <div className="ag-counts">
          <span className="ops-chip">
            <strong>{tickets.length}</strong>{" "}
            {tab === "mine" ? "mine" : tab === "resolved" ? "resolved" : "next up"}
          </span>
          {urgent > 0 ? (
            <span className="ops-chip is-danger">
              <strong>{urgent}</strong> urgent
            </span>
          ) : null}
          {breaching > 0 ? (
            <span className="ops-chip is-danger">
              <strong>{breaching}</strong> past 24h
            </span>
          ) : null}
          {awaiting > 0 ? (
            <span className="ops-chip is-warn">
              <strong>{awaiting}</strong> awaiting first reply
            </span>
          ) : null}
        </div>
      }
    >
      <div className="ag-tabbar">
        <div className="ops-segmented ag-status-tabs" role="tablist" aria-label="Queue view">
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
              {TAB_LABEL[queueTab]}
              {tab === queueTab && !isLoading ? (
                <span className="ops-filter-count">{tickets.length}</span>
              ) : null}
            </button>
          ))}
        </div>

        {/* The shortcuts were always here; nothing on this page said so. */}
        <div className="ops-hints ag-hints">
          <KeyHint keys={["j", "k"]}>move</KeyHint>
          <KeyHint keys={["r"]}>reply</KeyHint>
          <KeyHint keys={["a"]}>assign to me</KeyHint>
          <KeyHint keys={["⌘K"]}>go anywhere</KeyHint>
        </div>
      </div>

      <div
        className={`ag-split${openedId ? " is-open" : ""}`}
        id="ag-panel"
        role="tabpanel"
        aria-labelledby={`ag-tab-${tab}`}
      >
        <section className="ag-queue-pane" aria-label="Queue">
          {isLoading ? <SkeletonRows rows={6} height={62} /> : null}
          {error ? (
            <ErrorState body="The queue did not load." onRetry={() => void refetch()} />
          ) : null}
          {!isLoading && !error && tickets.length === 0 ? (
            <EmptyState
              title={
                tab === "mine"
                  ? "Nothing assigned to you"
                  : tab === "resolved"
                    ? "Nothing resolved in view"
                    : "Nothing waiting"
              }
              body={
                tab === "mine"
                  ? "Pick up a case from Next up and it will move here."
                  : tab === "resolved"
                    ? "Resolved cases appear here once someone closes them."
                    : "Every escalation has a first reply. New arrivals announce themselves at the top of the console."
              }
            />
          ) : null}
          {tickets.length > 0 ? (
            <ul className="ag-queue">
              {tickets.map((ticket) => (
                <li key={ticket.id}>
                  <QueueRow ticket={ticket} selected={ticket.id === activeId} onSelect={select} />
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="ag-detail" aria-label="Ticket detail">
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
          ) : (
            <EmptyState title="Pick a ticket to see the brief" />
          )}
        </section>
      </div>
    </OpsPage>
  );
}

export default function AgentPage() {
  return (
    <StaffGuard current="/agent" requireRoles={["ura_staff", "ura_admin"]}>
      {(who) => <AgentQueue who={who} />}
    </StaffGuard>
  );
}
