"use client";

/**
 * OIDC callback — the return leg of the authorization-code redirect.
 *
 * Completes the exchange in the browser using the PKCE verifier the sign-in page
 * stashed in sessionStorage. That is the public-client flow (OAuth 2.1 §4.1,
 * PKCE S256): there is no client secret here, and there must not be — this is a
 * static bundle any user can read. The backend never issues tokens, so it has no
 * token-exchange endpoint to proxy through.
 *
 * The token lands in the same store the API client already reads
 * (`lib/authSession`), so every existing hook picks it up with no change.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { setAuthToken } from "../../../lib/authSession";
import "../signin.css";

const OIDC_ISSUER = process.env.NEXT_PUBLIC_OIDC_ISSUER || "";
const OIDC_CLIENT_ID = process.env.NEXT_PUBLIC_OIDC_CLIENT_ID || "";

type Phase = "working" | "error" | "done";

export default function OidcCallbackPage() {
  const [phase, setPhase] = useState<Phase>("working");
  const [detail, setDetail] = useState("Completing sign-in…");
  const [role, setRole] = useState("");
  // A redirect callback must run its exchange exactly once; React 18 StrictMode
  // mounts effects twice in development and the code is single-use.
  const started = useRef(false);

  const exchange = useCallback(async () => {
    const params = new URLSearchParams(window.location.search);
    const error = params.get("error");
    if (error) {
      setPhase("error");
      setDetail(`${error}: ${params.get("error_description") || "the provider refused the request"}`);
      return;
    }

    const code = params.get("code");
    const state = params.get("state");
    const expectedState = sessionStorage.getItem("ura_oidc_state");
    const verifier = sessionStorage.getItem("ura_pkce_verifier");

    if (!code) {
      setPhase("error");
      setDetail("The provider returned no authorization code.");
      return;
    }
    // CSRF defence: a code arriving without the state we generated is not ours.
    if (!state || !expectedState || state !== expectedState) {
      setPhase("error");
      setDetail("State mismatch — this sign-in did not start in this browser tab. Start again.");
      return;
    }
    if (!verifier) {
      setPhase("error");
      setDetail("The PKCE verifier is missing. Start the sign-in again.");
      return;
    }

    try {
      const tokenUrl = `${OIDC_ISSUER.replace(/\/$/, "")}/protocol/openid-connect/token`;
      const res = await fetch(tokenUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: OIDC_CLIENT_ID,
          code,
          redirect_uri: `${window.location.origin}/signin/callback`,
          code_verifier: verifier,
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.access_token) {
        setPhase("error");
        setDetail(
          body.error_description || body.error || `The provider rejected the exchange (HTTP ${res.status}).`,
        );
        return;
      }

      setAuthToken(body.access_token);
      sessionStorage.removeItem("ura_pkce_verifier");
      sessionStorage.removeItem("ura_oidc_state");

      // Confirm the backend accepts it, and find out which role we hold, before
      // sending anyone to a dashboard that would refuse them.
      const me = await fetch("/api/v1/me", {
        headers: { Authorization: `Bearer ${body.access_token}` },
      }).then((r) => r.json());

      if (!me?.authenticated) {
        setPhase("error");
        setDetail("Signed in with the provider, but this application did not accept the token.");
        return;
      }
      setRole(me.role || "");
      setPhase("done");
      const staff = ["ura_staff", "ura_admin", "ura_auditor"].includes(me.role);
      setDetail(
        staff
          ? `Signed in as ${me.email || me.external_id} (${me.role}).`
          : `Signed in as ${me.email || me.external_id}, but role "${me.role}" has no staff access.`,
      );
      if (staff) {
        // ura_staff works the queue; admin and auditor land on the overview.
        const target = me.role === "ura_staff" ? "/agent" : "/admin";
        window.setTimeout(() => window.location.replace(target), 900);
      }
    } catch (err) {
      setPhase("error");
      setDetail(`Could not reach the identity provider: ${(err as Error).message}`);
    }
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void exchange();
  }, [exchange]);

  return (
    <main className="signin-page">
      <div className="signin-card">
        <header className="signin-head">
          <div className="signin-mark" aria-hidden="true">
            URA
          </div>
          <h1>{phase === "working" ? "Signing you in" : phase === "done" ? "Signed in" : "Sign-in failed"}</h1>
          <p className="signin-sub">Uganda Revenue Authority — Tax Assistant operations</p>
        </header>

        <p
          className={`signin-status ${phase === "error" ? "error" : phase === "done" ? "ok" : ""}`}
          role={phase === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          {detail}
        </p>

        {phase !== "working" && (
          <nav className="signin-onward" aria-label="Continue to">
            {role === "ura_staff" && <a href="/agent">My queue</a>}
            {(role === "ura_admin" || role === "ura_auditor") && <a href="/admin">Operations overview</a>}
            <a href="/signin">Back to sign-in</a>
          </nav>
        )}
      </div>
    </main>
  );
}
