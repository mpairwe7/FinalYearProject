"use client";

/**
 * One officer chat as the audit reads it: what the taxpayer asked for, the
 * chat in order, how it ended and how the taxpayer rated it.
 *
 * Read-only — the administrator audits the service and does not reply.
 * Opening it is a read of taxpayer data and is recorded as such
 * (`staff.ticket_viewed`), so it is read once, not re-polled.
 */
import React from "react";
import { EmptyState, ErrorState, SkeletonRows } from "../ops/States";
import { useSkeletonVisible } from "../../hooks/useLoadingPhase";
import { useAuditedTicket } from "../../hooks/useQualityAudit";
import { ticketRef } from "../../lib/ticketUi";
import { TicketChatThread } from "../staff/TicketChatThread";
import "../staff/staffTickets.css";

export function OfficerChatDetail({ ticketId }: { ticketId: string }) {
  const ticket = useAuditedTicket(ticketId);
  const loading = useSkeletonVisible(ticket.isLoading);

  if (loading) return <SkeletonRows rows={4} height={72} />;
  if (ticket.isError) {
    const gone = /^404\b/.test((ticket.error as Error).message);
    return gone ? (
      <EmptyState title="This case no longer exists" body="Its rating is kept for the audit period; the case itself has been deleted." />
    ) : (
      <ErrorState title="This chat did not load" body={(ticket.error as Error).message} onRetry={() => void ticket.refetch()} />
    );
  }
  const data = ticket.data;
  if (!data) return null;
  const summary = data.handoff?.summary;

  return (
    <article className="qa-chat-detail" aria-label="Officer chat">
      <header className="qa-timeline-head">
        <p className="qa-timeline-pattern">
          <strong>#{ticketRef(data.id)}</strong>
          {data.team ? <span> · {data.team.replaceAll("_", " ")}</span> : null}
          {data.chat?.officer_name ? <span> · {data.chat.officer_name}</span> : null}
        </p>
        <p className="qa-timeline-note">
          {data.reason || "Escalated to an officer"}
          {summary ? ` — ${summary}` : ""}
        </p>
      </header>
      <TicketChatThread ticket={data} />
    </article>
  );
}
