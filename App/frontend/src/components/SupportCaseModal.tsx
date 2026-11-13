"use client";

import React, { useCallback, useEffect, useState } from "react";
import { analyticsApi, type EscalationCaseDetail } from "../services/analyticsApi";
import { BuildingIcon, CheckCircleIcon, CloseIcon, CopyIcon, LoadingDots, PhoneIcon } from "./Icons";
import { useTranslation } from "../lib/i18n";
import "./supportCaseModal.css";

interface SupportCaseModalProps {
  isOpen: boolean;
  onClose: () => void;
  ticketId: string | null;
  conversationId?: string | null;
  /** An officer is chatting with the taxpayer in the main conversation (or
   *  just ended it there), so this room is the case's details, not a second
   *  place to write to them. */
  liveChat?: boolean;
}

export function SupportCaseModal({
  isOpen,
  onClose,
  ticketId,
  conversationId: _conversationId,
  liveChat = false,
}: SupportCaseModalProps) {
  const t = useTranslation();
  const [caseData, setCaseData] = useState<EscalationCaseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [sendingReply, setSendingReply] = useState(false);
  const [replySuccess, setReplySuccess] = useState(false);

  // Fetch ticket details
  const fetchCase = useCallback(async () => {
    if (!ticketId) return;
    try {
      const data = await analyticsApi.publicTicketStatus(ticketId);
      setCaseData(data);
      setError(null);
    } catch {
      setError(t("case.loadFailed"));
    }
  }, [ticketId, t]);

  useEffect(() => {
    if (!isOpen || !ticketId) return;
    setLoading(true);
    void fetchCase().finally(() => setLoading(false));

    // Poll every 5s for live officer reply
    const timer = window.setInterval(fetchCase, 5000);
    return () => window.clearInterval(timer);
  }, [isOpen, ticketId, fetchCase]);

  // Escape key closes modal
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const copyRef = useCallback(() => {
    if (!caseData?.reference && !ticketId) return;
    const ref = caseData?.reference || `TIC-${ticketId?.slice(0, 8).toUpperCase()}`;
    void navigator.clipboard.writeText(ref);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [caseData, ticketId]);

  const handleSendReply = async () => {
    if (!ticketId || !replyText.trim() || sendingReply) return;
    setSendingReply(true);
    setReplySuccess(false);
    try {
      const res = await analyticsApi.replyToPublicTicket(ticketId, replyText.trim());
      if (res.ok) {
        setReplyText("");
        setReplySuccess(true);
        void fetchCase();
        setTimeout(() => setReplySuccess(false), 3000);
      }
    } catch {
      setError(t("case.sendFailed"));
    } finally {
      setSendingReply(false);
    }
  };

  const [showEnOriginal, setShowEnOriginal] = useState(false);

  if (!isOpen || !ticketId) return null;

  const refCode = caseData?.reference || `TIC-${ticketId.slice(0, 8).toUpperCase()}`;
  const status = caseData?.status || "open";
  const hasOfficerReplied = Boolean(caseData?.officer_reply);
  const isResolved = status === "resolved";
  // G628: "won't fix" is a closed case, not one still in the queue.
  const isClosed = status === "wontfix";
  const hasLocalizedReply = Boolean(
    caseData?.officer_reply_localized && caseData.officer_reply_localized !== caseData.officer_reply,
  );
  const displayedReply = hasLocalizedReply && !showEnOriginal
    ? caseData?.officer_reply_localized
    : caseData?.officer_reply;

  // Compute active step index for visual stepper
  let stepIndex = 0;
  if (status === "open") stepIndex = 1;
  else if (status === "assigned" && !hasOfficerReplied) stepIndex = 2;
  else if (hasOfficerReplied && !isResolved) stepIndex = 3;
  else if (isResolved || isClosed) stepIndex = 4;

  return (
    <div className="scm-overlay" role="dialog" aria-modal="true" aria-labelledby="scm-title">
      <div className="scm-backdrop" onClick={onClose} aria-hidden="true" />
      <div className="scm-card">
        {/* Sticky Header */}
        <header className="scm-head">
          <div className="scm-title-col">
            <div className="scm-badge-row">
              <span className="scm-tag">Official Case Room</span>
              <button
                type="button"
                className={`scm-ref-btn${copied ? " is-copied" : ""}`}
                onClick={copyRef}
                title="Click to copy case reference"
              >
                <code>#{refCode}</code>
                <span>{copied ? "Copied!" : "Copy"}</span>
              </button>
            </div>
            <h2 id="scm-title" className="scm-title">
              {caseData?.reason || "URA Officer Escalation"}
            </h2>
          </div>
          <button
            type="button"
            className="scm-close-btn"
            onClick={onClose}
            aria-label="Close support case room"
          >
            <CloseIcon />
          </button>
        </header>

        <div className="scm-body">
          {loading && !caseData ? (
            <div className="scm-waiting-card">
              <div className="scm-waiting-spinner"><LoadingDots /></div>
              <div className="scm-waiting-content"><p>Connecting to support case...</p></div>
            </div>
          ) : null}
          {/* Quick Action Strip */}
          <div className="scm-action-bar">
            <a
              href="tel:0800117000"
              className="scm-action-btn is-hotline"
              title="Call URA toll-free helpline"
            >
              <PhoneIcon size={14} /> 0800 117 000
            </a>
            <button
              type="button"
              className="scm-action-btn"
              onClick={copyRef}
              title="Copy reference code to quote over phone or email"
            >
              <CopyIcon /> Quote #{refCode}
            </button>
            {caseData?.team_label && (
              <span className="scm-team-pill" title="Assigned Department">
                <BuildingIcon /> {caseData.team_label}
              </span>
            )}
          </div>

          {/* Stepper */}
          <div className="scm-stepper" aria-label="Case Progress">
            <div className={`scm-step${stepIndex >= 1 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 1 ? "✓" : "1"}</div>
              <div className="scm-step-label">{t("case.stepLogged")}</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 2 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 2 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 2 ? "✓" : "2"}</div>
              <div className="scm-step-label">{t("case.stepAssigned")}</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 3 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 3 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 3 ? "✓" : "3"}</div>
              <div className="scm-step-label">{t("case.stepInReview")}</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 4 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 4 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex >= 4 ? "✓" : "4"}</div>
              <div className="scm-step-label">{isClosed ? t("case.stepClosed") : t("case.stepResolved")}</div>
            </div>
          </div>

          {/* Error notice */}
          {error && <div className="scm-error-alert">{error}</div>}

          {/* Officer Response Section */}
          {hasOfficerReplied ? (
            <div className="scm-officer-card">
              <div className="scm-officer-header">
                <div className="scm-officer-avatar">URA</div>
                <div className="scm-officer-meta">
                  <div className="scm-officer-name">
                    <span>{caseData?.assignee_display || "Official URA Tax Officer"}</span>
                    <span className="scm-verified-badge" title="Verified URA Staff">
                      <CheckCircleIcon /> Verified
                    </span>
                  </div>
                  {caseData?.reply_at ? (
                    <span className="scm-officer-time">
                      Replied {new Date(caseData.reply_at * 1000).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="scm-officer-text">{displayedReply}</div>
              {hasLocalizedReply ? (
                <div className="scm-translation-toggle-bar">
                  <span className="scm-translation-note">
                    {showEnOriginal
                      ? "Viewing original English response from officer"
                      : `Translated to ${caseData?.locale ? caseData.locale.toUpperCase() : "your language"} for your convenience`}
                  </span>
                  <button
                    type="button"
                    className="scm-trans-btn"
                    onClick={() => setShowEnOriginal(!showEnOriginal)}
                  >
                    {showEnOriginal ? "View translated response" : "View original in English"}
                  </button>
                </div>
              ) : null}
            </div>
          ) : caseData ? (
            // G628: what the case's status says, not a promise of review.
            <div className="scm-waiting-card" role="status">
              {!isClosed && !isResolved && (
                <div className="scm-waiting-spinner">
                  <LoadingDots />
                </div>
              )}
              <div className="scm-waiting-content">
                <h4>
                  {isClosed || isResolved
                    ? t("case.titleClosed")
                    : status === "open"
                      ? t("case.titleOpen")
                      : t("case.titleAssigned")}
                </h4>
                <p>
                  {isClosed
                    ? t("case.closed")
                    : isResolved
                      ? t("case.resolved")
                    : status === "open"
                      ? t("case.waitingOpen")
                      : t("case.waitingAssigned", { team: caseData.team_label || caseData.team || "URA" })}
                </p>
                <p className="scm-waiting-sub">{t("case.reference", { ref: refCode })}</p>
              </div>
            </div>
          ) : null}

          {/* Two-Way Taxpayer Follow-up Composer */}
          {liveChat ? (
            <p className="scm-composer-label" role="note">
              {t("officerChat.inMainChat")}
            </p>
          ) : caseData?.can_reply !== false && (
            <div className="scm-composer-section">
              <label className="scm-composer-label" htmlFor="scm-reply-input">
                Reply to the Officer / Add Information
                <em>Include any document reference numbers, PRNs, or clarifications.</em>
              </label>
              <textarea
                id="scm-reply-input"
                className="scm-textarea"
                rows={3}
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                placeholder="Type your message or PRN here to send to the officer..."
                disabled={sendingReply}
              />
              <div className="scm-composer-actions">
                <button
                  type="button"
                  className="scm-send-btn"
                  onClick={handleSendReply}
                  disabled={!replyText.trim() || sendingReply}
                >
                  {sendingReply ? <LoadingDots /> : "Send to Officer"}
                </button>
                {replySuccess && (
                  <span className="scm-reply-ok">✓ Message sent to officer</span>
                )}
              </div>
            </div>
          )}

          {/* Prior Conversation Transcript Summary */}
          {caseData?.transcript && caseData.transcript.length > 0 && (
            <details className="scm-transcript-details">
              <summary className="scm-transcript-summary">
                View Case Conversation History ({caseData.transcript.length} turns)
              </summary>
              <div className="scm-transcript-list">
                {caseData.transcript.map((t, idx) => (
                  <div className="scm-transcript-turn" key={idx}>
                    {t.user_message && (
                      <div className="scm-turn-user">
                        <strong>Taxpayer:</strong> {t.user_message}
                      </div>
                    )}
                    {t.bot_reply && (
                      <div className="scm-turn-bot">
                        <strong>URA Assistant:</strong> {t.bot_reply}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
