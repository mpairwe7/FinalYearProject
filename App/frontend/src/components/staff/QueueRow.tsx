"use client";

import React, { useState } from "react";
import type { TicketQueueItem } from "../../services/analyticsApi";
import {
  isRecentTicket,
  ticketLocaleFlag,
  ticketLocaleLabel,
  ticketRef,
  topicLabel,
  waitingFor,
  waitTone,
} from "../../lib/ticketUi";
import "./staffTickets.css";

/**
 * One case in the queue with clear visual hierarchy, reference pill, and status alignment.
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
  const [showOriginal, setShowOriginal] = useState(false);
  const tone = waitTone(ticket.created_at, ticket.first_response_at, ticket.reply_at);
  const waitClass = tone === "ok" ? "" : ` is-${tone}`;
  const ref = ticketRef(ticket.id);
  const isRecent = isRecentTicket(ticket.created_at);
  const isVernacular = Boolean(
    ticket.locale && ticket.locale !== "en" && ticket.user_query_en && ticket.user_query_en !== ticket.user_query,
  );
  const isVoice = ticket.modality === "voice";

  return (
    <div
      role="button"
      tabIndex={0}
      className={`st-row${selected ? " is-selected" : ""}${isRecent ? " is-recent-escalation" : ""}`}
      onClick={() => onSelect(ticket.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(ticket.id);
        }
      }}
      aria-pressed={selected}
    >
      <div className="st-row-badge-col">
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span className={`st-pri st-pri-${ticket.priority}`}>{ticket.priority}</span>
          {isRecent ? <span className="st-new-badge">✨ NEW</span> : null}
        </div>
        <button
          type="button"
          className="st-ref-pill"
          title={`Click to copy reference ${ref}`}
          onClick={(e) => {
            e.stopPropagation();
            void navigator.clipboard.writeText(ref);
          }}
        >
          {ref}
        </button>
      </div>

      <div className="st-row-body">
        <div className="st-row-topic-line">
          <span className="st-row-topic">{ticket.reason || topicLabel(ticket)}</span>
          {ticket.locale && ticket.locale !== "en" ? (
            <span
              className="st-row-lang-pill"
              title={`Taxpayer language: ${ticketLocaleLabel(ticket.locale)} (transcribed/translated to English)`}
            >
              {ticketLocaleFlag(ticket.locale)} {ticketLocaleLabel(ticket.locale)} → EN
            </span>
          ) : null}
          {isVoice ? (
            <span className="st-row-voice-pill" title="Transcribed from taxpayer voice note">
              🎙️ Voice
            </span>
          ) : null}
        </div>

        <div className="st-row-query-row">
          <span className="st-row-query">
            {isVernacular && !showOriginal ? (
              <>
                <span className="st-query-trans-tag">[EN]</span> {ticket.user_query_en}
              </>
            ) : (
              ticket.user_query
            )}
          </span>
          {isVernacular ? (
            <button
              type="button"
              className="st-row-trans-toggle"
              title={showOriginal ? "Switch to English translation" : "View original vernacular query"}
              onClick={(e) => {
                e.stopPropagation();
                setShowOriginal((v) => !v);
              }}
            >
              {showOriginal ? "🇬🇧 EN" : `${ticketLocaleFlag(ticket.locale)} Orig`}
            </button>
          ) : null}
        </div>

        <div className="st-row-meta">
          {ticket.team ? <span className="st-row-team">🏢 {ticket.team.replace(/_/g, " ")}</span> : null}
          {ticket.assignee ? (
            <span className="st-row-assignee">👤 {ticket.assignee.split("@")[0]}</span>
          ) : (
            <span className="st-row-unclaimed">⚠️ Unassigned</span>
          )}
          {ticket.status !== "open" ? (
            <span className="st-row-status">{ticket.status}</span>
          ) : null}
        </div>
      </div>

      <span className={`st-row-wait${waitClass}`} title="waiting since it was raised">
        {waitingFor(ticket.created_at)}
      </span>
    </div>
  );
}
