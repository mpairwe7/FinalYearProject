"use client";

import Link from "next/link";
import React, { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from "react";
import { authHeaders, getAuthToken, getServerAuthToken, subscribeAuthToken } from "../lib/authSession";
import { getChatSessionId } from "../lib/chatSession";
import { useTranslation } from "../lib/i18n";
import { sanitizeReturnTo } from "../lib/oidcFlow";
import {
  sanitizeContinuationFields,
  savePendingPortalContinuation,
} from "../lib/portalContinuation";
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
  disabled?: boolean;
}

/**
 * Compute canonical WYSIWYS SHA-256 digest over rendered `display_fields` (`[{name, value}]`)
 * matching `portal_copilot.compute_displayed_fields_sha256` on the backend (OWASP ASI09).
 */
export async function computeDisplayedFieldsSha256(
  displayFields: ReadonlyArray<{ name: string; value: string; raw_value?: string }> | undefined,
): Promise<string | undefined> {
  if (!Array.isArray(displayFields)) return undefined;
  try {
    if (typeof crypto === "undefined" || !crypto.subtle) return undefined;
    const canonical = displayFields.map((item) => ({
      name: String(item.name ?? ""),
      value: String(item.raw_value !== undefined ? item.raw_value : (item.value ?? "")),
    }));
    const bytes = new TextEncoder().encode(JSON.stringify(canonical));
    const digestBuf = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digestBuf))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return undefined;
  }
}

/**
 * Declarative A2UI card surface for URA Portal Task Execution, Inline Form Elicitation,
 * Claude-Style Allow/Disallow Confirmation, Step-Up Login, and Stuck-User DOM Co-Pilot handoffs.
 */
export default function PortalCopilotCard({
  copilot,
  conversationId,
  onSubmitInlineForm,
  onActionClick,
  disabled = false,
}: PortalCopilotCardProps) {
  const t = useTranslation();
  const fieldIdPrefix = useId();
  const decisionInFlight = useRef(false);
  const [activeCopilot, setActiveCopilot] = useState<PortalCopilotPayload>(copilot);
  const [editing, setEditing] = useState(false);
  const [highValueAck, setHighValueAck] = useState(false);
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
    decisionReceipt?: {
      receipt_id?: string;
      params_sha256?: string;
      displayed_fields_sha256?: string;
      statutory_citation?: string;
    };
  }>({ status: "idle" });

  // Keep activeCopilot and formValues synchronized when a new prop arrives
  useEffect(() => {
    setActiveCopilot(copilot);
    setHighValueAck(false);
    setFormValues((prev) => {
      const next: Record<string, string> = {};
      for (const f of copilot.fields ?? []) {
        next[f.name] = f.value !== undefined && f.value !== "" ? f.value : (prev[f.name] ?? "");
      }
      return next;
    });
  }, [copilot]);

  const authToken = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const isSignedIn = Boolean(authToken);
  const effectiveCardType = editing ? "inline_form" : activeCopilot.card_type;

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
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (disabled || execState.status === "busy") return;
      const taskId = activeCopilot.task_id || "";
      if (!taskId) return;

      // Zero-LLM inline payload re-validation & HMAC re-signing when editing an existing confirmation card
      if (editing && activeCopilot.request_state && activeCopilot.idempotency_key) {
        setExecState({ status: "busy" });
        try {
          const res = await fetch("/api/v1/portal/tasks/execute", {
            method: "POST",
            headers: authHeaders({
              "Content-Type": "application/json",
              "X-Session-ID": getChatSessionId(),
            }),
            body: JSON.stringify({
              decision: "prepare",
              request_state: activeCopilot.request_state,
              idempotency_key: activeCopilot.idempotency_key,
              conversation_id: conversationId || "default",
              edited_fields: formValues,
            }),
          });
          const body = await res.json().catch(() => ({}));
          if (body.status === "validation_error" && body.updated_card) {
            setActiveCopilot((prev) => ({
              ...prev,
              fields: body.updated_card.fields ?? prev.fields,
              field_errors: body.updated_card.field_errors ?? prev.field_errors,
            }));
            setExecState({ status: "idle" });
            return;
          }
          if (res.ok && body.status === "prepared" && body.updated_card) {
            setActiveCopilot(body.updated_card as PortalCopilotPayload);
            setHighValueAck(false);
            setEditing(false);
            setExecState({ status: "idle" });
            return;
          }
        } catch {
          // Keep edited values for review; an ambiguous prepare failure must
          // not fall through to a second submission through chat.
        }
        setExecState({ status: "error", summary: t("portalCopilot.prepareFailed") });
        return;
      }

      setEditing(false);
      if (onSubmitInlineForm) {
        onSubmitInlineForm(taskId, formValues);
      }
    },
    [
      activeCopilot.idempotency_key,
      activeCopilot.request_state,
      activeCopilot.task_id,
      conversationId,
      editing,
      formValues,
      onSubmitInlineForm,
      disabled,
      execState.status,
      t,
    ],
  );

  const handleDecision = useCallback(
    async (decision: "allow" | "disallow") => {
      if (disabled || decisionInFlight.current || execState.status === "busy") return;
      if (!activeCopilot.request_state || !activeCopilot.idempotency_key) return;
      decisionInFlight.current = true;
      setExecState({ status: "busy" });
      try {
        const payloadBody: Record<string, unknown> = {
          decision,
          request_state: activeCopilot.request_state,
          idempotency_key: activeCopilot.idempotency_key,
          conversation_id: conversationId || "default",
        };
        if (decision === "allow") {
          const computedDigest = await computeDisplayedFieldsSha256(activeCopilot.display_fields);
          if (!computedDigest) {
            setExecState({ status: "error", summary: t("portalCopilot.verificationUnavailable") });
            return;
          }
          payloadBody.displayed_fields_sha256 = computedDigest;
        }
        if (activeCopilot.high_value_requires_ack) {
          payloadBody.high_value_acknowledged = highValueAck;
        }
        const res = await fetch("/api/v1/portal/tasks/execute", {
          method: "POST",
          headers: authHeaders({
            "Content-Type": "application/json",
            "X-Session-ID": getChatSessionId(),
          }),
          body: JSON.stringify(payloadBody),
        });
        const body = await res.json().catch(() => ({}));
        const expectedStatus = decision === "disallow" ? "declined" : "executed";
        if (!res.ok || body.ok !== true || body.status !== expectedStatus) {
          setExecState({
            status: "error",
            summary: t("portalCopilot.resultUnconfirmed"),
          });
          return;
        }
        setExecState({
          status: body.status === "declined" ? "declined" : "executed",
          summary:
            body.summary || (decision === "disallow" ? "Action cancelled." : "Task executed."),
          result: body.result || {},
          decisionReceipt: body.decision_receipt || undefined,
        });
      } catch {
        setExecState({
          status: "error",
          summary: t("portalCopilot.resultUnconfirmed"),
        });
      } finally {
        decisionInFlight.current = false;
      }
    },
    [
      activeCopilot.display_fields,
      activeCopilot.high_value_requires_ack,
      activeCopilot.idempotency_key,
      activeCopilot.request_state,
      conversationId,
      highValueAck,
      disabled,
      execState.status,
      t,
    ],
  );

  const handleStepUpLogin = useCallback(
    (returnMode: "chat" | "portal", rawHref: string) => {
      const safeHref =
        sanitizeReturnTo(rawHref) ||
        `/signin?continue=signin&returnTo=${encodeURIComponent(
          `/?conversation_id=${conversationId || "default"}&auth_resumed=1`,
        )}`;
      savePendingPortalContinuation({
        conversationId: conversationId || "default",
        taskId: copilot.task_id || "",
        portalKey: copilot.portal_key || "e_services",
        collectedFields: sanitizeContinuationFields(copilot.preserved_fields || formValues),
        returnMode,
      });
      if (typeof window !== "undefined") {
        window.location.assign(safeHref);
      }
    },
    [conversationId, copilot.portal_key, copilot.preserved_fields, copilot.task_id, formValues],
  );

  const safeCopilotPortalUrl =
    sanitizeReturnTo(copilot.copilot_portal_url) ||
    `/portal/${copilot.portal_key || "e_services"}?copilot=1`;

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
          {(activeCopilot.fields ?? []).map((f) => {
            const inputId = `${fieldIdPrefix}-portal-field-${f.name}`;
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
                    disabled={disabled || execState.status === "busy"}
                    id={inputId}
                    name={f.name}
                    className="portal-form-input"
                    value={currentVal}
                    required={f.required}
                    aria-invalid={hasErr}
                    aria-describedby={f.validation_error || f.helper_text ? descId : undefined}
                    onChange={(e) => handleFieldChange(f.name, e.target.value)}
                  >
                    <option value="">
                      {t("portalCopilot.selectFieldPrompt", { field: f.label })}
                    </option>
                    {f.options.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    disabled={disabled || execState.status === "busy"}
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

          {execState.status === "error" && (
            <p className="portal-field-error" role="alert">{execState.summary}</p>
          )}
          <div className="portal-copilot-actions">
            <button
              type="submit"
              className="portal-action-btn is-primary"
              data-testid="portal-inline-form-submit-btn"
              disabled={disabled || execState.status === "busy" || (!onSubmitInlineForm && !editing)}
            >
              <ShieldCheckIcon size={15} />{" "}
              {copilot.submit_label || t("portalCopilot.continueToReview")}
            </button>
            {copilot.copilot_portal_url && (
              <Link
                href={safeCopilotPortalUrl}
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
              <strong>{t("portalCopilot.prnReferenceLabel")}</strong> <code>{prnVal}</code>
            </div>
          )}
          {tinVal && (
            <div className="portal-receipt-kv" data-testid="portal-generated-tin">
              <strong>{t("portalCopilot.tinReferenceLabel")}</strong> <code>{tinVal}</code>
            </div>
          )}
          {execState.decisionReceipt?.receipt_id && (
            <div className="portal-receipt-kv" data-testid="portal-decision-receipt-id">
              <strong>{t("portalCopilot.decisionReceiptLabel")}</strong>{" "}
              <code>{execState.decisionReceipt.receipt_id}</code>
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
              <Link href={safeCopilotPortalUrl} className="portal-action-btn is-ghost">
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
          {(activeCopilot.display_fields ?? []).map((df) => {
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

        {activeCopilot.high_value_requires_ack && (
          <label
            className="portal-high-value-ack"
            data-testid="portal-high-value-ack-label"
          >
            <input
              type="checkbox"
              data-testid="portal-high-value-ack-checkbox"
              checked={highValueAck}
              onChange={(e) => setHighValueAck(e.target.checked)}
            />{" "}
            <span>{t("portalCopilot.highValueAckLabel")}</span>
          </label>
        )}

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
            disabled={
              disabled || execState.status === "busy" ||
              (Boolean(activeCopilot.high_value_requires_ack) && !highValueAck)
            }
            onClick={() => void handleDecision("allow")}
          >
            <CheckCircleIcon />{" "}
            {execState.status === "busy"
              ? t("portalCopilot.executing")
              : activeCopilot.allow_label || t("portalCopilot.allowBtn")}
          </button>
          <button
            type="button"
            className="portal-action-btn is-ghost"
            data-testid="portal-task-edit-btn"
            disabled={disabled || execState.status === "busy"}
            onClick={() => setEditing(true)}
          >
            <PencilIcon /> {copilot.edit_label || t("portalCopilot.editBtn")}
          </button>
          <button
            type="button"
            className="portal-action-btn is-danger"
            data-testid="portal-task-disallow-btn"
            disabled={disabled || execState.status === "busy"}
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
      sanitizeReturnTo(copilot.signin_chat_href) ||
      `/signin?continue=signin&returnTo=${encodeURIComponent(
        `/?conversation_id=${conversationId || "default"}&auth_resumed=1`,
      )}`;
    const portalHref =
      sanitizeReturnTo(copilot.signin_portal_href) ||
      `/signin?continue=signin&returnTo=${encodeURIComponent(safeCopilotPortalUrl)}`;

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
            <strong>{t("portalCopilot.preservedDetailsHeading")}</strong>
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
  const signInThenPortalUrl =
    sanitizeReturnTo(copilot.signin_portal_href) ||
    `/signin?continue=signin&returnTo=${encodeURIComponent(safeCopilotPortalUrl)}`;

  return (
    <section
      className="portal-copilot-card portal-launch-banner-card"
      data-testid={`portal-card-${effectiveCardType}`}
      aria-label={copilot.title || copilot.portal_name || "URA Portal Co-Pilot"}
    >
      <div className="portal-copilot-header">
        <span
          className={`portal-copilot-badge ${
            effectiveCardType === "error_diagnosis" ||
            effectiveCardType === "access_denied" ||
            copilot.result?.ok === false
              ? "is-amber"
              : "is-emerald"
          }`}
        >
          {effectiveCardType === "statutory_copilot_handoff" ? (
            <>
              <ShieldCheckIcon size={13} /> {t("portalCopilot.statutoryBadge")}
            </>
          ) : effectiveCardType === "executed_result" && copilot.result?.ok !== false ? (
            <>
              <CheckCircleIcon /> {t("portalCopilot.executedBadge")}
            </>
          ) : effectiveCardType === "access_denied" ? (
            <>
              <BanIcon size={13} />{" "}
              {copilot.statutory_citation || t("portalCopilot.accessDeniedBadge")}
            </>
          ) : effectiveCardType === "error_diagnosis" || copilot.result?.ok === false ? (
            <>
              <AlertTriangleIcon size={13} />{" "}
              {copilot.error_diagnosis?.error_code || t("portalCopilot.stuckBadge")}
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
      {copilot.what_needs_to_be_done && (
        <p className="portal-copilot-subtitle" data-testid="portal-card-what-needs-to-be-done">
          {copilot.what_needs_to_be_done}
        </p>
      )}
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

      {copilot.checklist && copilot.checklist.length > 0 && (
        <ul className="portal-guidance-checklist" data-testid="portal-card-checklist">
          {copilot.checklist.map((item, idx) => (
            <li key={idx} className={`is-${item.status}`}>
              <strong>{item.item}:</strong> <span>{item.detail}</span>
            </li>
          ))}
        </ul>
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

      {copilot.dom_actions && copilot.dom_actions.length > 0 ? (
        <ol className="portal-dom-recipe-list">
          {copilot.dom_actions.slice(0, 5).map((step) => (
            <li key={step.step_number}>
              <strong>{step.element}:</strong> {step.tooltip}
              {step.fill_value ? <code> ({step.fill_value})</code> : null}
            </li>
          ))}
        </ol>
      ) : copilot.error_diagnosis?.resolution_steps?.length ? (
        <ol className="portal-dom-recipe-list" data-testid="portal-error-resolution-steps">
          {copilot.error_diagnosis.resolution_steps.map((stepText, idx) => (
            <li key={idx}>{stepText}</li>
          ))}
        </ol>
      ) : null}

      {copilot.recommended_prompts && copilot.recommended_prompts.length > 0 && onActionClick ? (
        <div className="portal-clarifying-chips" data-testid="portal-card-recommended-prompts">
          {copilot.recommended_prompts.map((promptText) => (
            <button
              key={promptText}
              type="button"
              className="workflow-option-btn"
              onClick={() => onActionClick(promptText)}
            >
              {promptText}
            </button>
          ))}
        </div>
      ) : null}

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
            href={safeCopilotPortalUrl}
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
