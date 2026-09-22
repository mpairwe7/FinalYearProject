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
import { analyticsApi } from "../../services/analyticsApi";
import { TICKET_MACROS } from "../../lib/ticketMacros";
import {
  officerHandle,
  STATUS_LABEL,
  STATUSES,
  ticketLocaleFlag,
  ticketLocaleLabel,
  ticketRef,
} from "../../lib/ticketUi";
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
  const [previewText, setPreviewText] = useState("");
  const [previewBackend, setPreviewBackend] = useState("");
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [figuresSurvived, setFiguresSurvived] = useState<boolean | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const handle = officerHandle(who);
  const mine = handle && (ticket.assignee || "").toLowerCase() === handle.toLowerCase();
  const locked = Boolean(ticket.assignee && handle && !mine);
  const others = (ticket.viewers || []).filter(
    (viewer) => viewer.toLowerCase() !== handle.toLowerCase(),
  );
  const isVernacular = Boolean(ticket.locale && ticket.locale !== "en");

  const generatePreview = async () => {
    if (!reply.trim() || !isVernacular) return;
    setIsPreviewing(true);
    setShowPreview(true);
    try {
      const res = await analyticsApi.translate(reply.trim(), "en", ticket.locale || "lg");
      if (res && res.text) {
        setPreviewText(res.text);
        setPreviewBackend(res.backend || "");
        setFiguresSurvived(res.figures_survived !== false);
      }
    } catch {
      setPreviewText("");
      setFiguresSurvived(null);
    } finally {
      setIsPreviewing(false);
    }
  };

  const sendReply = (resolve: boolean) => {
    const patch: TicketPatch = {};
    if (reply.trim()) {
      patch.officer_reply = reply.trim();
      if (previewText.trim()) {
        patch.officer_reply_localized = previewText.trim();
      }
    }
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
    setPreviewText("");
    setShowPreview(false);
    setFiguresSurvived(null);
  };

  return (
    <div className="st-composer">
      {others.length > 0 ? (
        <p className="st-collision" role="status">
          <span className="st-collision-dot" aria-hidden="true" />
          Also viewing: {others.join(", ")}
        </p>
      ) : null}
      {locked ? (
        <p className="st-collision is-locked" role="alert">
          Assigned to {ticket.assignee}. Assign to me before you reply so two
          officers do not write to the same taxpayer.
        </p>
      ) : null}
      <div className="st-macros" role="group" aria-label="Canned replies">
        <span className="st-macros-label">Insert</span>
        <button
          type="button"
          className="st-chip is-ref-chip"
          onClick={() => {
            const refTag = `[Case Reference: #${ticketRef(ticket.id)}]`;
            setReply((prev) => (prev.trim() ? `${prev.trim()}\n\n${refTag}` : refTag));
          }}
        >
          #{ticketRef(ticket.id)}
        </button>
        <button
          type="button"
          className="st-chip"
          onClick={() => {
            const hotline = "For immediate phone follow-up, call URA toll-free 0800 117 000 / 0800 217 000.";
            setReply((prev) => (prev.trim() ? `${prev.trim()}\n\n${hotline}` : hotline));
          }}
        >
          📞 Hotline
        </button>
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
      {/* Two fields, never one. One reaches the taxpayer and one does not, so
          the difference is stated on the label rather than left to be
          remembered — and shown, by the edge each field carries. */}
      <label className="st-field is-outbound">
        <div className="st-field-header-row">
          <span className="st-field-label">
            Reply to the taxpayer (English)
            <em>
              {isVernacular
                ? `Author in English — will be translated to ${ticketLocaleLabel(ticket.locale)} for the taxpayer.`
                : "They see this on their next turn, exactly as written."}
            </em>
          </span>
          {isVernacular ? (
            <button
              type="button"
              className="ops-btn is-ghost is-xs st-preview-btn"
              disabled={!reply.trim() || isPreviewing}
              onClick={generatePreview}
              title={`Preview translation into ${ticketLocaleLabel(ticket.locale)}`}
            >
              {isPreviewing ? "Translating…" : `🌐 Preview ${ticketLocaleFlag(ticket.locale)} ${ticketLocaleLabel(ticket.locale)}`}
            </button>
          ) : null}
        </div>
        <textarea
          className="ops-textarea"
          data-ticket-reply="1"
          rows={4}
          value={reply}
          placeholder="Answer the question they actually asked in English…"
          onChange={(event) => {
            setReply(event.target.value);
            if (showPreview) {
              setFiguresSurvived(null);
            }
          }}
          aria-label="Reply to the taxpayer"
        />
      </label>

      {showPreview && isVernacular ? (
        <div className="st-translation-preview-panel" role="region" aria-label="Translation Preview">
          <div className="st-preview-header">
            <span className="st-preview-title">
              {ticketLocaleFlag(ticket.locale)} Taxpayer Delivery Preview ({ticketLocaleLabel(ticket.locale)})
            </span>
            {previewBackend ? (
              <span className="st-preview-backend">Engine: {previewBackend}</span>
            ) : null}
            <button
              type="button"
              className="ops-btn is-ghost is-xs st-preview-close"
              onClick={() => setShowPreview(false)}
            >
              ✕ Hide
            </button>
          </div>
          {isPreviewing ? (
            <p className="st-preview-loading">Generating translation in {ticketLocaleLabel(ticket.locale)}…</p>
          ) : previewText ? (
            <>
              <p className="st-preview-content">{previewText}</p>
              {figuresSurvived === false ? (
                <div className="st-fidelity-alert" role="alert">
                  ⚠️ <strong>Figure Check:</strong> Numbers, percentages, or tax amounts may have shifted during translation. Please verify before sending.
                </div>
              ) : figuresSurvived === true ? (
                <div className="st-fidelity-ok">
                  ✓ Figures & statutory amounts verified
                </div>
              ) : null}
            </>
          ) : (
            <p className="st-preview-empty">Could not generate live preview. Reply will be localized on delivery.</p>
          )}
        </div>
      ) : null}

      <label className="st-field is-internal">
        <span className="st-field-label">
          Internal note
          <em>Staff only. Never shown to the taxpayer.</em>
        </span>
        <textarea
          className="ops-textarea"
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
          className="ops-btn is-primary"
          disabled={!reply.trim() || pending || locked}
          onClick={() => sendReply(false)}
        >
          {pending ? "Saving…" : "Send reply"}
        </button>
        <button
          type="button"
          className="ops-btn"
          disabled={pending || locked || (!reply.trim() && !note.trim())}
          onClick={() => sendReply(true)}
        >
          Send and resolve
        </button>
        <button
          type="button"
          className="ops-btn"
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
            className="ops-btn"
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
        <div className="st-sent-block">
          <p className="st-sent">
            Sent (EN): “{ticket.officer_reply}”
            {ticket.reply_delivered_at ? " · seen by the taxpayer" : " · not yet seen"}
          </p>
          {ticket.officer_reply_localized && ticket.officer_reply_localized !== ticket.officer_reply ? (
            <p className="st-sent-localized">
              Delivered ({ticketLocaleLabel(ticket.locale)}): “{ticket.officer_reply_localized}”
            </p>
          ) : null}
        </div>
      ) : null}
      {ticket.staff_note ? <p className="st-note">{ticket.staff_note}</p> : null}

      <div className="st-status">
        <span className="st-field-label">Status</span>
        <div className="ops-segmented st-stepper" role="group" aria-label="Ticket status">
          {STATUSES.map((status) => (
            <button
              key={status}
              type="button"
              className={ticket.status === status ? "is-active" : undefined}
              disabled={ticket.status === status || pending}
              aria-pressed={ticket.status === status}
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
