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
 * secret) or RS256 against a remote JWKS. So this page cannot be an
 * email/password form: there is nothing to post to. It offers the two paths
 * that actually exist.
 *
 * 1. OIDC authorization-code redirect to the configured issuer. Passwords and
 *    recovery belong to that provider, which is why this page has no
 *    credential form of its own. Registration is the same redirect with one
 *    extra parameter and lives on `/signup` (see `lib/oidcFlow`).
 * 2. A dev token, for exploring the dashboards where no IdP is configured. It is
 *    NOT authentication and says so on screen — the backend's `make_dev_token`
 *    refuses to run under APP_ENV=production, and this panel hides itself unless
 *    the deployment opts in.
 *
 * Standards: OAuth 2.1 authorization-code + PKCE (draft-ietf-oauth-v2-1),
 * OIDC Core 1.0 §3.1; WCAG 2.2 AA for the form semantics.
 */
import Link from "next/link";
import React, { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  setAuthToken,
  getAuthToken,
  clearAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../../lib/authSession";
import { beginOidcFlow, isEmbedded, OIDC_CONFIGURED } from "../../lib/oidcFlow";
import { isStaffRole } from "../../lib/roles";
import "./signin.css";

/** Roles the dev-token panel can request — labels are specific to this panel. */
const DEV_ROLE_OPTIONS = [
  { role: "ura_staff", label: "Tax agent", hint: "Works the escalation queue" },
  { role: "ura_admin", label: "Administrator", hint: "Full operations view" },
  { role: "ura_auditor", label: "Auditor", hint: "Read-only oversight" },
] as const;

/** Dev sign-in is opt-in and must never be enabled on a production deployment. */
const DEV_SIGNIN_ENABLED = process.env.NEXT_PUBLIC_DEV_SIGNIN === "true";

export default function SignInPage() {
  const [role, setRole] = useState<string>("ura_staff");
  const [devToken, setDevToken] = useState("");
  const [status, setStatus] = useState<{ kind: "idle" | "error" | "ok"; message: string }>({
    kind: "idle",
    message: "",
  });
  // Read the token as an external store rather than copying it into state on
  // mount. It also means signing out updates this without a manual setState.
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const signedIn = Boolean(token);

  const startOidc = useCallback(async () => {
    if (!OIDC_CONFIGURED) return;
    try {
      await beginOidcFlow({ mode: "signin" });
      if (isEmbedded()) {
        // See the signup page: framed, the flow moves to a new top-level tab.
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
  //
  // beginOidcFlow opens `?continue=signin` in a new top-level tab when it is
  // framed, because identity providers refuse to render in a frame. Without
  // this the person would have to press the same button a second time in a tab
  // they did not ask for, which reads as the first press having failed.
  //
  // Guarded on not being embedded, so a framed page carrying the parameter
  // cannot loop itself opening tabs.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isEmbedded() || !OIDC_CONFIGURED) return;
    if (new URLSearchParams(window.location.search).get("continue") !== "signin") return;
    // queueMicrotask, not a bare call: startOidc sets its pending state before
    // its first await, and doing that synchronously inside an effect cascades a
    // render. Deferring past commit avoids the cascade rather than suppressing
    // the warning about it.
    queueMicrotask(() => void startOidc());
    // Once only: the parameter is stripped so a reload does not redirect again.
    window.history.replaceState({}, "", window.location.pathname);
  }, [startOidc]);

  const useDevToken = useCallback(async () => {
    const token = devToken.trim();
    if (!token) {
      setStatus({ kind: "error", message: "Paste a token first." });
      return;
    }
    setAuthToken(token);
    // Prove the token is actually accepted before sending anyone to a dashboard
    // that would just render empty panels.
    try {
      const res = await fetch("/api/v1/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = await res.json();
      if (!res.ok || !body?.authenticated) {
        clearAuthToken();
        setStatus({
          kind: "error",
          message: "The backend rejected that token. Check it was minted with this deployment's AUTH_DEV_SECRET.",
        });
        return;
      }
      // No setSignedIn: setAuthToken above already notified the token store.
      const staff = isStaffRole(body.role);
      setStatus({
        kind: "ok",
        message: staff
          ? `Signed in as ${body.email || body.external_id || "staff"} (${body.role}).`
          : `Token accepted, but role "${body.role}" is not staff — the dashboards will refuse it.`,
      });
    } catch (err) {
      clearAuthToken();
      setStatus({ kind: "error", message: `Could not reach the backend: ${(err as Error).message}` });
    }
  }, [devToken]);

  const signOut = useCallback(() => {
    clearAuthToken();
    setDevToken("");
    setStatus({ kind: "idle", message: "Signed out." });
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
            Accounts, passwords and recovery are managed by the identity
            provider — not by this application.
          </p>
          <button
            type="button"
            className="signin-primary"
            onClick={startOidc}
            disabled={!OIDC_CONFIGURED}
          >
            {OIDC_CONFIGURED ? "Continue with URA identity provider" : "Identity provider not configured"}
          </button>
          {!OIDC_CONFIGURED && (
            <p className="signin-hint">
              Set <code>NEXT_PUBLIC_OIDC_ISSUER</code> and{" "}
              <code>NEXT_PUBLIC_OIDC_CLIENT_ID</code> to enable this.
            </p>
          )}
        </section>

        {DEV_SIGNIN_ENABLED && (
          <section className="signin-block signin-dev" aria-labelledby="dev-h">
            <div className="signin-dev-flag" role="note">
              Development access — not authentication
            </div>
            <h2 id="dev-h">Use a development token</h2>
            <p className="signin-note">
              For exploring the dashboards where no identity provider is
              configured. Anyone with the shared secret can mint one, so it
              proves nothing about who you are. The backend refuses to mint
              these when <code>APP_ENV=production</code>.
            </p>

            <fieldset className="signin-roles">
              <legend>Role to request</legend>
              {DEV_ROLE_OPTIONS.map((r) => (
                <label key={r.role} className={role === r.role ? "role-opt active" : "role-opt"}>
                  <input
                    type="radio"
                    name="role"
                    value={r.role}
                    checked={role === r.role}
                    onChange={() => setRole(r.role)}
                  />
                  <span className="role-name">{r.label}</span>
                  <span className="role-hint">{r.hint}</span>
                </label>
              ))}
            </fieldset>

            <p className="signin-hint">
              Mint one on the backend host, then paste it below:
              <code className="signin-cmd">
                python -c &quot;from app.auth.jwt_auth import make_dev_token;
                print(make_dev_token(&apos;dev-user&apos;, role=&apos;{role}&apos;))&quot;
              </code>
            </p>

            <label className="signin-field">
              <span>Token</span>
              <textarea
                value={devToken}
                onChange={(e) => setDevToken(e.target.value)}
                placeholder="eyJhbGciOiJIUzI1NiIs..."
                rows={3}
                spellCheck={false}
                autoComplete="off"
              />
            </label>
            <button type="button" className="signin-secondary" onClick={useDevToken}>
              Verify and continue
            </button>
          </section>
        )}

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
            <a href="/admin">Operations overview</a>
            <a href="/agent">My queue</a>
            <a href="/analytics">Analytics</a>
            <button type="button" className="signin-link" onClick={signOut}>
              Sign out
            </button>
          </nav>
        )}

        {/* Both directions out of this page: register with the provider, or go
            back to the assistant, which needs no account at all. */}
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
