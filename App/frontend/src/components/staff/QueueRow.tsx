"use client";

import React from "react";
import type { TicketQueueItem } from "../../services/analyticsApi";
import { topicLabel, waitingFor, waitTone } from "../../lib/ticketUi";
import "./staffTickets.css";

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
      aria-current={selected ? "true" : undefined}
    >
      <span className={`st-pri st-pri-${ticket.priority}`}>{ticket.priority}</span>
      <span className="st-row-body">
        <span className="st-row-topic">{ticket.reason || topicLabel(ticket)}</span>
        <span className="st-row-query">{ticket.user_query}</span>
        <span className="st-row-meta">
          {ticket.team ? <span>{ticket.team.replace(/_/g, " ")}</span> : null}
          {ticket.assignee ? <span>Assigned {ticket.assignee}</span> : <span>Unassigned</span>}
          {ticket.status !== "open" ? <span>{ticket.status}</span> : null}
        </span>
      </span>
      <span className={`st-row-wait${waitClass}`} title="waiting since it was raised">
        {waitingFor(ticket.created_at)}
      </span>
    </button>
  );
}
