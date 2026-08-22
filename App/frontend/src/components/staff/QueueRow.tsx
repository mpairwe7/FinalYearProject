"use client";

import React from "react";
import type { TicketQueueItem } from "../../services/analyticsApi";
import { topicLabel, waitingFor, waitTone } from "../../lib/ticketUi";
import "./staffTickets.css";

/**
 * One case in the queue.
 *
 * The row carries four things an officer triages on — how urgent, what it is
 * about, who has it, how long it has waited — and the wait is the one that
 * changes colour, because it is the only one that gets worse on its own. An
 * unclaimed case says so in words rather than by the absence of a name.
 */
export function QueueRow({
  ticket,
  selected,
  onSelect,
}: {
  ticket: TicketQueueItem;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const tone = waitTone(ticket.created_at, ticket.first_response_at, ticket.reply_at);
  const waitClass = tone === "ok" ? "" : ` is-${tone}`;
  return (
    <button
      type="button"
      className={`st-row${selected ? " is-selected" : ""}`}
      onClick={() => onSelect(ticket.id)}
      aria-pressed={selected}
    >
      <span className={`st-pri st-pri-${ticket.priority}`}>{ticket.priority}</span>
      <span className="st-row-body">
        <span className="st-row-topic">{ticket.reason || topicLabel(ticket)}</span>
        <span className="st-row-query">{ticket.user_query}</span>
        <span className="st-row-meta">
          {ticket.team ? <span className="st-row-team">{ticket.team.replace(/_/g, " ")}</span> : null}
          {ticket.assignee ? (
            <span>Assigned {ticket.assignee}</span>
          ) : (
            <span className="st-row-unclaimed">Unassigned</span>
          )}
          {ticket.status !== "open" ? <span>{ticket.status}</span> : null}
        </span>
      </span>
      <span className={`st-row-wait${waitClass}`} title="waiting since it was raised">
        {waitingFor(ticket.created_at)}
      </span>
    </button>
  );
}
