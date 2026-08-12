"use client";

/**
 * Tax agent queue — the working view for an officer, not an administrator.
 *
 * `/admin/tickets` is the full console: every status, every filter, a table to
 * audit from. This page answers a narrower question — what should I pick up next,
 * and what do I need to know before I reply — so it shows one ticket at a time
 * with its handoff brief, and puts the reply box next to the transcript instead
 * of behind a row expansion.
 *
 * Reuses the existing ticket hooks and endpoints; no new backend surface. Two
 * things are carried over deliberately from the console, because both are
 * load-bearing rather than stylistic:
 *
 * - The transcript is shown in full and unedited. The point of escalation is that
 *   the taxpayer does not explain themselves twice.
 * - `officer_reply` and `staff_note` stay separate inputs with separate labels.
 *   One reaches the taxpayer and one does not; merging them is a privacy incident
 *   waiting to happen.
 *
 * Standards: ISO/IEC 25010:2023 §4 (Interaction Capability), WCAG 2.2 AA.
 */
import React, { useEffect, useMemo, useState } from "react";
import StaffGuard, { type StaffIdentity } from "../../components/StaffGuard";
import {
  useTicket,
  useTicketQueueFull,
  useTicketSla,
  useUpdateTicket,
} from "../../hooks/useAnalyticsDashboard";
import type { TicketQueueItem } from "../../services/analyticsApi";
import "./agent.css";

const PRIORITY_RANK: Record<string, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

function waitingFor(createdAt: number): string {
  const mins = Math.max(0, Math.round(Date.now() / 1000 - createdAt) / 60);
  const m = Math.round(mins);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

function turnTime(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

function QueueRow({
  ticket,
  selected,
  onSelect,
}: {
  ticket: TicketQueueItem;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        className={`ag-row${selected ? " selected" : ""}`}
        onClick={onSelect}
        aria-current={selected ? "true" : undefined}
      >
        <span className={`ag-pri ag-pri-${ticket.priority}`}>{ticket.priority}</span>
        <span className="ag-row-body">
          <span className="ag-row-topic">{ticket.handoff?.topic || ticket.reason || "Escalation"}</span>
          <span className="ag-row-query">{ticket.user_query}</span>
        </span>
        <span className="ag-row-wait">{waitingFor(ticket.created_at)}</span>
      </button>
    </li>
  );
}

function AgentQueue({ who }: { who: StaffIdentity }) {
  const [status, setStatus] = useState("open");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: queue, isLoading, error } = useTicketQueueFull(status, "", "", 50);
  const { data: detail } = useTicket(selectedId);
  const { data: sla } = useTicketSla(30);
  const update = useUpdateTicket();

  const [officerReply, setOfficerReply] = useState("");
  const [staffNote, setStaffNote] = useState("");

  const tickets = useMemo(() => {
    const rows = [...(queue?.tickets ?? [])];
    // Urgent first, then longest-waiting: the backend already orders this way,
    // but the queue is also filtered client-side so keep the invariant explicit.
    rows.sort(
      (a, b) =>
        (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9) ||
        a.created_at - b.created_at,
    );
    return rows;
  }, [queue]);

  // Land on the top of the queue rather than an empty pane.
  useEffect(() => {
    if (!selectedId && tickets.length > 0) setSelectedId(tickets[0].id);
  }, [tickets, selectedId]);

  // Switching ticket must not carry a half-typed reply onto someone else's case.
  useEffect(() => {
    setOfficerReply("");
    setStaffNote("");
  }, [selectedId]);

  const urgent = tickets.filter((t) => t.priority === "urgent").length;
  const awaiting = sla?.awaiting_first_response ?? 0;

  const submit = (nextStatus?: string) => {
    if (!selectedId) return;
    const patch: Record<string, string> = {};
    if (officerReply.trim()) patch.officer_reply = officerReply.trim();
    if (staffNote.trim()) patch.staff_note = staffNote.trim();
    if (nextStatus) patch.status = nextStatus;
    if (!Object.keys(patch).length) return;
    update.mutate({ id: selectedId, patch });
    setOfficerReply("");
    setStaffNote("");
  };

  return (
    <main className="ag-page">
      <header className="ag-head">
        <div>
          <h1>My queue</h1>
          <p className="ag-sub">
            Signed in as {who.email || who.external_id} — urgent first, then longest waiting
          </p>
        </div>
        <div className="ag-counts">
          <span className="ag-count">
            <strong>{tickets.length}</strong> {status}
          </span>
          {urgent > 0 && (
            <span className="ag-count urgent">
              <strong>{urgent}</strong> urgent
            </span>
          )}
          {awaiting > 0 && (
            <span className="ag-count warn">
              <strong>{awaiting}</strong> awaiting first reply
            </span>
          )}
        </div>
      </header>

      <div className="ag-status-tabs" role="tablist" aria-label="Queue status">
        {["open", "assigned", "resolved"].map((s) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={status === s}
            className={status === s ? "active" : ""}
            onClick={() => {
              setStatus(s);
              setSelectedId(null);
            }}
          >
            {s}
          </button>
        ))}
      </div>

      <div className="ag-split">
        <section className="ag-queue-pane" aria-label="Queue">
          {isLoading && <p className="ag-empty">Loading the queue…</p>}
          {error && <p className="ag-empty ag-error">Could not load the queue.</p>}
          {!isLoading && !error && tickets.length === 0 && (
            <p className="ag-empty">Nothing {status}. </p>
          )}
          <ul className="ag-queue">
            {tickets.map((t) => (
              <QueueRow
                key={t.id}
                ticket={t}
                selected={t.id === selectedId}
                onSelect={() => setSelectedId(t.id)}
              />
            ))}
          </ul>
        </section>

        <section className="ag-detail" aria-label="Ticket detail">
          {!selectedId && <p className="ag-empty">Pick a ticket to see the brief.</p>}
          {selectedId && !detail && <p className="ag-empty">Loading the ticket…</p>}
          {detail && (
            <>
              <div className="ag-detail-head">
                <span className={`ag-pri ag-pri-${detail.priority}`}>{detail.priority}</span>
                <h2>{detail.handoff?.topic || detail.reason || "Escalation"}</h2>
                <span className="ag-detail-wait">waiting {waitingFor(detail.created_at)}</span>
              </div>

              {detail.handoff && (
                <div
                  className={`ag-brief${detail.handoff.transfer_style === "warm" ? " warm" : ""}`}
                >
                  <div className="ag-brief-top">
                    <span className="ag-brief-label">Handoff brief</span>
                    {detail.handoff.transfer_style && (
                      <span className="ag-chip">{detail.handoff.transfer_style} transfer</span>
                    )}
                    {detail.handoff.sentiment && (
                      <span className="ag-chip">felt {detail.handoff.sentiment}</span>
                    )}
                  </div>
                  {detail.handoff.summary && <p className="ag-brief-summary">{detail.handoff.summary}</p>}
                  {detail.handoff.opening_guidance && (
                    <p className="ag-brief-open">
                      <strong>Open with:</strong> {detail.handoff.opening_guidance}
                    </p>
                  )}
                  {detail.handoff.required_details?.length ? (
                    <div className="ag-brief-need">
                      <strong>Have ready:</strong>
                      <ul>
                        {detail.handoff.required_details.map((d) => (
                          <li key={d}>{d}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </div>
              )}

              <div className="ag-transcript" aria-label="Conversation as it stood at escalation">
                <span className="ag-section-label">
                  Conversation at escalation
                  {detail.handoff?.turns_before_handoff
                    ? ` — ${detail.handoff.turns_before_handoff} turns`
                    : ""}
                </span>
                {detail.transcript?.length ? (
                  detail.transcript.map((turn, i) => (
                    <div className="ag-turn" key={`${turn.created_at}-${i}`}>
                      <p className="ag-turn-user">
                        <span className="ag-turn-who">Taxpayer</span>
                        {turn.user_message}
                      </p>
                      <p className="ag-turn-bot">
                        <span className="ag-turn-who">Assistant</span>
                        {turn.bot_reply}
                      </p>
                      <span className="ag-turn-meta">{turnTime(turn.created_at)}</span>
                    </div>
                  ))
                ) : (
                  <div className="ag-turn">
                    <p className="ag-turn-user">
                      <span className="ag-turn-who">Taxpayer</span>
                      {detail.user_query}
                    </p>
                    <p className="ag-turn-bot">
                      <span className="ag-turn-who">Assistant</span>
                      {detail.bot_reply}
                    </p>
                  </div>
                )}
              </div>

              <div className="ag-reply">
                <label className="ag-field">
                  <span className="ag-field-label">
                    Reply to the taxpayer
                    <em>They see this on their next turn.</em>
                  </span>
                  <textarea
                    value={officerReply}
                    onChange={(e) => setOfficerReply(e.target.value)}
                    rows={4}
                    placeholder="Answer the question they actually asked…"
                  />
                </label>

                <label className="ag-field">
                  <span className="ag-field-label">
                    Internal note
                    <em>Never shown to the taxpayer.</em>
                  </span>
                  <textarea
                    value={staffNote}
                    onChange={(e) => setStaffNote(e.target.value)}
                    rows={2}
                    placeholder="Context for the next officer…"
                  />
                </label>

                <div className="ag-actions">
                  <button
                    type="button"
                    className="ag-primary"
                    onClick={() => submit()}
                    disabled={update.isPending || (!officerReply.trim() && !staffNote.trim())}
                  >
                    {update.isPending ? "Saving…" : "Send reply"}
                  </button>
                  <button
                    type="button"
                    className="ag-secondary"
                    onClick={() => submit("resolved")}
                    disabled={update.isPending}
                  >
                    Send and resolve
                  </button>
                  {update.isError && <span className="ag-save-err">Could not save — try again.</span>}
                  {update.isSuccess && <span className="ag-save-ok">Saved.</span>}
                </div>
              </div>
            </>
          )}
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
