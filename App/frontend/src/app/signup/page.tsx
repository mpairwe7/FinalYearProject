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
import { useRouter } from "next/navigation";
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
    title: "URA employees & staff",
    body: "Staff, admin, and auditor roles are predefined and assigned by URA Administration. Staff accounts are not registered here — sign in directly with your official credentials.",
  },
] as const;

interface DerivedCredential {
  saltHex: string;
  hashHex: string;
}

async function deriveCredential(pwd: string, saltBytes?: Uint8Array): Promise<DerivedCredential> {
  if (typeof window === "undefined" || !window.crypto?.subtle) {
    throw new Error("Web Crypto API is required for secure authentication.");
  }
  const salt = saltBytes || window.crypto.getRandomValues(new Uint8Array(16));
  const enc = new TextEncoder();
  const keyMaterial = await window.crypto.subtle.importKey(
    "raw",
    enc.encode(pwd),
    { name: "PBKDF2" },
    false,
    ["deriveBits"],
  );
  const derived = await window.crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      salt: salt as unknown as BufferSource,
      iterations: 100_000,
      hash: "SHA-256",
    },
    keyMaterial,
    256,
  );
  const toHex = (buf: Uint8Array) => Array.from(buf).map((b) => b.toString(16).padStart(2, "0")).join("");
  return {
    saltHex: toHex(salt),
    hashHex: toHex(new Uint8Array(derived)),
  };
}

export default function SignUpPage() {
  const router = useRouter();
  const [status, setStatus] = useState<{ kind: "idle" | "info" | "error"; message: string }>({
    kind: "idle",
    message: "",
  });
  const [starting, setStarting] = useState(false);
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [createdRole, setCreatedRole] = useState<string | null>(null);

  // Someone who already has a token does not need this page; say so instead of
  // starting a second flow that would just replace a working session.
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);

  const handleLocalSignUp = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const userEmail = email.trim();
      const userName = fullName.trim() || (userEmail ? userEmail.split("@")[0] : "taxpayer");
      if (!userEmail) {
        setStatus({ kind: "error", message: "Please enter your email address." });
        return;
      }
      if (!password || password.length < 6) {
        setStatus({ kind: "error", message: "Password must be at least 6 characters long." });
        return;
      }
      if (confirmPassword && password !== confirmPassword) {
        setStatus({ kind: "error", message: "Passwords do not match." });
        return;
      }
      setSubmitting(true);
      setStatus({ kind: "idle", message: "" });
      try {
        const res = await fetch("/api/v1/auth/dev-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: "public",
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

        // Derive salted PBKDF2 credential hash (CWE-287 / CWE-312: random salt, work-factor KDF, fail-closed)
        if (typeof window !== "undefined") {
          try {
            const derived = await deriveCredential(password);
            localStorage.setItem(
              `taxpayer_cred_${userEmail.toLowerCase()}`,
              JSON.stringify({
                fullName: userName,
                email: userEmail,
                saltHex: derived.saltHex,
                hashHex: derived.hashHex,
                role: "public",
              }),
            );
          } catch (cryptoErr) {
            setStatus({ kind: "error", message: (cryptoErr as Error).message });
            setSubmitting(false);
            return;
          }
        }

        setAuthToken(data.token, "dev");
        setCreatedRole(data.role);
        setSubmitting(false);
        setStatus({
          kind: "info",
          message: `Taxpayer account created for ${data.email || data.user_id}! Redirecting to sign in...`,
        });
        // Redirect user to sign-in screen with pre-filled credentials for a seamless onboarding UX
        const params = new URLSearchParams();
        params.set("registered", "true");
        params.set("email", userEmail);
        params.set("role", "public");
        setTimeout(() => {
          router.push(`/signin?${params.toString()}`);
        }, 800);
      } catch (err) {
        setSubmitting(false);
        setStatus({ kind: "error", message: `Could not complete registration: ${(err as Error).message}` });
      }
    },
    [email, fullName, password, confirmPassword, router],
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
            disabled={!OIDC_CONFIGURED || starting}
          >
            {!OIDC_CONFIGURED
              ? "Identity provider not configured"
              : starting
                ? "Opening the provider…"
                : "Continue to registration"}
          </button>
          {!OIDC_CONFIGURED && (
            <p className="signin-hint">
              Set <code>NEXT_PUBLIC_OIDC_ISSUER</code> and{" "}
              <code>NEXT_PUBLIC_OIDC_CLIENT_ID</code> to enable this. Until then
              the assistant still answers questions signed out.
            </p>
          )}
        </section>

        {!OIDC_CONFIGURED && (
          <section className="signin-block signin-dev" aria-labelledby="signup-local-h">
            <div className="signin-dev-flag" role="note">
              Taxpayer Account Creation
            </div>
            <h2 id="signup-local-h">Register Taxpayer Account</h2>
            <p className="signin-note">
              Create your account with your personal email (e.g. Gmail, Yahoo Mail, Outlook) to save tax conversations and customize your taxpayer profile:
            </p>

            <form onSubmit={handleLocalSignUp} style={{ display: "grid", gap: "12px" }}>
              <div className="signin-field">
                <label htmlFor="signup-fullname">
                  <span>Full Name</span>
                </label>
                <input
                  id="signup-fullname"
                  type="text"
                  className="signin-input"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="e.g. Ronald Kigozi"
                  autoComplete="name"
                />
              </div>

              <div className="signin-field">
                <label htmlFor="signup-email">
                  <span>Email Address</span>
                </label>
                <input
                  id="signup-email"
                  type="email"
                  className="signin-input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="e.g. yourname@gmail.com, yourname@yahoo.com"
                  required
                  aria-required="true"
                  autoComplete="email"
                  inputMode="email"
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </div>

              <div className="signin-field">
                <label htmlFor="signup-password">
                  <span>Create Password</span>
                </label>
                <div className="signin-input-wrap">
                  <input
                    id="signup-password"
                    type={showPassword ? "text" : "password"}
                    className="signin-input"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="At least 6 characters"
                    required
                    aria-required="true"
                    minLength={6}
                    autoComplete="new-password"
                    aria-describedby="signup-password-hint"
                  />
                  <button
                    type="button"
                    className="signin-pw-toggle"
                    onClick={() => setShowPassword((prev) => !prev)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    aria-pressed={showPassword}
                  >
                    {showPassword ? "🙈" : "👁️"}
                  </button>
                </div>
                <span id="signup-password-hint" className="signin-hint" style={{ marginTop: "3px" }}>
                  Must be at least 6 characters long
                </span>
              </div>

              <div className="signin-field">
                <label htmlFor="signup-confirm-password">
                  <span>Confirm Password</span>
                </label>
                <div className="signin-input-wrap">
                  <input
                    id="signup-confirm-password"
                    type={showConfirmPassword ? "text" : "password"}
                    className="signin-input"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-type your password"
                    required
                    aria-required="true"
                    minLength={6}
                    autoComplete="new-password"
                  />
                  <button
                    type="button"
                    className="signin-pw-toggle"
                    onClick={() => setShowConfirmPassword((prev) => !prev)}
                    aria-label={showConfirmPassword ? "Hide password confirmation" : "Show password confirmation"}
                    aria-pressed={showConfirmPassword}
                  >
                    {showConfirmPassword ? "🙈" : "👁️"}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                className="signin-primary"
                style={{ marginTop: "6px" }}
                disabled={submitting}
              >
                {submitting ? "Creating Account…" : "Create Taxpayer Account"}
              </button>
            </form>

            <div className="signin-staff-notice" style={{ marginTop: "16px", padding: "12px", background: "var(--surface-2)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-0)" }}>
              <div style={{ fontWeight: 600, fontSize: "12.5px", color: "var(--text-1)", marginBottom: "4px" }}>
                🏛️ URA Staff, Administrators & Auditors
              </div>
              <p style={{ margin: 0, fontSize: "12px", color: "var(--text-2)", lineHeight: 1.45 }}>
                Staff roles and access privileges are pre-assigned by URA Administration and do not register here.
                Official personnel should go directly to the sign-in portal.
              </p>
              <div style={{ marginTop: "8px" }}>
                <Link href="/signin" style={{ fontSize: "12.5px", fontWeight: 600, color: "var(--ura-blue-bright)" }}>
                  Sign in with predefined staff credentials →
                </Link>
              </div>
            </div>
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
