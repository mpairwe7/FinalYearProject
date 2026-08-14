"use client";

/**
 * Privacy and data controls.
 *
 * Three different stores of data are involved and the panel keeps them
 * visibly separate, because "delete my data" means something different in each:
 *
 *   - This browser — analytics consent and the conversation list, both in
 *     localStorage. Clearing them touches nothing on the server.
 *   - Consent receipts — append-only rows behind `/v1/me/consents`. Withdrawing
 *     `personalization` also purges the memory built under it (UDPA 2019: a
 *     withdrawal must stop the processing, not just record an intention).
 *   - The account — `GET /v1/me/export` is the UDPA data-portability copy;
 *     `DELETE /v1/me` is the right-to-erasure call and is irreversible.
 */

import React, { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getAnalyticsConsent, setAnalyticsConsent } from "../../lib/analyticsConsent";
import { clearAnalyticsQueue, initAnalytics } from "../../store/useAnalyticsStore";
import { useChatStore } from "../../store/useChatStore";
import {
  accountApi,
  type ConsentPurpose,
  type ConsentReceipt,
} from "../../services/accountApi";
import type { ConfirmRequest } from "../ConfirmDialog";
import {
  ActionButton,
  SettingsRow,
  SettingsSection,
  StatusNote,
  Toggle,
} from "./controls";

/**
 * The purposes worth showing, with what each one actually gates. Every entry
 * is a value the backend's CHECK constraint accepts (`ConsentPurpose`); the
 * remaining URA-account purposes are granted by the identity provider's scopes
 * rather than from here, so they are not offered as toggles.
 */
const PURPOSES: readonly { value: ConsentPurpose; label: string; hint: string }[] = [
  {
    value: "personalization",
    label: "Remember facts about me",
    hint: "Lets the assistant reuse things like your taxpayer type between conversations. Withdrawing also deletes what it already learned.",
  },
  {
    value: "analytics",
    label: "Product analytics on my account",
    hint: "Aggregate usage counts kept against your account, on top of the anonymous browser analytics above.",
  },
  {
    value: "ticket_escalation",
    label: "Escalate to a human officer",
    hint: "Allows a conversation to be attached to a support ticket a URA officer can read.",
  },
  {
    value: "long_term_storage",
    label: "Keep my conversations",
    hint: "Retains chat history past the default retention window so it is there when you come back.",
  },
  {
    value: "voice_recording",
    label: "Voice input",
    hint: "Required by the voice pipeline when voice consent is enforced. Audio is hashed for the audit trail, never stored by default.",
  },
];

function downloadJson(filename: string, data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  // Revoke on the next tick — Safari cancels the download if the URL dies first.
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

interface PrivacySectionProps {
  signedIn: boolean;
  /** Routed through the dialog's ConfirmDialog so destructive rows get a step. */
  requestConfirm: (request: ConfirmRequest) => void;
  onSignedOut: () => void;
}

export default function PrivacySection({
  signedIn,
  requestConfirm,
  onSignedOut,
}: PrivacySectionProps) {
  const queryClient = useQueryClient();
  const conversations = useChatStore((s) => s.conversations);
  const clearAllSessions = useChatStore((s) => s.clearAllSessions);
  // Consent is read once per open rather than subscribed: this panel is the
  // only thing that changes it while it is on screen.
  const [analytics, setAnalytics] = useState<boolean>(() => getAnalyticsConsent() === true);
  const [note, setNote] = useState<{ kind: "ok" | "error"; message: string } | null>(null);

  const consentsQuery = useQuery({
    queryKey: ["consents"],
    queryFn: accountApi.consents,
    enabled: signedIn,
    staleTime: 30_000,
    retry: false,
  });

  const active = new Set(
    (consentsQuery.data?.consents ?? [])
      .filter((c: ConsentReceipt) => c.withdrawn_at === null)
      .map((c: ConsentReceipt) => c.purpose),
  );

  const consentMutation = useMutation({
    // The receipts are re-read from the server afterwards, so neither call's
    // return value is used here — hence the void result rather than a union of
    // the grant and withdraw response shapes.
    mutationFn: async ({ purpose, grant }: { purpose: ConsentPurpose; grant: boolean }) => {
      if (grant) await accountApi.grantConsents([purpose]);
      else await accountApi.withdrawConsents([purpose]);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["consents"] }),
  });

  const exportMutation = useMutation({
    mutationFn: accountApi.export,
    onSuccess: (data) => {
      downloadJson("ura-assistant-account-export.json", data);
      setNote({ kind: "ok", message: "Your account export has been downloaded." });
    },
    onError: (err: Error) =>
      setNote({ kind: "error", message: `Export failed: ${err.message}` }),
  });

  const eraseMutation = useMutation({
    mutationFn: accountApi.erase,
    onSuccess: () => {
      // The account's rows are gone; keeping the token would show a signed-in
      // shell with nothing behind it.
      clearAllSessions();
      onSignedOut();
    },
    onError: (err: Error) =>
      setNote({ kind: "error", message: `Deletion failed: ${err.message}` }),
  });

  const toggleAnalytics = useCallback((next: boolean) => {
    setAnalytics(next);
    setAnalyticsConsent(next);
    if (next) initAnalytics();
    else clearAnalyticsQueue();
  }, []);

  const exportLocalChats = useCallback(() => {
    downloadJson("ura-assistant-conversations.json", {
      exported_at: new Date().toISOString(),
      conversations: useChatStore.getState().conversations,
    });
  }, []);

  const confirmClearChats = useCallback(() => {
    requestConfirm({
      title: "Delete all conversations?",
      message: `All ${conversations.length} saved conversations will be removed from this browser. This cannot be undone.`,
      confirmLabel: "Delete all",
      danger: true,
      action: () => {
        clearAllSessions();
        setNote({ kind: "ok", message: "Conversations deleted from this browser." });
      },
    });
  }, [clearAllSessions, conversations.length, requestConfirm]);

  const confirmErase = useCallback(() => {
    requestConfirm({
      title: "Delete your account data?",
      message:
        "Your profile, conversations, consent history and remembered facts are deleted from the server and you are signed out. The tamper-evident audit ledger keeps a record that the deletion happened. This cannot be undone.",
      confirmLabel: "Delete everything",
      danger: true,
      action: () => eraseMutation.mutate(),
    });
  }, [eraseMutation, requestConfirm]);

  return (
    <>
      <SettingsSection
        title="This browser"
        description="Stored locally on this device — no account needed, and nothing here is sent anywhere."
      >
        <SettingsRow
          label="Anonymous product analytics"
          hint="Event counts with no personal data, used to see which parts of the assistant work. The same choice as the privacy banner."
        >
          <Toggle
            label="Anonymous product analytics"
            checked={analytics}
            onChange={toggleAnalytics}
          />
        </SettingsRow>

        <SettingsRow
          label="Saved conversations"
          hint={
            conversations.length === 0
              ? "No conversations saved in this browser yet."
              : `${conversations.length} conversation${conversations.length === 1 ? "" : "s"} in this browser's storage.`
          }
        >
          <ActionButton onClick={exportLocalChats} disabled={conversations.length === 0}>
            Download
          </ActionButton>
        </SettingsRow>

        <SettingsRow label="Clear chat history" hint="Removes every saved conversation from this device.">
          <ActionButton
            variant="danger"
            onClick={confirmClearChats}
            disabled={conversations.length === 0}
          >
            Delete all
          </ActionButton>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        title="Consent"
        description="What this service may do with your data, recorded as dated receipts you can withdraw at any time (UDPA 2019)."
      >
        {!signedIn && (
          <StatusNote kind="info">
            Consent receipts belong to an account. Signed out, only the
            browser-local choices above apply.
          </StatusNote>
        )}

        {signedIn && consentsQuery.error && (
          <StatusNote kind="error">
            Could not load your consent receipts: {(consentsQuery.error as Error).message}
          </StatusNote>
        )}

        {signedIn &&
          !consentsQuery.error &&
          PURPOSES.map((purpose) => (
            <SettingsRow key={purpose.value} label={purpose.label} hint={purpose.hint}>
              <Toggle
                label={purpose.label}
                checked={active.has(purpose.value)}
                disabled={consentsQuery.isPending || consentMutation.isPending}
                onChange={(next) =>
                  consentMutation.mutate({ purpose: purpose.value, grant: next })
                }
              />
            </SettingsRow>
          ))}

        {consentMutation.error && (
          <StatusNote kind="error">
            Could not record that change: {(consentMutation.error as Error).message}
          </StatusNote>
        )}
      </SettingsSection>

      <SettingsSection
        title="Your account data"
        description="Rights under the Uganda Data Protection and Privacy Act, 2019 — a copy of everything held about you, or its removal."
      >
        {!signedIn ? (
          <StatusNote kind="info">
            Nothing is held server-side for an anonymous browser, so there is
            nothing to export or erase.
          </StatusNote>
        ) : (
          <>
            <SettingsRow
              label="Export my data"
              hint="Identity, profile, consent receipts, conversations, tickets and remembered facts, as JSON."
            >
              <ActionButton onClick={() => exportMutation.mutate()} busy={exportMutation.isPending}>
                {exportMutation.isPending ? "Preparing…" : "Download"}
              </ActionButton>
            </SettingsRow>

            <SettingsRow
              label="Delete my data"
              hint="Erases every record tied to your account and signs you out. Irreversible."
            >
              <ActionButton variant="danger" onClick={confirmErase} busy={eraseMutation.isPending}>
                {eraseMutation.isPending ? "Deleting…" : "Delete account data"}
              </ActionButton>
            </SettingsRow>
          </>
        )}

        {note && <StatusNote kind={note.kind}>{note.message}</StatusNote>}
      </SettingsSection>
    </>
  );
}
