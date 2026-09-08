"use client";

/**
 * Sign-in.
 *
 * Titled plainly rather than "Staff sign-in": the sidebar account block, the
 * header pair, the landing note, Settings › Account and the tax-profile panel
 * all send ordinary taxpayers here, and a page announcing itself as staff-only
 * tells most of the people who arrive that they are in the wrong place. Staff
 * are served by the same OIDC redirect — the difference is the role their token
 * carries, resolved after the exchange, not a different door.
 *
 * The backend VERIFIES tokens; it does not issue them. There is no credential
 * store and no `/auth/login` — `auth/jwt_auth.py` does HS256 (dev shared
 * secret) or RS256 against a remote JWKS.
 *
 * All logins (taxpayers, staff, admin, auditor) authenticate through the
 * configured Auth0 OIDC flow.
 *
 * Standards: OAuth 2.1 authorization-code + PKCE (draft-ietf-oauth-v2-1),
 * OIDC Core 1.0 §3.1; WCAG 2.2 AA for the form semantics.
 */
import Link from "next/link";
import React, { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  clearAuthToken,
  getAuthMethod,
  getAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../../lib/authSession";
import {
  beginOidcFlow,
  endOidcSession,
  isEmbedded,
  OIDC_CONFIGURED,
} from "../../lib/oidcFlow";
import "./signin.css";

export default function SignInPage() {
  const [status, setStatus] = useState<{ kind: "idle" | "error" | "ok"; message: string }>({
    kind: "idle",
    message: "",
  });

  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const signedIn = Boolean(token);

  const startOidc = useCallback(async (prompt?: "login") => {
    if (!OIDC_CONFIGURED) return;
    try {
      await beginOidcFlow({ mode: "signin", prompt });
      if (isEmbedded()) {
        setStatus({
          kind: "ok",
          message:
            "Sign-in opened in a new tab — your identity provider will not display inside an embedded page.",
        });
      }
    } catch (err) {
      setStatus({
        kind: "error",
        message: `Could not start the sign-in redirect: ${(err as Error).message}`,
      });
    }
  }, []);

  // Auto-start when the embedded page handed the flow to this tab.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isEmbedded() || !OIDC_CONFIGURED) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("continue") !== "signin") return;
    const prompt = params.get("prompt") === "login" ? "login" : undefined;
    queueMicrotask(() => void startOidc(prompt));
    window.history.replaceState({}, "", window.location.pathname);
  }, [startOidc]);

  const signOut = useCallback(() => {
    const method = getAuthMethod();
    clearAuthToken();
    setStatus({ kind: "idle", message: "Signed out." });
    if (method !== "dev") endOidcSession();
  }, []);

  return (
    <main className="signin-page">
      <div className="signin-card">
        <header className="signin-head">
          <div className="signin-mark" aria-hidden="true">
            URA
          </div>
          <h1>Sign in</h1>
          <p className="signin-sub">
            Uganda Revenue Authority — Tax Assistant
          </p>
        </header>

        <section className="signin-block" aria-labelledby="oidc-h">
          <h2 id="oidc-h">Sign in with your URA account</h2>
          <p className="signin-note">
            Accounts, passwords, Google sign-in and security recovery are managed by URA&apos;s configured Auth0 identity provider.
            Universal Login authenticates taxpayers, tax agents, and administrators, routing each user to their designated workspace upon sign-in.
          </p>
          <button
            type="button"
            className="signin-primary"
            onClick={() => void startOidc()}
            disabled={!OIDC_CONFIGURED}
          >
            {OIDC_CONFIGURED ? "Continue with URA identity provider" : "Identity provider not configured"}
          </button>
          {/* The escape hatch from "it signed me in as the wrong person". */}
          {OIDC_CONFIGURED && (
            <button
              type="button"
              className="signin-link signin-switch-account"
              onClick={() => void startOidc("login")}
              data-testid="signin-different-account"
            >
              Sign in as a different user
            </button>
          )}
          {!OIDC_CONFIGURED && (
            <p className="signin-hint">
              Set <code>NEXT_PUBLIC_OIDC_ISSUER</code> and{" "}
              <code>NEXT_PUBLIC_OIDC_CLIENT_ID</code> to enable this.
            </p>
          )}
        </section>

        {status.message && (
          <p
            className={`signin-status ${status.kind}`}
            role={status.kind === "error" ? "alert" : "status"}
          >
            {status.message}
          </p>
        )}

        {signedIn && (
          <nav className="signin-onward" aria-label="Continue to">
            <a href="/agent" style={{ fontWeight: 700 }}>
              👉 Escalation Queue &amp; User Queries (/agent)
            </a>
            <a href="/admin">Operations overview (/admin)</a>
            <a href="/analytics">Analytics (/analytics)</a>
            <button type="button" className="signin-link" onClick={signOut}>
              Sign out
            </button>
          </nav>
        )}

        <footer className="signin-switch">
          <p>
            No account yet? <Link href="/signup">Create one</Link>
          </p>
          <Link className="signin-switch-alt" href="/">
            Back to the assistant
          </Link>
        </footer>
      </div>
    </main>
  );
}
