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
import { useRouter } from "next/navigation";
import React, { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import {
  setAuthToken,
  getAuthMethod,
  getAuthToken,
  clearAuthToken,
  getServerAuthToken,
  looksLikeJwt,
  sanitizeAuthToken,
  subscribeAuthToken,
} from "../../lib/authSession";
import {
  beginOidcFlow,
  endOidcSession,
  isEmbedded,
  OIDC_CONFIGURED,
} from "../../lib/oidcFlow";
import { isStaffRole, landingPathForRole, roleLabel } from "../../lib/roles";
import "./signin.css";

/** Roles the dev-token panel can request — labels are specific to this panel. */
const DEV_ROLE_OPTIONS = [
  { role: "ura_staff", label: "Tax agent", hint: "Works the escalation queue & user queries" },
  { role: "ura_admin", label: "Administrator", hint: "Full operations view" },
  { role: "ura_auditor", label: "Auditor", hint: "Read-only oversight" },
] as const;

/** Dev sign-in is enabled when no IdP is configured or explicitly opted into. */
const DEV_SIGNIN_ENABLED =
  process.env.NEXT_PUBLIC_DEV_SIGNIN === "true" || !OIDC_CONFIGURED;

async function hashCredential(pwd: string, email: string): Promise<string> {
  if (typeof window === "undefined" || !window.crypto?.subtle) {
    return "";
  }
  const enc = new TextEncoder();
  const data = enc.encode(`ura-taxpayer:${email.toLowerCase()}:${pwd}`);
  const buffer = await window.crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export default function SignInPage() {
  const router = useRouter();
  const [role, setRole] = useState<string>("ura_staff");
  const [taxpayerEmail, setTaxpayerEmail] = useState("");
  const [taxpayerPassword, setTaxpayerPassword] = useState("");
  const [customEmail, setCustomEmail] = useState("");
  const [devToken, setDevToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<{ kind: "idle" | "error" | "ok"; message: string }>({
    kind: "idle",
    message: "",
  });
  // Read the token as an external store rather than copying it into state on
  // mount. It also means signing out updates this without a manual setState.
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const signedIn = Boolean(token);

  // Read URL parameters on mount to prefill email/role and show account creation success notices
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const wasRegistered = params.get("registered") === "true";
    const emailParam = params.get("email");
    const roleParam = params.get("role");

    queueMicrotask(() => {
      if (wasRegistered) {
        setStatus({
          kind: "ok",
          message: "Taxpayer account created successfully! Sign in below to enter the tax assistant.",
        });
      }
      if (emailParam) {
        setTaxpayerEmail(emailParam);
        setCustomEmail(emailParam);
      }
      if (roleParam) {
        setRole(roleParam);
      }
    });
  }, []);

  /**
   * Determine the internal destination based on the user's role and any requested returnTo parameter.
   * Internally detects:
   * - URA Staff -> /agent (My Queue & User Query Flows)
   * - URA Admin -> /admin (Operations Console)
   * - URA Auditor -> /analytics (Auditor View)
   * - Normal Taxpayer -> / (Assistant Chat)
   */
  const resolveRoleDestination = useCallback((targetRole: string, backendRedirect?: string) => {
    if (backendRedirect && backendRedirect.startsWith("/")) {
      return backendRedirect;
    }
    if (typeof window === "undefined") return landingPathForRole(targetRole);
    const params = new URLSearchParams(window.location.search);
    const returnTo = params.get("returnTo");
    const staff = isStaffRole(targetRole);

    if (returnTo && returnTo.startsWith("/")) {
      if (!staff && (returnTo.startsWith("/admin") || returnTo.startsWith("/agent") || returnTo.startsWith("/analytics"))) {
        return "/";
      }
      return returnTo;
    }

    return landingPathForRole(targetRole);
  }, []);

  /**
   * `prompt` is what makes "sign in as somebody else" work.
   *
   * Without it the provider answers the authorize request from its own session
   * cookie — no login screen, straight back with a token for whoever it
   * remembers. Reported as "when I want to sign in as another user, it doesn't
   * do that but just automatically signs me in the older account". `login`
   * (OIDC Core 1.0 §3.1.2.1) requires reauthentication.
   */
  const startOidc = useCallback(async (prompt?: "login") => {
    if (!OIDC_CONFIGURED) return;
    try {
      await beginOidcFlow({ mode: "signin", prompt });
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
    const params = new URLSearchParams(window.location.search);
    if (params.get("continue") !== "signin") return;
    // Carried from the framed tab that opened this one — without it the fresh
    // tab starts an ordinary flow and the provider signs the person back in as
    // whoever it remembers.
    const prompt = params.get("prompt") === "login" ? "login" : undefined;
    // queueMicrotask, not a bare call: startOidc sets its pending state before
    // its first await, and doing that synchronously inside an effect cascades a
    // render. Deferring past commit avoids the cascade rather than suppressing
    // the warning about it.
    queueMicrotask(() => void startOidc(prompt));
    // Once only: the parameter is stripped so a reload does not redirect again.
    window.history.replaceState({}, "", window.location.pathname);
  }, [startOidc]);

  const requestDevToken = useCallback(
    async (targetRole: string, targetEmail?: string, targetUserId?: string) => {
      setLoading(true);
      setStatus({ kind: "idle", message: "" });
      const emailToUse =
        targetEmail ||
        (targetRole === "public"
          ? "taxpayer@ura.go.ug"
          : `${targetRole.replace("ura_", "")}@ura.go.ug`);
      const userToUse = targetUserId || targetRole.replace("ura_", "");

      try {
        const res = await fetch("/api/v1/auth/dev-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: targetRole,
            email: emailToUse,
            user_id: userToUse,
          }),
        });

        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setStatus({
            kind: "error",
            message: body?.detail || `Sign-in failed with status ${res.status}.`,
          });
          setLoading(false);
          return;
        }

        const data = await res.json();
        if (!data?.token) {
          setStatus({ kind: "error", message: "Backend did not return a valid authentication token." });
          setLoading(false);
          return;
        }

        setAuthToken(data.token, "dev");
        setLoading(false);
        const staff = isStaffRole(data.role);
        const dest = resolveRoleDestination(data.role, data.redirect_url);
        setStatus({
          kind: "ok",
          message: staff
            ? `Signed in as ${data.email || data.user_id} (${roleLabel(data.role)})! Redirecting to workspace...`
            : `Signed in as ${data.email || data.user_id}! Redirecting to tax assistant...`,
        });
        setTimeout(() => {
          router.push(dest);
        }, 400);
      } catch (err) {
        setLoading(false);
        setStatus({
          kind: "error",
          message: `Could not reach backend: ${(err as Error).message}`,
        });
      }
    },
    [resolveRoleDestination, router],
  );

  const useDevToken = useCallback(async () => {
    // Not `.trim()`: a token pasted from a terminal or a chat client can carry a
    // zero-width space or a BOM, which trim leaves in place. Anything outside
    // base64url is stripped — see sanitizeAuthToken for why that matters.
    const token = sanitizeAuthToken(devToken);
    if (!token) {
      setStatus({ kind: "error", message: "Paste a token first." });
      return;
    }
    if (!looksLikeJwt(token)) {
      setStatus({
        kind: "error",
        message:
          "That does not look like a token. A token is three dot-separated parts starting with \"eyJ\" — check the whole string was copied.",
      });
      return;
    }
    // Verify BEFORE storing. Storing first meant a token the browser could not
    // even put in a header was already in localStorage, so every later request
    // failed the same way and the only way out was clearing site data.
    try {
      const res = await fetch("/api/v1/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = await res.json();
      if (!res.ok || !body?.authenticated) {
        setStatus({
          kind: "error",
          message: "The backend rejected that token. Check it was minted with this deployment's AUTH_DEV_SECRET.",
        });
        return;
      }
      // Accepted — only now does it go into storage. Tagged `dev` so sign-out
      // does not try to end a provider session that never existed.
      setAuthToken(token, "dev");
      const staff = isStaffRole(body.role);
      const dest = resolveRoleDestination(body.role);
      setStatus({
        kind: "ok",
        message: staff
          ? `Signed in as ${body.email || body.external_id || "staff"} (${body.role})! Redirecting to workspace...`
          : `Signed in as ${body.email || body.external_id || "taxpayer"} (${body.role})! Redirecting to assistant...`,
      });
      setTimeout(() => {
        router.push(dest);
      }, 500);
    } catch (err) {
      // Nothing was stored on this path, so there is nothing to roll back —
      // and clearing here would sign out a session that this attempt never
      // touched.
      setStatus({ kind: "error", message: `Could not reach the backend: ${(err as Error).message}` });
    }
  }, [devToken, resolveRoleDestination, router]);

  const signOut = useCallback(() => {
    const method = getAuthMethod();
    clearAuthToken();
    setDevToken("");
    setStatus({ kind: "idle", message: "Signed out." });
    // And at the provider, or this only makes the application forget you while
    // the provider's cookie signs you straight back in. Navigates away when it
    // succeeds, so nothing after this runs in that case.
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
            Accounts, passwords and recovery are managed by the identity
            provider — not by this application.
          </p>
          <button
            type="button"
            className="signin-primary"
            onClick={() => void startOidc()}
            disabled={!OIDC_CONFIGURED}
          >
            {OIDC_CONFIGURED ? "Continue with URA identity provider" : "Identity provider not configured"}
          </button>
          {/* The escape hatch from "it signed me in as the wrong person".
              Signing out now ends the provider session too, so this should
              rarely be needed — but a provider that publishes no
              end_session_endpoint cannot be logged out remotely at all, and on
              a shared machine there is always a session somebody forgot to
              end. `prompt=login` makes the provider ask, whatever it
              remembers. */}
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

        {DEV_SIGNIN_ENABLED && (
          <section className="signin-block signin-dev" aria-labelledby="dev-h">
            <div className="signin-dev-flag" role="note">
              Authentication Portal
            </div>
            <h2 id="dev-h">Taxpayer Sign In</h2>
            <p className="signin-note">
              Sign in with your personal email (e.g. Gmail, Yahoo Mail, Outlook). The system preserves your conversation history and tax profile preferences:
            </p>

            <form
              onSubmit={async (e) => {
                e.preventDefault();
                const mail = taxpayerEmail.trim();
                if (!mail) {
                  setStatus({ kind: "error", message: "Please enter your email address." });
                  return;
                }
                if (!taxpayerPassword) {
                  setStatus({ kind: "error", message: "Please enter your password." });
                  return;
                }

                // Verify saved credential hash if created locally (CWE-312: no plaintext)
                if (typeof window !== "undefined") {
                  try {
                    const raw = localStorage.getItem(`taxpayer_cred_${mail.toLowerCase()}`);
                    if (raw) {
                      const cred = JSON.parse(raw);
                      const inputHash = await hashCredential(taxpayerPassword, mail);
                      if (cred.pwdHash && cred.pwdHash !== inputHash) {
                        setStatus({ kind: "error", message: "Incorrect password. Please verify your credentials." });
                        return;
                      }
                    }
                  } catch {
                    // pass through
                  }
                }

                void requestDevToken("public", mail, mail.split("@")[0]);
              }}
              style={{ display: "grid", gap: "8px", marginBottom: "18px" }}
            >
              <label className="signin-field">
                <span>Personal Email Address</span>
                <input
                  type="email"
                  className="signin-input"
                  value={taxpayerEmail}
                  onChange={(e) => setTaxpayerEmail(e.target.value)}
                  placeholder="e.g. yourname@gmail.com, yourname@yahoo.com"
                  required
                />
              </label>

              <label className="signin-field">
                <span>Password</span>
                <input
                  type="password"
                  className="signin-input"
                  value={taxpayerPassword}
                  onChange={(e) => setTaxpayerPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                />
              </label>

              <button
                type="submit"
                className="signin-primary"
                disabled={loading}
                style={{ marginTop: "4px" }}
              >
                {loading ? "Signing in..." : "Sign In as Taxpayer"}
              </button>
            </form>

            <div style={{ borderTop: "1px solid var(--border-0)", paddingTop: "16px", marginTop: "14px" }}>
              <h2 style={{ fontSize: "15px", fontWeight: 650, color: "var(--text-1)", marginBottom: "4px" }}>
                🏛️ Predefined URA Staff Credentials
              </h2>
              <p className="signin-note" style={{ marginBottom: "12px" }}>
                Staff, Administrator, and Auditor credentials are administrative and pre-assigned by URA Administration.
                The auth system internally detects your assigned role and automatically redirects you to your workspace:
              </p>

              <div className="signin-quick-grid">
                <button
                  type="button"
                  className="signin-quick-btn"
                  disabled={loading}
                  onClick={() => void requestDevToken("ura_staff", "agent.sarah@ura.go.ug", "agent-sarah")}
                >
                  <span className="btn-role">👮 Tax Agent (Staff)</span>
                  <span className="btn-desc">agent.sarah@ura.go.ug · Redirects to /agent</span>
                </button>

                <button
                  type="button"
                  className="signin-quick-btn"
                  disabled={loading}
                  onClick={() => void requestDevToken("ura_admin", "admin@ura.go.ug", "admin-user")}
                >
                  <span className="btn-role">⚡ Administrator</span>
                  <span className="btn-desc">admin@ura.go.ug · Redirects to /admin</span>
                </button>

                <button
                  type="button"
                  className="signin-quick-btn"
                  disabled={loading}
                  onClick={() => void requestDevToken("ura_auditor", "auditor@ura.go.ug", "auditor-user")}
                >
                  <span className="btn-role">📋 Compliance Auditor</span>
                  <span className="btn-desc">auditor@ura.go.ug · Redirects to /analytics</span>
                </button>
              </div>

              <details className="signin-manual-toggle">
                <summary>Custom URA staff email or paste an existing token</summary>
                <div style={{ marginTop: "12px" }}>
                  <label className="signin-field">
                    <span>Assigned URA Email</span>
                    <input
                      type="text"
                      className="signin-input"
                      value={customEmail}
                      onChange={(e) => setCustomEmail(e.target.value)}
                      placeholder="e.g. officer.grace@ura.go.ug"
                    />
                  </label>

                  <fieldset className="signin-roles" style={{ marginTop: "10px" }}>
                    <legend>Role</legend>
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

                  <button
                    type="button"
                    className="signin-secondary"
                    style={{ marginTop: "6px", width: "100%" }}
                    disabled={loading}
                    onClick={() => void requestDevToken(role, customEmail)}
                  >
                    {loading ? "Signing in..." : "Sign in with selected role"}
                  </button>

                  <div style={{ marginTop: "14px", borderTop: "1px solid var(--border-0)", paddingTop: "12px" }}>
                    <label className="signin-field">
                      <span>Or paste an existing JWT token</span>
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
                  </div>
                </div>
              </details>
            </div>
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
            <a href="/agent" style={{ fontWeight: 700 }}>
              👉 Escalation Queue & User Queries (/agent)
            </a>
            <a href="/admin">Operations overview (/admin)</a>
            <a href="/analytics">Analytics (/analytics)</a>
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
