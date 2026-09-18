"use client";

import React, { useCallback, useEffect, useState } from "react";
import { analyticsApi, type EscalationCaseDetail } from "../services/analyticsApi";
import { CloseIcon, LoadingDots, CheckCircleIcon } from "./Icons";
import "./supportCaseModal.css";

interface SupportCaseModalProps {
  isOpen: boolean;
  onClose: () => void;
  ticketId: string | null;
  conversationId?: string | null;
}

export function SupportCaseModal({
  isOpen,
  onClose,
  ticketId,
  conversationId: _conversationId,
}: SupportCaseModalProps) {
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
      setError("Unable to load support case details. Please try again.");
    }
  }, [ticketId]);

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
      setError("Failed to send message to the officer. Please try again.");
    } finally {
      setSendingReply(false);
    }
  };

  if (!isOpen || !ticketId) return null;

  const refCode = caseData?.reference || `TIC-${ticketId.slice(0, 8).toUpperCase()}`;
  const status = caseData?.status || "open";
  const hasOfficerReplied = Boolean(caseData?.officer_reply);
  const isResolved = status === "resolved";

  // Compute active step index for visual stepper
  let stepIndex = 0;
  if (status === "open") stepIndex = 1;
  else if (status === "assigned" && !hasOfficerReplied) stepIndex = 2;
  else if (hasOfficerReplied && !isResolved) stepIndex = 3;
  else if (isResolved) stepIndex = 4;

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
              📞 0800 117 000
            </a>
            <button
              type="button"
              className="scm-action-btn"
              onClick={copyRef}
              title="Copy reference code to quote over phone or email"
            >
              📋 Quote #{refCode}
            </button>
            {caseData?.team_label && (
              <span className="scm-team-pill" title="Assigned Department">
                🏢 {caseData.team_label}
              </span>
            )}
          </div>

          {/* Stepper */}
          <div className="scm-stepper" aria-label="Case Progress">
            <div className={`scm-step${stepIndex >= 1 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 1 ? "✓" : "1"}</div>
              <div className="scm-step-label">Logged</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 2 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 2 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 2 ? "✓" : "2"}</div>
              <div className="scm-step-label">Assigned</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 3 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 3 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex > 3 ? "✓" : "3"}</div>
              <div className="scm-step-label">In Review</div>
            </div>
            <div className={`scm-step-line${stepIndex >= 4 ? " is-active" : ""}`} />
            <div className={`scm-step${stepIndex >= 4 ? " is-active" : ""}`}>
              <div className="scm-step-dot">{stepIndex >= 4 ? "✓" : "4"}</div>
              <div className="scm-step-label">Resolved</div>
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
              <div className="scm-officer-text">{caseData?.officer_reply}</div>
            </div>
          ) : (
            <div className="scm-waiting-card">
              <div className="scm-waiting-spinner">
                <LoadingDots />
              </div>
              <div className="scm-waiting-content">
                <h4>A URA Officer is Reviewing Your Case</h4>
                <p>
                  Your inquiry has been assigned to the{" "}
                  <strong>{caseData?.team_label || "support team"}</strong>. An officer is
                  reviewing the official ledger and documents. Their verified response will appear
                  directly here.
                </p>
                <p className="scm-waiting-sub">
                  You can keep this window open or return anytime — your reference code is{" "}
                  <code>#{refCode}</code>.
                </p>
              </div>
            </div>
          )}

          {/* Two-Way Taxpayer Follow-up Composer */}
          {caseData?.can_reply !== false && (
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
