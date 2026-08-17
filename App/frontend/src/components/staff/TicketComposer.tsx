"use client";

/**
 * Reply pair + claim/resolve actions.
 *
 * `officer_reply` and `staff_note` stay separate all the way to the
 * submit call: one reaches the taxpayer and one does not. The parent
 * remounts this with `key={ticketId}` so a half-typed reply cannot
 * land on someone else's case.
 */
import React, { useState } from "react";
import type { TicketDetail } from "../../services/analyticsApi";
import type { TicketPatch } from "../../services/analyticsApi";
import { TICKET_MACROS } from "../../lib/ticketMacros";
import { officerHandle, STATUS_LABEL, STATUSES } from "../../lib/ticketUi";
import type { StaffIdentity } from "../StaffGuard";
import "./staffTickets.css";

export function TicketComposer({
  ticket,
  who,
  pending,
  isError,
  isSuccess,
  onPatch,
}: {
  ticket: TicketDetail;
  who?: StaffIdentity;
  pending: boolean;
  isError: boolean;
  isSuccess: boolean;
  onPatch: (patch: TicketPatch) => void;
}) {
  const [reply, setReply] = useState("");
  const [note, setNote] = useState("");
  const handle = officerHandle(who);
  const mine = handle && (ticket.assignee || "").toLowerCase() === handle.toLowerCase();
  const locked = Boolean(ticket.assignee && handle && !mine);
  const others = (ticket.viewers || []).filter(
    (viewer) => viewer.toLowerCase() !== handle.toLowerCase(),
  );

  const sendReply = (resolve: boolean) => {
    const patch: TicketPatch = {};
    if (reply.trim()) patch.officer_reply = reply.trim();
    if (note.trim()) patch.staff_note = note.trim();
    if (resolve) patch.status = "resolved";
    if (!handle) {
      /* keep going — reply does not require a claim */
    } else if (!ticket.assignee && !resolve) {
      patch.assignee = handle;
      if (ticket.status === "open") patch.status = "assigned";
    }
    if (!Object.keys(patch).length) return;
    onPatch(patch);
    setReply("");
    setNote("");
  };

  return (
    <div className="st-composer">
      {others.length > 0 ? (
        <p className="st-collision" role="status">
          Also viewing: {others.join(", ")}
        </p>
      ) : null}
      {locked ? (
        <p className="st-collision" role="alert">
          Assigned to {ticket.assignee}. Assign to me before you reply so two
          officers do not write to the same taxpayer.
        </p>
      ) : null}
      <div className="st-macros" role="group" aria-label="Canned replies">
        {TICKET_MACROS.map((macro) => (
          <button
            key={macro.id}
            type="button"
            className="st-chip"
            onClick={() => setReply((prev) => (prev.trim() ? `${prev.trim()}\n\n${macro.body}` : macro.body))}
          >
            {macro.label}
          </button>
        ))}
      </div>
      <label className="st-field">
        <span className="st-field-label">
          Reply to the taxpayer
          <em>They see this on their next turn, exactly as written.</em>
        </span>
        <textarea
          data-ticket-reply="1"
          rows={4}
          value={reply}
          placeholder="Answer the question they actually asked…"
          onChange={(event) => setReply(event.target.value)}
          aria-label="Reply to the taxpayer"
        />
      </label>

      <label className="st-field">
        <span className="st-field-label">
          Internal note
          <em>Staff only. Never shown to the taxpayer.</em>
        </span>
        <textarea
          rows={2}
          value={note}
          placeholder="Context for the next officer…"
          onChange={(event) => setNote(event.target.value)}
          aria-label="Internal note"
        />
      </label>

      <div className="st-actions">
        <button
          type="button"
          className="st-btn st-btn-primary"
          disabled={!reply.trim() || pending || locked}
          onClick={() => sendReply(false)}
        >
          {pending ? "Saving…" : "Send reply"}
        </button>
        <button
          type="button"
          className="st-btn"
          disabled={pending || locked || (!reply.trim() && !note.trim())}
          onClick={() => sendReply(true)}
        >
          Send and resolve
        </button>
        <button
          type="button"
          className="st-btn"
          disabled={!note.trim() || pending}
          onClick={() => {
            onPatch({ staff_note: note.trim() });
            setNote("");
          }}
        >
          Save note
        </button>
        {handle ? (
          <button
            type="button"
            className="st-btn"
            disabled={pending || Boolean(mine)}
            onClick={() =>
              onPatch({
                assignee: handle,
                status: ticket.status === "open" ? "assigned" : ticket.status,
              })
            }
          >
            {mine ? "Assigned to you" : "Assign to me"}
          </button>
        ) : null}
        {isError ? (
          <span className="st-save-err" role="alert">
            Could not save — try again.
          </span>
        ) : null}
        {isSuccess ? (
          <span className="st-save-ok" role="status">
            Saved.
          </span>
        ) : null}
      </div>

      {ticket.officer_reply ? (
        <p className="st-sent">
          Sent: “{ticket.officer_reply}”
          {ticket.reply_delivered_at ? " · seen by the taxpayer" : " · not yet seen"}
        </p>
      ) : null}
      {ticket.staff_note ? <p className="st-note">{ticket.staff_note}</p> : null}

      <div>
        <span className="st-field-label">Status</span>
        <div className="st-stepper" role="group" aria-label="Ticket status">
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={`st-btn${ticket.status === status ? " is-current" : ""}`}
              disabled={ticket.status === status || pending}
              onClick={() => onPatch({ status })}
            >
              {STATUS_LABEL[status]}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
