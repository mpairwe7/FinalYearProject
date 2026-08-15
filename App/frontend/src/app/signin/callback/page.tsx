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
import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { setAuthToken } from "../../../lib/authSession";
import { discoverOidc, TOKEN_ENDPOINT_KEY } from "../../../lib/oidc";
import {
  clearOidcFlowState,
  OIDC_CLIENT_ID,
  OIDC_ISSUER,
  OIDC_REDIRECT_PATH,
  OIDC_RETURN_TO_KEY,
  OIDC_STATE_KEY,
  PKCE_VERIFIER_KEY,
} from "../../../lib/oidcFlow";
import { isStaffRole, staffLandingPath } from "../../../lib/roles";
import "../signin.css";

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
    const expectedState = sessionStorage.getItem(OIDC_STATE_KEY);
    const verifier = sessionStorage.getItem(PKCE_VERIFIER_KEY);
    // Read before the flow state is cleared below: sign-up sets this so a
    // taxpayer lands back on the assistant instead of a dashboard they cannot open.
    const returnTo = sessionStorage.getItem(OIDC_RETURN_TO_KEY);

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
      // The sign-in leg stashes the discovered endpoint; discover again if this
      // page was reached without it (a bookmarked callback, a new tab).
      const tokenUrl =
        sessionStorage.getItem(TOKEN_ENDPOINT_KEY) ||
        (await discoverOidc(OIDC_ISSUER)).token_endpoint;
      const res = await fetch(tokenUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "authorization_code",
          client_id: OIDC_CLIENT_ID,
          code,
          redirect_uri: `${window.location.origin}${OIDC_REDIRECT_PATH}`,
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
      clearOidcFlowState();

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
      const staff = isStaffRole(me.role);
      setDetail(
        staff
          ? `Signed in as ${me.email || me.external_id} (${me.role}).`
          : `Signed in as ${me.email || me.external_id}. This account has no staff access — taking you to the assistant.`,
      );
      // Staff land on the tool their role can open; everyone else goes where the
      // flow started, which for sign-up is the assistant itself. A non-staff
      // account used to be left on this page with nowhere to go.
      const target = staff ? staffLandingPath(me.role) : returnTo || "/";
      window.setTimeout(() => window.location.replace(target), 900);
    } catch (err) {
      setPhase("error");
      setDetail(`Could not reach the identity provider: ${(err as Error).message}`);
    }
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // Deferred to a task rather than called inline: the validation branches
    // (missing code, state mismatch) set state synchronously, and doing that in
    // an effect body is a cascading render during mount. The values it reads —
    // the query string and the PKCE verifier — are only available client-side,
    // so this cannot move into render or an initialiser.
    const id = window.setTimeout(() => void exchange(), 0);
    return () => window.clearTimeout(id);
  }, [exchange]);

  return (
    <main className="signin-page">
      <div className="signin-card">
        <header className="signin-head">
          <div className="signin-mark" aria-hidden="true">
            URA
          </div>
          <h1>{phase === "working" ? "Signing you in" : phase === "done" ? "Signed in" : "Sign-in failed"}</h1>
          <p className="signin-sub">Uganda Revenue Authority — Tax Assistant</p>
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
            <Link href="/">The assistant</Link>
            <a href="/signin">Back to sign-in</a>
          </nav>
        )}
      </div>
    </main>
  );
}
