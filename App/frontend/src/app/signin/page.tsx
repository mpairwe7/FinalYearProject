"use client";

/**
 * Staff sign-in.
 *
 * The backend VERIFIES tokens; it does not issue them. There is no
 * credential store, no `/auth/login`, no `/signup` — `auth/jwt_auth.py` does
 * HS256 (dev shared secret) or RS256 against a remote JWKS. So this page cannot
 * be an email/password form: there is nothing to post to. It offers the two
 * paths that actually exist.
 *
 * 1. OIDC authorization-code redirect to the configured issuer. Registration and
 *    password recovery belong to that provider, which is why this page has no
 *    "create account" form of its own.
 * 2. A dev token, for exploring the dashboards where no IdP is configured. It is
 *    NOT authentication and says so on screen — the backend's `make_dev_token`
 *    refuses to run under APP_ENV=production, and this panel hides itself unless
 *    the deployment opts in.
 *
 * Standards: OAuth 2.1 authorization-code + PKCE (draft-ietf-oauth-v2-1),
 * OIDC Core 1.0 §3.1; WCAG 2.2 AA for the form semantics.
 */
import React, { useCallback, useMemo, useState, useSyncExternalStore } from "react";
import {
  setAuthToken,
  getAuthToken,
  clearAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../../lib/authSession";
import { discoverOidc, TOKEN_ENDPOINT_KEY } from "../../lib/oidc";
import "./signin.css";

/** Roles the backend treats as staff — `AuthUser.is_staff` in auth/models.py. */
const STAFF_ROLES = [
  { role: "ura_staff", label: "Tax agent", hint: "Works the escalation queue" },
  { role: "ura_admin", label: "Administrator", hint: "Full operations view" },
  { role: "ura_auditor", label: "Auditor", hint: "Read-only oversight" },
] as const;

const OIDC_ISSUER = (process.env.NEXT_PUBLIC_OIDC_ISSUER || "").trim();
const OIDC_CLIENT_ID = (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID || "").trim();
const OIDC_SCOPE = (process.env.NEXT_PUBLIC_OIDC_SCOPE || "openid profile email").trim();
/**
 * Optional `audience`. Some providers only issue a verifiable JWT access token
 * when the request names an API audience — Auth0 returns an OPAQUE token
 * without it, which the backend cannot verify and rejects as a malformed token.
 * Keycloak needs nothing here (its audience mapper handles it), so this stays
 * empty unless a deployment requires it.
 */
const OIDC_AUDIENCE = (process.env.NEXT_PUBLIC_OIDC_AUDIENCE || "").trim();
/** Dev sign-in is opt-in and must never be enabled on a production deployment. */
const DEV_SIGNIN_ENABLED = process.env.NEXT_PUBLIC_DEV_SIGNIN === "true";

function randomVerifier(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

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

  const oidcConfigured = useMemo(
    () => Boolean(OIDC_ISSUER && OIDC_CLIENT_ID),
    [],
  );

  const startOidc = useCallback(async () => {
    if (!oidcConfigured) return;
    try {
      // Ask the provider where its endpoints are rather than assuming a vendor's
      // URL layout; every provider publishes this and they all differ.
      const endpoints = await discoverOidc(OIDC_ISSUER);

      const verifier = randomVerifier();
      const challenge = await pkceChallenge(verifier);
      // Held for the callback leg; sessionStorage so it dies with the tab.
      sessionStorage.setItem("ura_pkce_verifier", verifier);
      const state = randomVerifier().slice(0, 24);
      sessionStorage.setItem("ura_oidc_state", state);
      // Carry the token endpoint over so the callback does not have to discover
      // again; it re-discovers if this is missing.
      sessionStorage.setItem(TOKEN_ENDPOINT_KEY, endpoints.token_endpoint);

      const url = new URL(endpoints.authorization_endpoint);
      url.searchParams.set("client_id", OIDC_CLIENT_ID);
      url.searchParams.set("response_type", "code");
      url.searchParams.set("scope", OIDC_SCOPE);
      url.searchParams.set("redirect_uri", `${window.location.origin}/signin/callback`);
      url.searchParams.set("state", state);
      url.searchParams.set("code_challenge", challenge);
      url.searchParams.set("code_challenge_method", "S256");
      if (OIDC_AUDIENCE) url.searchParams.set("audience", OIDC_AUDIENCE);
      window.location.assign(url.toString());
    } catch (err) {
      setStatus({
        kind: "error",
        message: `Could not start the sign-in redirect: ${(err as Error).message}`,
      });
    }
  }, [oidcConfigured]);

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
      const staff = ["ura_staff", "ura_admin", "ura_auditor"].includes(body.role);
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
          <h1>Staff sign-in</h1>
          <p className="signin-sub">
            Uganda Revenue Authority — Tax Assistant operations
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
            disabled={!oidcConfigured}
          >
            {oidcConfigured ? "Continue with URA identity provider" : "Identity provider not configured"}
          </button>
          {!oidcConfigured && (
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
              {STAFF_ROLES.map((r) => (
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
      </div>
    </main>
  );
}
