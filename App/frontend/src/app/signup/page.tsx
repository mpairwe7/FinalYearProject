"use client";

/**
 * Account creation.
 *
 * There is no local credential store to register against — the backend
 * verifies tokens and issues none — so "create an account" means one thing
 * here: send the person to the identity provider's registration screen and
 * come back through `/signin/callback` with a token. That is the same
 * authorization-code + PKCE redirect as sign-in, plus the registration hint
 * (`prompt=create` / `screen_hint=signup`); `lib/oidcFlow` owns both.
 *
 * This page exists as its own route rather than a mode of `/signin` because it
 * has a different job: it is where someone arrives from the landing page's
 * "Sign up" button, and the first thing it has to say is that the assistant
 * answers tax questions with no account at all. An account buys saved
 * conversations and, for URA employees, the operations tools.
 */

import Link from "next/link";
import React, { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  getAuthToken,
  getServerAuthToken,
  setAuthToken,
  subscribeAuthToken,
} from "../../lib/authSession";
import { beginOidcFlow, isEmbedded, OIDC_CONFIGURED } from "../../lib/oidcFlow";
import { isStaffRole } from "../../lib/roles";
import "../signin/signin.css";

const BENEFITS = [
  {
    title: "Ask without an account",
    body: "Tax questions, rates, deadlines and document checks all work signed out. Nothing on this page is required to use the assistant.",
  },
  {
    title: "An account adds continuity",
    body: "Your profile — taxpayer type, industry, preferred detail level — shapes the answers, and conversations follow you between devices.",
  },
  {
    title: "URA employees",
    body: "Staff roles are granted by the identity provider, not requested here. Register first, then an administrator assigns the role.",
  },
] as const;

export default function SignUpPage() {
  const [status, setStatus] = useState<{ kind: "idle" | "info" | "error"; message: string }>({
    kind: "idle",
    message: "",
  });
  const [starting, setStarting] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [selectedRole, setSelectedRole] = useState("ura_staff");
  const [submitting, setSubmitting] = useState(false);
  const [createdRole, setCreatedRole] = useState<string | null>(null);

  // Someone who already has a token does not need this page; say so instead of
  // starting a second flow that would just replace a working session.
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);

  const handleLocalSignUp = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!email && !fullName) {
        setStatus({ kind: "error", message: "Please provide an email or name." });
        return;
      }
      setSubmitting(true);
      setStatus({ kind: "idle", message: "" });
      try {
        const userEmail = email.trim() || (selectedRole === "public" ? "taxpayer@ura.go.ug" : `${selectedRole.replace("ura_", "")}@ura.go.ug`);
        const userName = fullName.trim() || userEmail.split("@")[0];
        const res = await fetch("/api/v1/auth/dev-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: selectedRole,
            email: userEmail,
            user_id: userName,
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setStatus({ kind: "error", message: body?.detail || `Signup failed with status ${res.status}.` });
          setSubmitting(false);
          return;
        }
        const data = await res.json();
        if (!data?.token) {
          setStatus({ kind: "error", message: "Failed to mint session token." });
          setSubmitting(false);
          return;
        }
        setAuthToken(data.token, "dev");
        setCreatedRole(data.role);
        setSubmitting(false);
        setStatus({
          kind: "info",
          message: `Account created for ${data.email || data.user_id} (${data.role})! You are now signed in.`,
        });
      } catch (err) {
        setSubmitting(false);
        setStatus({ kind: "error", message: `Could not complete registration: ${(err as Error).message}` });
      }
    },
    [email, fullName, selectedRole],
  );

  const startSignUp = useCallback(async () => {
    if (!OIDC_CONFIGURED) return;
    setStarting(true);
    try {
      // Registration usually ends on a taxpayer account with no dashboard, so
      // the callback sends them back to the assistant rather than to /admin.
      await beginOidcFlow({ mode: "signup", returnTo: "/" });
      if (isEmbedded()) {
        // beginOidcFlow opened a new tab rather than redirecting: identity
        // providers refuse to render inside a frame. Say where it went — the
        // button would otherwise spin on a page that is never going to move.
        setStarting(false);
        setStatus({
          kind: "info",
          message:
            "Registration opened in a new tab — your identity provider will not display inside an embedded page.",
        });
      }
    } catch (err) {
      setStarting(false);
      setStatus({
        kind: "error",
        message: `Could not start registration: ${(err as Error).message}`,
      });
    }
  }, []);

  // Auto-start when the embedded page handed the flow to this tab.
  //
  // beginOidcFlow opens `?continue=signup` in a new top-level tab when it is
  // framed, because identity providers refuse to render in a frame. Without
  // this the person would have to press the same button a second time in a tab
  // they did not ask for, which reads as the first press having failed.
  //
  // Guarded on not being embedded, so a framed page carrying the parameter
  // cannot loop itself opening tabs.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isEmbedded() || !OIDC_CONFIGURED) return;
    if (new URLSearchParams(window.location.search).get("continue") !== "signup") return;
    // queueMicrotask, not a bare call: startSignUp sets its pending state before
    // its first await, and doing that synchronously inside an effect cascades a
    // render. Deferring past commit avoids the cascade rather than suppressing
    // the warning about it.
    queueMicrotask(() => void startSignUp());
    // Once only: the parameter is stripped so a reload does not redirect again.
    window.history.replaceState({}, "", window.location.pathname);
  }, [startSignUp]);

  return (
    <main className="signin-page">
      <div className="signin-card">
        <header className="signin-head">
          <div className="signin-mark" aria-hidden="true">
            URA
          </div>
          <h1>Create an account</h1>
          <p className="signin-sub">
            Uganda Revenue Authority — Tax Assistant
          </p>
        </header>

        {OIDC_CONFIGURED ? (
          <section className="signin-block" aria-labelledby="signup-h">
            <h2 id="signup-h">Register with the URA identity provider</h2>
            <p className="signin-note">
              Accounts, passwords, multi-factor setup and recovery are held by the
              identity provider — this application never sees a password. You will
              be taken there to register and returned here once you are done.
            </p>
            <button
              type="button"
              className="signin-primary"
              onClick={startSignUp}
              disabled={starting}
            >
              {starting ? "Opening the provider…" : "Continue to registration"}
            </button>
          </section>
        ) : (
          <section className="signin-block signin-dev" aria-labelledby="signup-local-h">
            <div className="signin-dev-flag" role="note">
              Quick Account Creation
            </div>
            <h2 id="signup-local-h">Create an account</h2>
            <p className="signin-note">
              Non-IT staff, officers, and taxpayers can create an account below to explore user queries, escalations, and system flows:
            </p>

            <form onSubmit={handleLocalSignUp} style={{ display: "grid", gap: "10px" }}>
              <label className="signin-field">
                <span>Full Name or Officer Name</span>
                <input
                  type="text"
                  className="signin-input"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="e.g. Officer Grace"
                  required
                />
              </label>

              <label className="signin-field">
                <span>Email Address</span>
                <input
                  type="email"
                  className="signin-input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="e.g. grace.o@ura.go.ug"
                  required
                />
              </label>

              <fieldset className="signin-roles" style={{ marginTop: "6px" }}>
                <legend>Role / Access Level</legend>
                <label className={selectedRole === "ura_staff" ? "role-opt active" : "role-opt"}>
                  <input
                    type="radio"
                    name="role"
                    value="ura_staff"
                    checked={selectedRole === "ura_staff"}
                    onChange={() => setSelectedRole("ura_staff")}
                  />
                  <span className="role-name">URA Tax Agent (Staff)</span>
                  <span className="role-hint">Work the escalation queue and inspect user query flows</span>
                </label>
                <label className={selectedRole === "ura_admin" ? "role-opt active" : "role-opt"}>
                  <input
                    type="radio"
                    name="role"
                    value="ura_admin"
                    checked={selectedRole === "ura_admin"}
                    onChange={() => setSelectedRole("ura_admin")}
                  />
                  <span className="role-name">URA Administrator</span>
                  <span className="role-hint">Full operations and analytics console</span>
                </label>
                <label className={selectedRole === "public" ? "role-opt active" : "role-opt"}>
                  <input
                    type="radio"
                    name="role"
                    value="public"
                    checked={selectedRole === "public"}
                    onChange={() => setSelectedRole("public")}
                  />
                  <span className="role-name">Taxpayer (Citizen)</span>
                  <span className="role-hint">Ask tax questions with saved multi-turn conversations</span>
                </label>
              </fieldset>

              <button
                type="submit"
                className="signin-primary"
                style={{ marginTop: "6px" }}
                disabled={submitting}
              >
                {submitting ? "Creating Account…" : "Create Account & Sign In"}
              </button>
            </form>
          </section>
        )}

        <section className="signin-block" aria-labelledby="signup-what">
          <h2 id="signup-what">What an account changes</h2>
          <ul className="signin-benefits">
            {BENEFITS.map((b) => (
              <li key={b.title}>
                <strong>{b.title}</strong>
                <span>{b.body}</span>
              </li>
            ))}
          </ul>
        </section>

        {token && (
          <p className="signin-status ok" role="status">
            You are signed in on this browser.
          </p>
        )}

        {status.message && (
          <p
            className={`signin-status ${status.kind === "error" ? "error" : "ok"}`}
            role={status.kind === "error" ? "alert" : "status"}
          >
            {status.message}
          </p>
        )}

        {createdRole && (
          <nav className="signin-onward" aria-label="Continue to">
            {isStaffRole(createdRole) ? (
              <a href="/agent" style={{ fontWeight: 700 }}>
                👉 Proceed to My Queue & User Queries (/agent)
              </a>
            ) : (
              <Link href="/" style={{ fontWeight: 700 }}>
                👉 Proceed to Assistant Chat (/)
              </Link>
            )}
            <a href="/admin">Operations overview</a>
          </nav>
        )}

        <footer className="signin-switch">
          <p>
            Already have an account? <Link href="/signin">Sign in</Link>
          </p>
          <Link className="signin-switch-alt" href="/">
            Continue without an account
          </Link>
        </footer>
      </div>
    </main>
  );
}
