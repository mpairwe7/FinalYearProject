"use client";

/**
 * Account — who you are signed in as, and the ways in and out.
 *
 * Five states, because "signed in" is not a boolean here: a token can exist and
 * still be refused (expired, or minted for a different deployment), and the
 * whole auth stack can be unconfigured. Each of those needs its own sentence,
 * or the panel reads as broken.
 */

import Link from "next/link";
import React, { useEffect, useState } from "react";
import type { IdentityState } from "../../hooks/useIdentity";
import { staffDestinationsFor } from "../../lib/roles";
import { accountApi, ApiError } from "../../services/accountApi";
import { ActionButton, SettingsRow, SettingsSection, StatusNote } from "./controls";

export default function AccountSection({ state }: { state: IdentityState }) {
  const { status, identity, name, roleName, initials, error, isStaff, signOut, refresh } = state;
  const notConfigured = error instanceof ApiError && error.status === 503;
  const destinations = staffDestinationsFor(identity?.role);

  return (
    <>
      <SettingsSection
        title="Account"
        description="Identities are held by the URA identity provider. This application only verifies the token it issues — it never sees a password."
      >
        {status === "anonymous" && (
          <>
            <StatusNote kind="info">
              You are using the assistant signed out. Questions, document checks
              and voice all work this way; an account adds a saved profile and
              conversations that follow you between devices.
            </StatusNote>
            <div className="setv2-actions">
              <Link className="setv2-btn setv2-btn-primary" href="/signin">
                Sign in
              </Link>
              <Link className="setv2-btn setv2-btn-secondary" href="/signup">
                Create an account
              </Link>
            </div>
          </>
        )}

        {status === "checking" && <StatusNote kind="info">Checking your sign-in…</StatusNote>}

        {status === "rejected" && (
          <>
            <StatusNote kind="error">
              This browser holds a token, but the backend does not accept it — it
              has most likely expired, or it was issued for a different
              deployment. Signing in again replaces it.
            </StatusNote>
            <div className="setv2-actions">
              <Link className="setv2-btn setv2-btn-primary" href="/signin">
                Sign in again
              </Link>
              <ActionButton onClick={signOut}>Discard the token</ActionButton>
            </div>
          </>
        )}

        {status === "unavailable" && (
          <>
            <StatusNote kind="error">
              {notConfigured
                ? "Accounts are not configured on this deployment, so signing in is unavailable. The assistant still answers questions."
                : `Could not reach the backend to check your sign-in: ${error?.message ?? "unknown error"}`}
            </StatusNote>
            <div className="setv2-actions">
              <ActionButton onClick={refresh}>Try again</ActionButton>
              <ActionButton onClick={signOut}>Sign out</ActionButton>
            </div>
          </>
        )}

        {status === "signed-in" && identity && (
          <>
            <div className="setv2-identity">
              <span className="setv2-avatar" aria-hidden="true">
                {initials}
              </span>
              <span className="setv2-identity-text">
                <span className="setv2-identity-name">{name}</span>
                <span className="setv2-identity-role">{roleName}</span>
              </span>
            </div>

            <SettingsRow label="Role" hint="Granted by the identity provider, not from this page.">
              <span className="setv2-static">{identity.role}</span>
            </SettingsRow>
            {identity.tenant_id && (
              <SettingsRow label="Organisation" hint="The tenant your records are scoped to.">
                <span className="setv2-static">{identity.tenant_id}</span>
              </SettingsRow>
            )}
            {identity.external_id && (
              <SettingsRow
                label="Provider subject"
                hint="The stable id your identity provider issued for you."
              >
                <span className="setv2-static setv2-static-mono">{identity.external_id}</span>
              </SettingsRow>
            )}

            <div className="setv2-actions">
              <ActionButton variant="danger" onClick={signOut}>
                Sign out
              </ActionButton>
            </div>
          </>
        )}
      </SettingsSection>

      {status === "signed-in" ? <SandboxAccountPanel /> : null}
      {status === "signed-in" ? <ReminderInboxPanel /> : null}

      {isStaff && destinations.length > 0 && (
        <SettingsSection
          title="Operations tools"
          description="Available to your role. The API enforces access independently of this list."
        >
          <div className="setv2-links">
            {destinations.map((d) => (
              <a key={d.href} className="setv2-link" href={d.href}>
                {d.label}
              </a>
            ))}
          </div>
        </SettingsSection>
      )}
    </>
  );
}

function SandboxAccountPanel() {
  const [note, setNote] = useState<string>("");
  const [tin, setTin] = useState<string>("");
  const [live, setLive] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    accountApi
      .account()
      .then((row) => {
        if (cancelled) return;
        setLive(Boolean(row.live));
        setTin(row.profile?.tin || "");
        setNote(row.profile?.note || row.source || "");
      })
      .catch(() => {
        if (!cancelled) setNote("Account connector unavailable.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SettingsSection
      title="URA account (prototype)"
      description="Sandbox snapshot. Never treated as a live URA balance."
    >
      {live === false ? (
        <StatusNote kind="info">
          {tin ? `Sandbox TIN ${tin}. ` : ""}
          {note || "Placeholder. Not URA data."}
        </StatusNote>
      ) : live ? (
        <StatusNote kind="info">Live connector reported configured.</StatusNote>
      ) : (
        <StatusNote kind="info">Loading account snapshot…</StatusNote>
      )}
    </SettingsSection>
  );
}

function ReminderInboxPanel() {
  const [lines, setLines] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    accountApi
      .reminders()
      .then((row) => {
        if (!cancelled) setLines((row.reminders || []).map((r) => r.message || r.deadline_name));
      })
      .catch(() => {
        if (!cancelled) setLines([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <SettingsSection
      title="Deadline inbox"
      description="In-app reminders. Email and SMS stay in a mock outbox."
    >
      {lines.length === 0 ? (
        <StatusNote kind="info">No due reminders in the inbox.</StatusNote>
      ) : (
        lines.map((line) => (
          <StatusNote key={line} kind="info">
            {line}
          </StatusNote>
        ))
      )}
    </SettingsSection>
  );
}
