"use client";

/**
 * Staff ticket queue — Phase 18.
 *
 * The escalation backend already carries everything an officer needs:
 * the whole conversation as it stood when the ticket was raised, the
 * taxpayer's sentiment at that moment, whether to open warm or cold, and
 * what to have ready. Until now none of it was reachable except as JSON.
 *
 * Two decisions worth keeping:
 *
 * - The transcript is shown in full and unedited. The point of the
 *   feature is that the taxpayer does not explain themselves twice, and
 *   a UI that summarises it would put the problem straight back.
 * - `officer_reply` and `staff_note` are separate inputs with separate
 *   labels, because one reaches the taxpayer and one does not. Merging
 *   them into a single "notes" box would be a privacy incident waiting
 *   to happen.
 */
import React, { useState } from "react";
import {
  useTicket,
  useTicketQueueFull,
  useTicketSla,
  useUpdateTicket,
} from "../../../hooks/useAnalyticsDashboard";
import type { TicketQueueItem } from "../../../services/analyticsApi";
import "./tickets.css";

const STATUSES = ["open", "assigned", "resolved", "wontfix"] as const;
const PRIORITIES = ["urgent", "high", "normal", "low"] as const;

function waitingFor(createdAt: number): string {
  const seconds = Math.max(0, Date.now() / 1000 - createdAt);
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function TicketRow({
  ticket,
  selected,
  onSelect,
}: {
  ticket: TicketQueueItem;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const handoff = ticket.handoff ?? {};
  return (
    <button
      type="button"
      className={`ticket-row ${selected ? "is-selected" : ""}`}
      onClick={() => onSelect(ticket.id)}
      aria-current={selected}
    >
      <span className={`pill pill-${ticket.priority}`}>{ticket.priority}</span>
      <span className="ticket-row-main">
        <span className="ticket-row-reason">{ticket.reason || "escalation"}</span>
        <span className="ticket-row-meta">
          {ticket.team ? <span>{ticket.team.replace(/_/g, " ")}</span> : null}
          {handoff.topic ? <span>{handoff.topic.replace(/_/g, " ")}</span> : null}
          {ticket.status !== "open" ? <span>{ticket.status}</span> : null}
        </span>
      </span>
      <span className="ticket-row-age" title="waiting since it was raised">
        {waitingFor(ticket.created_at)}
      </span>
    </button>
  );
}

function TicketDetailPanel({ ticketId }: { ticketId: string }) {
  const { data: ticket, isLoading, error } = useTicket(ticketId);
  const update = useUpdateTicket();
  const [reply, setReply] = useState("");
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState("");

  if (isLoading) return <p className="ticket-empty">Loading ticket…</p>;
  if (error || !ticket) return <p className="ticket-empty">Could not load this ticket.</p>;

  const handoff = ticket.handoff ?? {};
  const transcript = ticket.transcript ?? [];
  const warm = handoff.transfer_style === "warm";

  const apply = (patch: Record<string, string>) => {
    update.mutate({ id: ticket.id, patch });
  };

  return (
    <article className="ticket-detail">
      <header className="ticket-detail-head">
        <div>
          <h2>{ticket.reason || "Escalation"}</h2>
          <p className="ticket-detail-sub">
            <span className={`pill pill-${ticket.priority}`}>{ticket.priority}</span>
            <span className="pill pill-status">{ticket.status}</span>
            {handoff.sentiment && handoff.sentiment !== "neutral" ? (
              <span className="pill pill-sentiment">{handoff.sentiment}</span>
            ) : null}
            {warm ? (
              <span className="pill pill-warm" title="Brief yourself before making contact">
                warm transfer
              </span>
            ) : null}
          </p>
        </div>
      </header>

      {handoff.summary ? <p className="ticket-summary">{handoff.summary}</p> : null}

      {warm && handoff.opening_guidance ? (
        <p className="ticket-guidance">
          <strong>Before you open:</strong> {handoff.opening_guidance}
        </p>
      ) : null}

      {handoff.required_details?.length ? (
        <section className="ticket-block">
          <h3>Have ready</h3>
          <ul>
            {handoff.required_details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="ticket-block">
        <h3>
          Conversation
          <span className="ticket-block-hint">
            {transcript.length} turn{transcript.length === 1 ? "" : "s"}, as it stood when the
            ticket was raised
          </span>
        </h3>
        {transcript.length === 0 ? (
          <p className="ticket-empty">No transcript was captured for this ticket.</p>
        ) : (
          <ol className="transcript">
            {transcript.map((turn, index) => (
              <li key={`${turn.created_at}-${index}`}>
                <p className="turn turn-user">
                  <span className="turn-who">Taxpayer</span>
                  {turn.user_message}
                </p>
                <p className="turn turn-bot">
                  <span className="turn-who">Assistant</span>
                  {turn.bot_reply}
                </p>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="ticket-block">
        <h3>Reply to the taxpayer</h3>
        <p className="ticket-block-hint">
          Delivered in their chat the next time they open it. They will see this exactly as
          written.
        </p>
        <textarea
          className="ticket-input"
          rows={4}
          value={reply}
          placeholder="e.g. Your TIN was reactivated on 4 August. No further action is needed."
          onChange={(event) => setReply(event.target.value)}
          aria-label="Reply to the taxpayer"
        />
        <button
          type="button"
          className="btn btn-primary"
          disabled={!reply.trim() || update.isPending}
          onClick={() => {
            apply({ officer_reply: reply.trim() });
            setReply("");
          }}
        >
          Send reply
        </button>
        {ticket.officer_reply ? (
          <p className="ticket-sent">
            Sent: “{ticket.officer_reply}”
            {ticket.reply_delivered_at ? " · seen by the taxpayer" : " · not yet seen"}
          </p>
        ) : null}
      </section>

      <section className="ticket-block">
        <h3>Internal note</h3>
        <p className="ticket-block-hint">Staff only. Never shown to the taxpayer.</p>
        <textarea
          className="ticket-input"
          rows={2}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          aria-label="Internal note"
        />
        <button
          type="button"
          className="btn"
          disabled={!note.trim() || update.isPending}
          onClick={() => {
            apply({ staff_note: note.trim() });
            setNote("");
          }}
        >
          Save note
        </button>
        {ticket.staff_note ? <p className="ticket-note">{ticket.staff_note}</p> : null}
      </section>

      <section className="ticket-block ticket-actions">
        <div>
          <label htmlFor="assignee">Assign to</label>
          <input
            id="assignee"
            className="ticket-input"
            value={assignee}
            placeholder={ticket.assignee || "officer name"}
            onChange={(event) => setAssignee(event.target.value)}
          />
          <button
            type="button"
            className="btn"
            disabled={!assignee.trim() || update.isPending}
            onClick={() => {
              apply({ assignee: assignee.trim() });
              setAssignee("");
            }}
          >
            Assign
          </button>
        </div>
        <div>
          <span className="ticket-actions-label">Status</span>
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={`btn ${ticket.status === status ? "is-current" : ""}`}
              disabled={ticket.status === status || update.isPending}
              onClick={() => apply({ status })}
            >
              {status}
            </button>
          ))}
        </div>
      </section>
    </article>
  );
}

export default function StaffTicketQueue() {
  const [status, setStatus] = useState<string>("open");
  const [priority, setPriority] = useState<string>("");
  const [team, setTeam] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);
  const { data: queue, isLoading, error } = useTicketQueueFull(status, priority, team);
  const { data: sla } = useTicketSla(30);

  const tickets = queue?.tickets ?? [];
  // Team names come from the server so the UI does not hardcode an org chart.
  const teams = queue?.teams ?? [];

  return (
    <main className="tickets-page">
      <header className="tickets-header">
        <div>
          <h1>Escalation queue</h1>
          <p className="tickets-subtitle">
            Urgent first, then longest waiting. Every ticket carries the conversation that
            produced it.
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
          </dl>
        ) : null}
      </header>

      <div className="tickets-filters">
        {STATUSES.map((value) => (
          <button
            key={value}
            type="button"
            className={`filter ${status === value ? "is-active" : ""}`}
            onClick={() => setStatus(value)}
          >
            {value}
          </button>
        ))}
        <span className="filter-sep" />
        <button
          type="button"
          className={`filter ${priority === "" ? "is-active" : ""}`}
          onClick={() => setPriority("")}
        >
          all priorities
        </button>
        {PRIORITIES.map((value) => (
          <button
            key={value}
            type="button"
            className={`filter ${priority === value ? "is-active" : ""}`}
            onClick={() => setPriority(value)}
          >
            {value}
          </button>
        ))}
        {teams.length > 0 ? (
          <>
            <span className="filter-sep" />
            <button
              type="button"
              className={`filter ${team === "" ? "is-active" : ""}`}
              onClick={() => setTeam("")}
            >
              all teams
            </button>
            {teams.map((value) => (
              <button
                key={value}
                type="button"
                className={`filter ${team === value ? "is-active" : ""}`}
                onClick={() => setTeam(value)}
              >
                {value.replace(/_/g, " ")}
              </button>
            ))}
          </>
        ) : null}
      </div>

      <div className="tickets-body">
        <section className="tickets-list" aria-label="Ticket queue">
          {isLoading ? <p className="ticket-empty">Loading queue…</p> : null}
          {error ? <p className="ticket-empty">Could not load the queue.</p> : null}
          {!isLoading && !error && tickets.length === 0 ? (
            <p className="ticket-empty">Nothing waiting.</p>
          ) : null}
          {tickets.map((ticket) => (
            <TicketRow
              key={ticket.id}
              ticket={ticket}
              selected={selected === ticket.id}
              onSelect={setSelected}
            />
          ))}
        </section>

        <section className="tickets-detail" aria-label="Ticket detail">
          {selected ? (
            <TicketDetailPanel ticketId={selected} />
          ) : (
            <p className="ticket-empty">Select a ticket to see the conversation.</p>
          )}
        </section>
      </div>
    </main>
  );
}
