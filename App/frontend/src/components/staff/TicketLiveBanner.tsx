"use client";

import React, { useState } from "react";
import type { LiveEscalation } from "../../hooks/useTicketStream";
import "./staffTickets.css";

/**
 * Live arrival strip.
 *
 * It used to be a permanent full-width bar reading "Listening for new
 * escalations" on every staff page — a row of chrome that was only ever
 * interesting for the few seconds after an escalation landed. The connection
 * state now lives as a dot in the nav, and this appears only when there is
 * something to announce, then steps out of the way.
 *
 * The socket itself belongs to StaffGuard: one subscription for the console
 * rather than one per component that wants to know about it.
 */
export function TicketLiveBanner({ latest }: { latest: LiveEscalation | null }) {
  // Dismissal is per escalation id, so the next arrival shows itself without
  // needing an effect to clear this.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const show = latest && dismissed !== latest.id;

  return (
    <div className="st-live-region" aria-live="polite">
      {show && latest ? (
        <div className="st-live">
          <span className="st-live-pulse" aria-hidden="true" />
          <a href={`/admin/tickets?ticket=${encodeURIComponent(latest.id)}`}>
            <span className={`ops-chip ops-chip-caps ${priorityTone(latest.priority)}`}>
              {latest.priority}
            </span>
            New escalation: {latest.reason}
          </a>
          <button
            type="button"
            className="ops-btn is-ghost is-sm st-live-dismiss"
            onClick={() => setDismissed(latest.id)}
          >
            Dismiss
          </button>
        </div>
      ) : null}
    </div>
  );
}

function priorityTone(priority: string): string {
  if (priority === "urgent") return "is-danger";
  if (priority === "high") return "is-warn";
  return "";
}
