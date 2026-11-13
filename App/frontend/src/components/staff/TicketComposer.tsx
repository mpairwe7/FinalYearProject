"use client";

/**
 * Reply pair + claim/resolve actions, and ending a live chat.
 *
 * On a text case the reply is a message in a live chat. Ending the chat —
 * "End chat", "Send & end chat", or resolving/closing from the status control,
 * which the server treats the same — tells the taxpayer, asks them to rate the
 * chat and hands them back to the assistant, so it asks for a confirmation.
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
import {
  AlertTriangleIcon,
  CheckIcon,
  CloseIcon,
  GlobeIcon,
  PhoneIcon,
} from "../ops/icons";
import { useEndChat } from "../../hooks/useAnalyticsDashboard";
import { TICKET_MACROS, unfilledPlaceholders } from "../../lib/ticketMacros";
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
  // G635: the English the preview was made from. A preview goes with a reply
  // only if it is of exactly that text and its figure check passed; an edit
  // drops it. The server checks figures again either way.
  const [previewFor, setPreviewFor] = useState("");
  const [previewUnavailable, setPreviewUnavailable] = useState(false);
  const handle = officerHandle(who);
  const mine = handle && (ticket.assignee || "").toLowerCase() === handle.toLowerCase();
  const locked = Boolean(ticket.assignee && handle && !mine);
  const others = (ticket.viewers || []).filter(
    (viewer) => viewer.toLowerCase() !== handle.toLowerCase(),
  );
  const isVernacular = Boolean(ticket.locale && ticket.locale !== "en");
  const endChat = useEndChat();
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [outcome, setOutcome] = useState<"resolved" | "wontfix">("resolved");
  const isChat = ticket.modality !== "voice";
  const chatLive = isChat && ticket.chat?.state !== "ended" && ticket.status !== "resolved" && ticket.status !== "wontfix";

  const clearPreview = () => {
    setPreviewText("");
    setPreviewBackend("");
    setPreviewFor("");
    setFiguresSurvived(null);
    setPreviewUnavailable(false);
  };

  const generatePreview = async () => {
    if (!reply.trim() || !isVernacular) return;
    const source = reply.trim();
    clearPreview();
    setIsPreviewing(true);
    setShowPreview(true);
    try {
      const res = await analyticsApi.translate(source, "en", ticket.locale || "lg");
      // No translation came back: the server returns the English with an
      // error and backend "unavailable". That is not a translation, and its
      // figures "surviving" proves nothing (G635).
      if (!res || !res.text || res.error || res.backend === "unavailable") {
        setPreviewUnavailable(true);
        return;
      }
      setPreviewText(res.text);
      setPreviewBackend(res.backend || "");
      setPreviewFor(source);
      setFiguresSurvived(res.figures_survived !== false);
    } catch {
      setPreviewUnavailable(true);
    } finally {
      setIsPreviewing(false);
    }
  };

  // G636: a template's [[parts]] are what the officer found; none may reach
  // the taxpayer unwritten.
  const toFill = unfilledPlaceholders(reply);
  const blocked = toFill.length > 0;

  /** The preview, if it may go with this exact reply. */
  const sendablePreview = () =>
    previewText.trim() && previewFor === reply.trim() && figuresSurvived === true ? previewText.trim() : "";

  const sendReply = (resolve: boolean) => {
    if (unfilledPlaceholders(reply).length) return;
    const patch: TicketPatch = {};
    if (reply.trim()) {
      patch.officer_reply = reply.trim();
      const localized = sendablePreview();
      if (localized) patch.officer_reply_localized = localized;
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
    clearPreview();
    setShowPreview(false);
  };

  const confirmEndChat = () => {
    endChat.mutate(
      {
        id: ticket.id,
        body: {
          outcome,
          closing_message: reply.trim() || undefined,
          closing_message_localized: reply.trim() ? sendablePreview() || undefined : undefined,
        },
      },
      {
        onSuccess: () => {
          setConfirmEnd(false);
          setReply("");
          clearPreview();
          setShowPreview(false);
        },
      },
    );
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
          <PhoneIcon /> Hotline
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
      {/* A div, not a label: the Preview button sits in this field, and a
          button inside a label takes the label's text as its accessible name.
          The textarea names itself (aria-label). */}
      <div className="st-field is-outbound">
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
              <GlobeIcon />
              {isPreviewing
                ? "Translating…"
                : `Preview ${ticketLocaleFlag(ticket.locale)} ${ticketLocaleLabel(ticket.locale)}`}
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
            // G635: a preview of other words is not this reply's translation.
            if (previewText || figuresSurvived !== null || previewUnavailable) clearPreview();
          }}
          aria-label="Reply to the taxpayer"
        />
      </div>

      {showPreview && isVernacular ? (
        <div className="st-translation-preview-panel" role="region" aria-label="Translation Preview">
          <div className="st-preview-header">
            <span className="st-preview-title">
              <GlobeIcon /> Taxpayer Delivery Preview ({ticketLocaleLabel(ticket.locale)})
            </span>
            {previewBackend ? (
              <span className="st-preview-backend">Engine: {previewBackend}</span>
            ) : null}
            <button
              type="button"
              className="ops-btn is-ghost is-xs st-preview-close"
              onClick={() => setShowPreview(false)}
            >
              <CloseIcon /> Hide
            </button>
          </div>
          {isPreviewing ? (
            <p className="st-preview-loading">Generating translation in {ticketLocaleLabel(ticket.locale)}…</p>
          ) : previewText ? (
            <>
              <p className="st-preview-content">{previewText}</p>
              {figuresSurvived === false ? (
                <div className="st-fidelity-alert" role="alert">
                  <AlertTriangleIcon />{" "}
                  <strong>Figure Check:</strong> Numbers, percentages, or tax amounts may have shifted during translation. Please verify before sending.
                </div>
              ) : figuresSurvived === true ? (
                <div className="st-fidelity-ok">
                  <CheckIcon /> Figure check passed (automatic): the same numbers appear in both texts
                </div>
              ) : null}
            </>
          ) : previewUnavailable ? (
            <p className="st-preview-empty" role="status">
              Translation is unavailable right now. When you send, the server tries again; if it still
              cannot, the taxpayer receives your English.
            </p>
          ) : (
            <p className="st-preview-empty">Edit made: preview again to see the translation of this text.</p>
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

      {blocked ? (
        <p className="st-save-err" role="status">
          Fill in or delete the parts in [[double brackets]] before sending: {toFill.join("; ")}.
        </p>
      ) : null}
      <div className="st-actions">
        <button
          type="button"
          className="ops-btn is-primary"
          disabled={!reply.trim() || pending || locked || blocked}
          onClick={() => sendReply(false)}
        >
          {pending ? "Saving…" : "Send reply"}
        </button>
        <button
          type="button"
          className="ops-btn"
          disabled={pending || locked || blocked || (!reply.trim() && !note.trim())}
          onClick={() => sendReply(true)}
        >
          {chatLive ? "Send & end chat" : "Send and resolve"}
        </button>
        {chatLive ? (
          <button
            type="button"
            className="ops-btn"
            disabled={pending || locked || endChat.isPending}
            aria-expanded={confirmEnd}
            aria-controls={`st-end-chat-${ticket.id}`}
            onClick={() => setConfirmEnd((open) => !open)}
          >
            End chat
          </button>
        ) : null}
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

      {confirmEnd && chatLive ? (
        <div className="st-end-chat" id={`st-end-chat-${ticket.id}`} role="group" aria-label="End the chat">
          <p className="st-end-chat-lead">
            End this chat? The taxpayer is told it has ended, asked to rate it, and handed back to the
            assistant.
            {reply.trim() ? " Your reply above is sent as the closing message." : " Write a closing message above if you want to say goodbye."}
          </p>
          <fieldset className="st-end-chat-outcome">
            <legend>How did it end?</legend>
            <label>
              <input
                type="radio"
                name={`st-outcome-${ticket.id}`}
                value="resolved"
                checked={outcome === "resolved"}
                onChange={() => setOutcome("resolved")}
              />
              Resolved
            </label>
            <label>
              <input
                type="radio"
                name={`st-outcome-${ticket.id}`}
                value="wontfix"
                checked={outcome === "wontfix"}
                onChange={() => setOutcome("wontfix")}
              />
              Closed without resolution
            </label>
          </fieldset>
          <div className="st-actions">
            <button type="button" className="ops-btn is-primary" disabled={endChat.isPending || blocked} onClick={confirmEndChat}>
              {endChat.isPending ? "Ending…" : "End chat"}
            </button>
            <button type="button" className="ops-btn" onClick={() => setConfirmEnd(false)} disabled={endChat.isPending}>
              Keep chatting
            </button>
            {endChat.isError ? (
              <span className="st-save-err" role="alert">
                Could not end the chat — try again.
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

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
        <span className="st-field-label">
          Status
          {chatLive ? <em>Resolving or closing a live chat ends it for the taxpayer.</em> : null}
        </span>
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
