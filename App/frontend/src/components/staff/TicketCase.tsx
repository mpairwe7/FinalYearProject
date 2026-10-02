"use client";

import React, { useEffect, useState } from "react";
import type { TicketDetail, TicketPatch } from "../../services/analyticsApi";
import {
  officerHandle,
  STATUS_LABEL,
  ticketLocaleFlag,
  ticketLocaleLabel,
  ticketRef,
  topicLabel,
  waitingFor,
  waitTone,
} from "../../lib/ticketUi";
import { analyticsApi } from "../../services/analyticsApi";
import { Skeleton } from "../ops/States";
import type { StaffIdentity } from "../StaffGuard";
import { TicketComposer } from "./TicketComposer";
import "./staffTickets.css";

function PresenceBeat({ ticketId, viewer }: { ticketId: string; viewer: string }) {
  useEffect(() => {
    if (!ticketId || !viewer) return;
    const beat = () => {
      void analyticsApi.heartbeatPresence(ticketId).catch(() => undefined);
    };
    beat();
    const timer = window.setInterval(beat, 20_000);
    return () => window.clearInterval(timer);
  }, [ticketId, viewer]);
  return null;
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

/**
 * One case, everything the officer needs before replying.
 *
 * The header is sticky. On a case with a long transcript, "Back to queue", the
 * status and the priority were the first things to scroll away, which is
 * exactly backwards: they are the controls, and the transcript is the reading.
 *
 * The transcript is still shown in full and unedited, and the two reply fields
 * are still two fields — both are load-bearing rather than stylistic, and both
 * are covered by tests that would notice.
 */
export function TicketCase({
  ticket,
  loading,
  error,
  who,
  pending,
  isError,
  isSuccess,
  onPatch,
  onBack,
}: {
  ticket?: TicketDetail;
  loading?: boolean;
  error?: boolean;
  who?: StaffIdentity;
  pending: boolean;
  isError: boolean;
  isSuccess: boolean;
  onPatch: (patch: TicketPatch) => void;
  onBack?: () => void;
}) {
  const [transMode, setTransMode] = useState<"both" | "en" | "orig">("both");
  const handoff = ticket?.handoff ?? {};
  const transcript = ticket?.transcript ?? [];
  const warm = handoff.transfer_style === "warm";
  const canAct = who?.role !== "ura_auditor";
  const tone = ticket ? waitTone(ticket.created_at, ticket.first_response_at, ticket.reply_at) : "ok";
  const handle = officerHandle(who);
  const isVernacular = Boolean(ticket?.locale && ticket.locale !== "en");

  if (loading) {
    return (
      <div className="st-case-loading" aria-busy="true">
        <Skeleton width="55%" height={18} />
        <Skeleton width="30%" height={14} />
        <Skeleton height={90} radius="var(--ops-radius-sm)" />
        <Skeleton height={160} radius="var(--ops-radius-sm)" />
        <span className="ops-sr-only">Loading the case…</span>
      </div>
    );
  }
  if (error || !ticket) return <p className="st-empty">Could not load this ticket.</p>;

  return (
    <article className="st-case">
      {canAct && handle ? <PresenceBeat ticketId={ticket.id} viewer={handle} /> : null}
      <header className="st-case-head">
        <div className="st-case-headline">
          {onBack ? (
            <button type="button" className="ops-btn is-ghost is-sm st-back" onClick={onBack}>
              ← Back to queue
            </button>
          ) : null}
          <h2>{ticket.reason || topicLabel(ticket)}</h2>
          <div className="st-case-ref-bar">
            <span className="st-case-ref-badge" title="Official URA Reference Code">
              #{ticketRef(ticket.id)}
            </span>
            <button
              type="button"
              className="ops-btn is-ghost is-xs"
              onClick={() => {
                void navigator.clipboard.writeText(ticketRef(ticket.id));
              }}
              title="Copy reference code"
            >
              📋 Copy Ref
            </button>
            <button
              type="button"
              className="ops-btn is-ghost is-xs"
              onClick={() => {
                if (typeof window !== "undefined") {
                  const url = `${window.location.origin}/admin/tickets?ticket=${encodeURIComponent(ticket.id)}`;
                  void navigator.clipboard.writeText(url);
                }
              }}
              title="Copy deep link to this ticket"
            >
              🔗 Copy Link
            </button>
            {ticket.team ? (
              <span className="st-pill st-pill-team">
                🏢 {ticket.team.replace(/_/g, " ")}
              </span>
            ) : null}
            {ticket.reply_delivered_at ? (
              <span className="st-pill is-delivered" title="Taxpayer received officer reply">
                ✓ Delivered to Taxpayer
              </span>
            ) : ticket.officer_reply ? (
              <span className="st-pill is-pending-delivery" title="Reply queued for taxpayer delivery">
                ⏳ Queued for Delivery
              </span>
            ) : null}
          </div>
          <p className="st-case-pills">
            <span className={`st-pri st-pri-${ticket.priority}`}>{ticket.priority}</span>
            <span className="st-pill">{STATUS_LABEL[ticket.status] || ticket.status}</span>
            {ticket.locale && ticket.locale !== "en" ? (
              <span className="st-pill st-pill-lang" title={`Taxpayer language is ${ticketLocaleLabel(ticket.locale)}`}>
                {ticketLocaleFlag(ticket.locale)} {ticketLocaleLabel(ticket.locale)}
              </span>
            ) : null}
            {ticket.modality === "voice" ? (
              <span className="st-pill st-pill-voice" title="Transcribed from taxpayer voice note">
                🎙️ Voice Note
              </span>
            ) : null}
            {handoff.sentiment && handoff.sentiment !== "neutral" ? (
              <span className="st-pill st-pill-sentiment">{handoff.sentiment}</span>
            ) : null}
            {warm ? (
              <span className="st-pill st-pill-warm" title="Brief yourself before making contact">
                warm transfer
              </span>
            ) : null}
            {ticket.assignee ? <span className="st-pill">{ticket.assignee}</span> : null}
          </p>
        </div>
        <span className={`st-case-wait st-row-wait${tone === "ok" ? "" : ` is-${tone}`}`}>
          waiting {waitingFor(ticket.created_at)}
        </span>
      </header>

      {ticket.user_query_en && ticket.user_query_en !== ticket.user_query ? (
        <section className="st-query-translation-banner" aria-label="Query Translation">
          <span className="st-brief-label">🇬🇧 English Translation of Citizen Query</span>
          <p className="st-query-translation-text">{ticket.user_query_en}</p>
          <span className="st-query-translation-orig">
            Original ({ticketLocaleLabel(ticket.locale)}): “{ticket.user_query}”
          </span>
        </section>
      ) : null}

      {handoff.summary || handoff.opening_guidance || handoff.required_details?.length ? (
        <section className="st-brief">
          <span className="st-brief-label">Handoff brief</span>
          {handoff.summary ? <p>{handoff.summary}</p> : null}
          {warm && handoff.opening_guidance ? (
            <p>
              <strong>Before you open:</strong> {handoff.opening_guidance}
            </p>
          ) : null}
          {handoff.required_details?.length ? (
            <>
              <strong>Have ready</strong>
              <ul>
                {handoff.required_details.map((detail) => (
                  <li key={detail}>{detail}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ) : null}

      <section aria-label="Conversation as it stood at escalation">
        <div className="st-conversation-head">
          <span className="st-brief-label">
            Conversation
            <span>
              {" "}
              · {transcript.length} turn{transcript.length === 1 ? "" : "s"}, as it stood when the
              ticket was raised
            </span>
          </span>
          {isVernacular ? (
            <div className="st-trans-mode-switch" role="group" aria-label="Transcript language mode">
              <button
                type="button"
                className={`st-trans-btn${transMode === "both" ? " is-active" : ""}`}
                onClick={() => setTransMode("both")}
              >
                Dual View
              </button>
              <button
                type="button"
                className={`st-trans-btn${transMode === "en" ? " is-active" : ""}`}
                onClick={() => setTransMode("en")}
              >
                English Only
              </button>
              <button
                type="button"
                className={`st-trans-btn${transMode === "orig" ? " is-active" : ""}`}
                onClick={() => setTransMode("orig")}
              >
                Original ({ticket.locale?.toUpperCase()})
              </button>
            </div>
          ) : null}
        </div>
        {transcript.length === 0 ? (
          <p className="st-empty">No transcript was captured for this ticket.</p>
        ) : (
          <ol className="st-transcript">
            {transcript.map((turn, index) => {
              const hasEn = Boolean(turn.user_message_en && turn.user_message_en !== turn.user_message);
              return (
                <li className="st-turn" key={`${turn.created_at}-${index}`}>
                  {hasEn && transMode === "both" ? (
                    <div className="st-turn-bilingual-block">
                      <p className="st-turn-orig">
                        <span className="st-turn-who">Taxpayer ({ticketLocaleLabel(ticket.locale)})</span>
                        {turn.user_message}
                      </p>
                      <p className="st-turn-trans">
                        <span className="st-turn-who">🌐 English Translation</span>
                        {turn.user_message_en}
                      </p>
                    </div>
                  ) : (
                    <p>
                      <span className="st-turn-who">
                        Taxpayer {hasEn && transMode === "en" ? "[EN]" : ""}
                      </span>
                      {hasEn && transMode === "en" ? turn.user_message_en : turn.user_message}
                    </p>
                  )}
                  <p className="st-turn-bot">
                    <span className="st-turn-who">Assistant</span>
                    {turn.bot_reply}
                  </p>
                  {turn.created_at ? (
                    <span className="st-turn-meta">{turnTime(turn.created_at)}</span>
                  ) : null}
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {canAct ? (
        <TicketComposer
          key={ticket.id}
          ticket={ticket}
          who={who}
          pending={pending}
          isError={isError}
          isSuccess={isSuccess}
          onPatch={onPatch}
        />
      ) : (
        <p className="st-readonly">
          Auditor view — replies and assignment stay with tax agents and administrators.
        </p>
      )}
    </article>
  );
}
