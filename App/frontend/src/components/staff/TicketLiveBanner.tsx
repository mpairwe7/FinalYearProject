"use client";

import React from "react";
import { useTicketStream } from "../../hooks/useTicketStream";
import "./staffTickets.css";

/** Live arrival strip for every staff page. */
export function TicketLiveBanner() {
  const { latest, connected } = useTicketStream(true);
  return (
    <div className="st-live" aria-live="polite">
      <span className={`st-live-dot${connected ? " is-on" : ""}`} aria-hidden="true" />
      {latest ? (
        <a href={`/admin/tickets?ticket=${encodeURIComponent(latest.id)}`}>
          New {latest.priority} escalation: {latest.reason}
        </a>
      ) : (
        <span>{connected ? "Listening for new escalations" : "Reconnecting to the queue…"}</span>
      )}
    </div>
  );
}
