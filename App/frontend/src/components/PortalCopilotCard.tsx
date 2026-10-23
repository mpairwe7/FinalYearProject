"use client";

import Link from "next/link";
import React, { useCallback, useState } from "react";
import { authHeaders, getAuthToken } from "../lib/authSession";
import { getChatSessionId } from "../lib/chatSession";
import { useTranslation } from "../lib/i18n";
import { savePendingPortalContinuation } from "../lib/portalContinuation";
import type { PortalCopilotPayload } from "../store/useChatStore";
import {
  AlertTriangleIcon,
  ArrowRightIcon,
  BanIcon,
  CheckCircleIcon,
  CheckIcon,
  CompassIcon,
  CopyIcon,
  ExternalLinkIcon,
  EyeIcon,
  EyeOffIcon,
  LockIcon,
  PencilIcon,
  ShieldCheckIcon,
} from "./Icons";

export interface PortalCopilotCardProps {
  copilot: PortalCopilotPayload;
  conversationId: string | null;
  onSubmitInlineForm?: (taskId: string, fields: Record<string, string>) => void;
  onActionClick?: (action: string) => void;
}

export default function PortalCopilotCard({
  copilot,
  conversationId,
  onSubmitInlineForm,
  onActionClick,
}: PortalCopilotCardProps) {
  const t = useTranslation();
  const [editing, setEditing] = useState(false);
  const [copiedRef, setCopiedRef] = useState(false);
  const [revealedFields, setRevealedFields] = useState<Record<string, boolean>>({});
  const [formValues, setFormValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const f of copilot.fields ?? []) {
      init[f.name] = f.value ?? "";
    }
    return init;
  });
  const [execState, setExecState] = useState<{
    status: "idle" | "busy" | "executed" | "declined" | "error";
    summary?: string;
    result?: Record<string, unknown>;
  }>({ status: "idle" });

  const isSignedIn = Boolean(getAuthToken());
  const effectiveCardType = editing ? "inline_form" : copilot.card_type;

  const toggleReveal = useCallback((fieldName: string) => {
    setRevealedFields((prev) => ({ ...prev, [fieldName]: !prev[fieldName] }));
  }, []);

  const handleCopyRef = useCallback(async (val: string) => {
    try {
      await navigator.clipboard.writeText(val);
      setCopiedRef(true);
      setTimeout(() => setCopiedRef(false), 1600);
    } catch {
      // Clipboard unavailable
    }
  }, []);

  const handleFieldChange = useCallback((name: string, value: string) => {
    setFormValues((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleFormSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const taskId = copilot.task_id || "";
      if (!taskId) return;
      setEditing(false);
      if (onSubmitInlineForm) {
        onSubmitInlineForm(taskId, formValues);
      }
    },
    [copilot.task_id, formValues, onSubmitInlineForm],
  );

  const handleDecision = useCallback(
    async (decision: "allow" | "disallow") => {
      if (!copilot.request_state || !copilot.idempotency_key) return;
      setExecState({ status: "busy" });
      try {
        const res = await fetch("/api/v1/portal/tasks/execute", {
          method: "POST",
          headers: authHeaders({
            "Content-Type": "application/json",
            "X-Session-ID": getChatSessionId(),
          }),
          body: JSON.stringify({
            decision,
            request_state: copilot.request_state,
            idempotency_key: copilot.idempotency_key,
            conversation_id: conversationId || "default",
          }),
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
          setExecState({
            status: "error",
            summary: body.detail || body.summary || `Request failed (${res.status})`,
          });
          return;
        }
        setExecState({
          status: body.status === "declined" ? "declined" : "executed",
          summary: body.summary || (decision === "disallow" ? "Action cancelled." : "Task executed."),
          result: body.result || {},
        });
      } catch (err) {
        setExecState({
          status: "error",
          summary: err instanceof Error ? err.message : "Network error while executing task.",
        });
      }
    },
    [conversationId, copilot.idempotency_key, copilot.request_state],
  );

  const handleStepUpLogin = useCallback(
    (returnMode: "chat" | "portal", href: string) => {
      savePendingPortalContinuation({
        conversationId: conversationId || "default",
        taskId: copilot.task_id || "",
        portalKey: copilot.portal_key || "e_services",
        collectedFields: copilot.raw_preserved_fields || copilot.preserved_fields || formValues,
        returnMode,
      });
      if (typeof window !== "undefined") {
        window.location.assign(href);
      }
    },
    [
      conversationId,
      copilot.portal_key,
      copilot.preserved_fields,
      copilot.raw_preserved_fields,
      copilot.task_id,
      formValues,
    ],
  );

  // ── 1. Inline Chat Input Form Card (`inline_form`) ──────────────────────────
  if (effectiveCardType === "inline_form") {
    return (
      <section
        className="portal-copilot-card portal-inline-form-card"
        data-testid="portal-inline-form-card"
        aria-label={copilot.title || t("portalCopilot.inlineFormBadge")}
      >
        <div className="portal-copilot-header">
          <span className="portal-copilot-badge is-amber">
            <CompassIcon size={13} /> {t("portalCopilot.inlineFormBadge")}
          </span>
          {copilot.statutory_citation && (
            <span className="portal-copilot-citation">{copilot.statutory_citation}</span>
          )}
        </div>
        <h3 className="portal-copilot-title">{copilot.title}</h3>
        <p className="portal-copilot-subtitle">
          {copilot.description || t("portalCopilot.inlineFormSubtitle")}
        </p>

        <form className="portal-inline-form" onSubmit={handleFormSubmit}>
          {(copilot.fields ?? []).map((f) => {
            const inputId = `portal-field-${copilot.task_id || "task"}-${f.name}`;
            const descId = `${inputId}-desc`;
            const currentVal = formValues[f.name] ?? "";
            const hasErr = Boolean(f.validation_error);
            return (
              <div className="portal-form-field" key={f.name}>
                <label htmlFor={inputId} className="portal-form-label">
                  {f.label}
                  {f.required && (
                    <span className="portal-required-star" aria-hidden="true">
                      {" "}
                      *
                    </span>
                  )}
                </label>
                {f.input_type === "select" && f.options?.length ? (
                  <select
                    id={inputId}
                    name={f.name}
                    className="portal-form-input"
                    value={currentVal}
                    required={f.required}
                    aria-invalid={hasErr}
                    aria-describedby={f.validation_error || f.helper_text ? descId : undefined}
                    onChange={(e) => handleFieldChange(f.name, e.target.value)}
                  >
                    <option value="">Select {f.label}…</option>
                    {f.options.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    id={inputId}
                    name={f.name}
                    type={f.input_type || "text"}
                    className="portal-form-input"
                    placeholder={f.placeholder || ""}
                    value={currentVal}
                    required={f.required}
                    aria-invalid={hasErr}
                    aria-describedby={f.validation_error || f.helper_text ? descId : undefined}
                    onChange={(e) => handleFieldChange(f.name, e.target.value)}
                  />
                )}
                {f.validation_error && (
                  <p id={descId} className="portal-field-error" role="alert">
                    <AlertTriangleIcon size={13} /> {f.validation_error}
                  </p>
                )}
                {f.helper_text && !f.validation_error && (
                  <p id={descId} className="portal-field-helper">
                    {f.helper_text}
                  </p>
                )}
              </div>
            );
          })}

          <div className="portal-copilot-actions">
            <button
              type="submit"
              className="portal-action-btn is-primary"
              data-testid="portal-inline-form-submit-btn"
            >
              <ShieldCheckIcon size={15} />{" "}
              {copilot.submit_label || t("portalCopilot.continueToReview")}
            </button>
            {copilot.copilot_portal_url && (
              <Link
                href={copilot.copilot_portal_url}
                className="portal-action-btn is-ghost"
                data-testid="portal-open-copilot-link"
              >
                {t("portalCopilot.openWithCopilot", {
                  portal: copilot.portal_name || "URA Portal",
                })}{" "}
                <ExternalLinkIcon size={13} />
              </Link>
            )}
          </div>
        </form>
      </section>
    );
  }

  // ── 2. Claude-Style Action Confirmation Card (`confirmation_required`) ──────
  if (effectiveCardType === "confirmation_required") {
    if (execState.status === "executed" || execState.status === "declined") {
      const prnVal = execState.result?.prn ? String(execState.result.prn) : "";
      const tinVal = execState.result?.tin ? String(execState.result.tin) : "";
      const refToCopy = prnVal || tinVal;
      return (
        <section
          className={`portal-copilot-card portal-decision-result-card is-${execState.status}`}
          data-testid="portal-task-decision-result"
          role="status"
        >
          <div className="portal-copilot-header">
            <span
              className={`portal-copilot-badge ${
                execState.status === "executed" ? "is-emerald" : "is-neutral"
              }`}
            >
              {execState.status === "executed" ? (
                <>
                  <CheckCircleIcon /> {t("portalCopilot.executedBadge")}
                </>
              ) : (
                <>
                  <BanIcon size={13} /> {t("portalCopilot.declinedBadge")}
                </>
              )}
            </span>
            {copilot.live === false && (
              <span className="portal-copilot-badge is-sim">
                {t("portalCopilot.simulationBadge")}
              </span>
            )}
          </div>
          <h3 className="portal-copilot-title">{copilot.title}</h3>
          <p className="portal-copilot-subtitle">{execState.summary}</p>
          {prnVal && (
            <div className="portal-receipt-kv" data-testid="portal-generated-prn">
              <strong>PRN Reference:</strong> <code>{prnVal}</code>
            </div>
          )}
          {tinVal && (
            <div className="portal-receipt-kv" data-testid="portal-generated-tin">
              <strong>TIN Reference:</strong> <code>{tinVal}</code>
            </div>
          )}
          <div className="portal-copilot-actions">
            {refToCopy && (
              <button
                type="button"
                className="portal-action-btn is-ghost"
                data-testid="portal-copy-receipt-btn"
                onClick={() => void handleCopyRef(refToCopy)}
              >
                {copiedRef ? (
                  <>
                    <CheckIcon /> {t("portalCopilot.copiedReceipt")}
                  </>
                ) : (
                  <>
                    <CopyIcon /> {t("portalCopilot.copyReceipt")}
                  </>
                )}
              </button>
            )}
            {copilot.copilot_portal_url && (
              <Link href={copilot.copilot_portal_url} className="portal-action-btn is-ghost">
                {t("portalCopilot.openWithCopilot", {
                  portal: copilot.portal_name || "URA Portal",
                })}{" "}
                <ExternalLinkIcon size={13} />
              </Link>
            )}
          </div>
        </section>
      );
    }

    return (
      <section
        className="portal-copilot-card portal-confirmation-card"
        data-testid="portal-confirmation-card"
        aria-label={copilot.title || t("portalCopilot.confirmationBadge")}
      >
        <div className="portal-copilot-header">
          <span className="portal-copilot-badge is-amber">
            <ShieldCheckIcon size={13} /> {t("portalCopilot.confirmationBadge")}
          </span>
          {copilot.live === false && (
            <span className="portal-copilot-badge is-sim">
              {t("portalCopilot.simulationBadge")}
            </span>
          )}
        </div>
        <h3 className="portal-copilot-title">{copilot.title}</h3>
        <p className="portal-verification-banner" role="note">
          <AlertTriangleIcon size={15} />{" "}
          <span>{copilot.verification_prompt || t("portalCopilot.verificationPrompt")}</span>
        </p>

        <dl className="portal-verification-table">
          {(copilot.display_fields ?? []).map((df) => {
            const isRevealed = Boolean(revealedFields[df.name]);
            const shownValue = isRevealed && df.raw_value ? df.raw_value : df.value;
            return (
              <div className="portal-verification-row" key={df.name}>
                <dt>{df.label}</dt>
                <dd>
                  <code data-testid={`portal-confirm-value-${df.name}`}>{shownValue}</code>
                  {df.sensitive && df.raw_value && (
                    <button
                      type="button"
                      className="portal-sensitive-toggle"
                      data-testid={`portal-sensitive-toggle-${df.name}`}
                      onClick={() => toggleReveal(df.name)}
                    >
                      {isRevealed ? (
                        <>
                          <EyeOffIcon size={13} /> {t("portalCopilot.hideSensitive")}
                        </>
                      ) : (
                        <>
                          <EyeIcon size={13} /> {t("portalCopilot.revealSensitive")}
                        </>
                      )}
                    </button>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>

        <p className="portal-field-helper" data-testid="portal-reversibility-notice">
          {t("portalCopilot.reversibilityNotice")}
        </p>

        {execState.status === "error" && execState.summary && (
          <p className="portal-field-error" role="alert">
            <AlertTriangleIcon size={14} /> {execState.summary}
          </p>
        )}

        <div className="portal-copilot-actions">
          <button
            type="button"
            className="portal-action-btn is-primary"
            data-testid="portal-task-allow-btn"
            disabled={execState.status === "busy"}
            onClick={() => void handleDecision("allow")}
          >
            <CheckCircleIcon />{" "}
            {execState.status === "busy"
              ? t("portalCopilot.executing")
              : copilot.allow_label || t("portalCopilot.allowBtn")}
          </button>
          <button
            type="button"
            className="portal-action-btn is-ghost"
            data-testid="portal-task-edit-btn"
            disabled={execState.status === "busy"}
            onClick={() => setEditing(true)}
          >
            <PencilIcon /> {copilot.edit_label || t("portalCopilot.editBtn")}
          </button>
          <button
            type="button"
            className="portal-action-btn is-danger"
            data-testid="portal-task-disallow-btn"
            disabled={execState.status === "busy"}
            onClick={() => void handleDecision("disallow")}
          >
            <BanIcon size={14} /> {copilot.disallow_label || t("portalCopilot.disallowBtn")}
          </button>
        </div>
      </section>
    );
  }

  // ── 3. Step-Up Login & Auto-Return Card (`step_up_login`) ───────────────────
  if (effectiveCardType === "step_up_login") {
    const preservedEntries = Object.entries(copilot.preserved_fields ?? {});
    const chatHref =
      copilot.signin_chat_href ||
      `/signin?continue=signin&returnTo=${encodeURIComponent(
        `/?conversation_id=${conversationId || "default"}&auth_resumed=1`,
      )}`;
    const portalHref =
      copilot.signin_portal_href ||
      `/signin?continue=signin&returnTo=${encodeURIComponent(
        copilot.copilot_portal_url || "/portal/e_services?copilot=1",
      )}`;

    return (
      <section
        className="portal-copilot-card portal-stepup-login-card"
        data-testid="portal-stepup-login-card"
        aria-label={t("portalCopilot.stepUpBadge")}
      >
        <div className="portal-copilot-header">
          <span className="portal-copilot-badge is-amber">
            <LockIcon size={13} /> {t("portalCopilot.stepUpBadge")}
          </span>
          {copilot.statutory_citation && (
            <span className="portal-copilot-citation">{copilot.statutory_citation}</span>
          )}
        </div>
        <h3 className="portal-copilot-title">{copilot.title}</h3>
        <p className="portal-copilot-subtitle">{copilot.login_reason}</p>

        {preservedEntries.length > 0 && (
          <div className="portal-preserved-summary">
            <strong>Preserved details (saved across sign-in):</strong>
            <ul>
              {preservedEntries.map(([k, v]) => (
                <li key={k}>
                  <span>{k}:</span> <code>{v}</code>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="portal-copilot-actions">
          <button
            type="button"
            className="portal-action-btn is-primary"
            data-testid="portal-stepup-chat-btn"
            onClick={() => handleStepUpLogin("chat", chatHref)}
          >
            <LockIcon size={14} /> {t("portalCopilot.signInReturnChat")} <ArrowRightIcon size={14} />
          </button>
          <button
            type="button"
            className="portal-action-btn is-ghost"
            data-testid="portal-stepup-portal-btn"
            onClick={() => handleStepUpLogin("portal", portalHref)}
          >
            {t("portalCopilot.signInOpenPortal")} <ExternalLinkIcon size={13} />
          </button>
        </div>
      </section>
    );
  }

  // ── 4. Statutory Co-Pilot Handoff, Stuck Portal Assistance, Executed Result & Launch Banner ──
  const needsLoginBeforePortal = Boolean(copilot.requires_auth && !isSignedIn);
  const targetPortalUrl =
    copilot.copilot_portal_url || `/portal/${copilot.portal_key || "e_services"}?copilot=1`;
  const signInThenPortalUrl =
    copilot.signin_portal_href ||
    `/signin?continue=signin&returnTo=${encodeURIComponent(targetPortalUrl)}`;

  return (
    <section
      className="portal-copilot-card portal-launch-banner-card"
      data-testid={`portal-card-${effectiveCardType}`}
      aria-label={copilot.title || copilot.portal_name || "URA Portal Co-Pilot"}
    >
      <div className="portal-copilot-header">
        <span className="portal-copilot-badge is-emerald">
          {effectiveCardType === "statutory_copilot_handoff" ? (
            <>
              <ShieldCheckIcon size={13} /> {t("portalCopilot.statutoryBadge")}
            </>
          ) : effectiveCardType === "executed_result" ? (
            <>
              <CheckCircleIcon /> {t("portalCopilot.executedBadge")}
            </>
          ) : effectiveCardType === "access_denied" ? (
            <>
              <BanIcon size={13} /> {copilot.statutory_citation || "Access Denied"}
            </>
          ) : (
            <>
              <CompassIcon size={13} /> {t("portalCopilot.stuckBadge")}
            </>
          )}
        </span>
        {copilot.live === false && (
          <span className="portal-copilot-badge is-sim">
            {t("portalCopilot.simulationBadge")}
          </span>
        )}
      </div>

      {copilot.title && <h3 className="portal-copilot-title">{copilot.title}</h3>}
      {(copilot.verification_warning ||
        copilot.user_message ||
        copilot.diagnosis_summary ||
        copilot.error) && (
        <p className="portal-copilot-subtitle">
          {copilot.error ||
            copilot.verification_warning ||
            copilot.user_message ||
            copilot.diagnosis_summary}
        </p>
      )}

      {copilot.completeness_check && !copilot.completeness_check.is_complete && (
        <div className="portal-completeness-guard" data-testid="portal-completeness-guard">
          <p className="portal-field-error">
            <AlertTriangleIcon size={14} /> {t("portalCopilot.incompleteScreenshotWarning")}
          </p>
          {copilot.completeness_check.clarifying_options?.length ? (
            <div className="portal-clarifying-chips">
              {copilot.completeness_check.clarifying_options.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  className="workflow-option-btn"
                  onClick={() => onActionClick?.(chip)}
                >
                  {chip}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {copilot.dom_actions && copilot.dom_actions.length > 0 && (
        <ol className="portal-dom-recipe-list">
          {copilot.dom_actions.slice(0, 5).map((step) => (
            <li key={step.step_number}>
              <strong>{step.element}:</strong> {step.tooltip}
              {step.fill_value ? <code> ({step.fill_value})</code> : null}
            </li>
          ))}
        </ol>
      )}

      <div className="portal-copilot-actions">
        {needsLoginBeforePortal ? (
          <Link
            href={signInThenPortalUrl}
            className="portal-action-btn is-primary"
            data-testid="portal-signin-first-link"
          >
            <LockIcon size={14} />{" "}
            {t("portalCopilot.signInFirstThenCopilot", {
              portal: copilot.portal_name || "URA Portal",
            })}{" "}
            <ArrowRightIcon size={14} />
          </Link>
        ) : (
          <Link
            href={targetPortalUrl}
            className="portal-action-btn is-primary"
            data-testid="portal-direct-copilot-link"
          >
            {copilot.open_portal_label ||
              t("portalCopilot.openWithCopilot", {
                portal: copilot.portal_name || "URA Portal",
              })}{" "}
            <ExternalLinkIcon size={13} />
          </Link>
        )}
      </div>
    </section>
  );
}
