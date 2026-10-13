"use client";

/**
 * The live chat between the taxpayer and the officer on a case, in order.
 *
 * Read by the officer answering it and by the administrator auditing it, so
 * it shows what each side actually saw: an officer's message in English and,
 * for a case in another language, as delivered. The end of the chat is a
 * marked line with who ended it, how, and how the taxpayer rated it.
 *
 * Cases from before the thread existed have only the taxpayer's follow-ups in
 * the transcript (with an empty assistant reply) and the officer's latest
 * reply; those are shown in the same shape, so no case reads as empty.
 */
import React from "react";
import type { TicketChatMessage, TicketDetail } from "../../services/analyticsApi";
import { ticketLocaleLabel } from "../../lib/ticketUi";
import { SATISFACTION_FACES, SATISFACTION_LABELS_EN } from "../rating/SatisfactionScale";

function when(ts: number): string {
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

/** The thread, or the legacy follow-ups shaped like one. */
export function chatMessagesOf(ticket: TicketDetail): TicketChatMessage[] {
  if (ticket.messages?.length) return ticket.messages;
  const legacy: TicketChatMessage[] = (ticket.transcript ?? [])
    .filter((turn) => turn.sender === "taxpayer")
    .map((turn, index) => ({
      seq: index + 1,
      sender: "taxpayer" as const,
      text: turn.user_message,
      created_at: turn.created_at,
    }));
  if (ticket.officer_reply) {
    legacy.push({
      seq: legacy.length + 1,
      sender: "officer",
      text: ticket.officer_reply,
      text_localized: ticket.officer_reply_localized,
      created_at: ticket.reply_at || ticket.created_at,
    });
  }
  return legacy.sort((a, b) => a.created_at - b.created_at);
}

/** "Rated 2/5 · Dissatisfied", or what happened instead. */
export function chatRatingText(rating: NonNullable<TicketDetail["chat"]>["rating"]): string {
  if (!rating) return "";
  if (rating.status === "answered" && rating.score != null) {
    return `Taxpayer rated the chat ${rating.score}/5 · ${SATISFACTION_LABELS_EN[rating.score - 1]}`;
  }
  if (rating.status === "skipped") return "Taxpayer skipped the rating";
  if (rating.status === "pending") return "Rating asked for — no answer yet";
  return "No rating";
}

export function TicketChatThread({ ticket }: { ticket: TicketDetail }) {
  const messages = chatMessagesOf(ticket);
  const chat = ticket.chat;
  const vernacular = Boolean(ticket.locale && ticket.locale !== "en");
  const rating = chat?.rating;

  return (
    <section className="st-chat" aria-label="Chat with the taxpayer">
      <div className="st-conversation-head">
        <span className="st-brief-label">
          Chat with the taxpayer
          <span>
            {" "}
            ·{" "}
            {chat?.state === "officer"
              ? `live — ${chat.officer_name || "an officer"} has the chat, the assistant is paused`
              : chat?.state === "ended"
                ? "ended"
                : chat?.state === "waiting"
                  ? "waiting for an officer — the assistant is still answering"
                  : `${messages.length} message${messages.length === 1 ? "" : "s"}`}
          </span>
        </span>
      </div>
      {messages.length === 0 ? (
        <p className="st-empty">No messages yet. Take the case and reply to start the chat.</p>
      ) : (
        <ol className="st-chat-thread">
          {messages.map((message) => {
            if (message.sender === "system") {
              return (
                <li key={message.seq} className="st-chat-end">
                  <span>
                    Chat ended
                    {chat?.ended_by_name ? ` by ${chat.ended_by_name}` : ""}
                    {chat?.outcome === "wontfix" ? " · closed without resolution" : chat?.outcome === "resolved" ? " · resolved" : ""}
                    {message.created_at ? ` · ${when(message.created_at)}` : ""}
                  </span>
                </li>
              );
            }
            const fromOfficer = message.sender === "officer";
            const localized =
              fromOfficer && vernacular && message.text_localized && message.text_localized !== message.text
                ? message.text_localized
                : "";
            return (
              <li key={message.seq} className={`st-chat-msg ${fromOfficer ? "is-officer" : "is-taxpayer"}`}>
                <span className="st-turn-who">
                  {fromOfficer ? message.officer_name || "Officer" : "Taxpayer"}
                </span>
                <p>{message.text}</p>
                {localized ? (
                  <p className="st-chat-localized">
                    <span className="st-turn-who">Delivered ({ticketLocaleLabel(ticket.locale)})</span>
                    {localized}
                  </p>
                ) : null}
                {message.created_at ? <span className="st-turn-meta">{when(message.created_at)}</span> : null}
              </li>
            );
          })}
        </ol>
      )}
      {rating ? (
        <p className={`st-chat-rating${rating.score != null && rating.score <= 2 ? " is-low" : ""}`}>
          {rating.score != null ? (
            <span aria-hidden="true" className="st-chat-rating-face">
              {SATISFACTION_FACES[rating.score - 1]}
            </span>
          ) : null}
          {chatRatingText(rating)}
          {rating.comment ? <span className="st-chat-rating-comment">“{rating.comment}”</span> : null}
        </p>
      ) : null}
    </section>
  );
}
