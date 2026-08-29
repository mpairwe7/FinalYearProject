/**
 * Starting an OIDC authorization-code + PKCE redirect.
 *
 * One builder serves both entry points, because sign-in and sign-up are the
 * same OAuth request with one extra parameter. The backend verifies tokens and
 * issues none (`auth/jwt_auth.py`), so there is no local credential store to
 * register against: creating an account means sending the person to the
 * provider's own registration screen and coming back with a token.
 *
 * Registration is requested two ways on purpose:
 *   - `prompt=create` — "Initiating User Registration via OpenID Connect 1.0",
 *     the standard signal, understood by Entra ID, Google and Curity.
 *   - `screen_hint=signup` — Auth0's equivalent, which predates that spec and
 *     is what our own deployment (an Auth0 tenant) actually reads.
 * Providers ignore parameters they do not know, so sending both is safe and
 * means the flow works on either kind of tenant without a build-time switch.
 *
 * Standards: OAuth 2.1 §4.1 public client with PKCE S256 (RFC 7636),
 * OIDC Core 1.0 §3.1, OIDC Discovery 1.0 §4.
 */

import { discoverOidc, END_SESSION_ENDPOINT_KEY, TOKEN_ENDPOINT_KEY } from "./oidc";

export const OIDC_ISSUER = (process.env.NEXT_PUBLIC_OIDC_ISSUER || "").trim();
export const OIDC_CLIENT_ID = (process.env.NEXT_PUBLIC_OIDC_CLIENT_ID || "").trim();
const OIDC_SCOPE = (process.env.NEXT_PUBLIC_OIDC_SCOPE || "openid profile email").trim();
/**
 * Optional `audience`. Some providers only issue a verifiable JWT access token
 * when the request names an API audience — Auth0 returns an OPAQUE token
 * without it, which the backend cannot verify and rejects as malformed.
 * Keycloak needs nothing here (its audience mapper handles it).
 */
const OIDC_AUDIENCE = (process.env.NEXT_PUBLIC_OIDC_AUDIENCE || "").trim();

/** Both entry points return through this one route. */
export const OIDC_REDIRECT_PATH = "/signin/callback";

export const PKCE_VERIFIER_KEY = "ura_pkce_verifier";
export const OIDC_STATE_KEY = "ura_oidc_state";
/** Where to send the browser once the exchange succeeds (see the callback). */
export const OIDC_RETURN_TO_KEY = "ura_oidc_return_to";

/** True when this deployment has an identity provider configured. */
export const OIDC_CONFIGURED = Boolean(OIDC_ISSUER && OIDC_CLIENT_ID);

type AuthorizeMode = "signin" | "signup";

/**
 * Where the browser lands after the provider has ended its session.
 *
 * `/signin` by default, rather than the assistant: the only reason to log out
 * of the provider is to sign in as somebody else, and finishing on a page with
 * no sign-in control makes the person hunt for one.
 *
 * Configurable because this URL has to match a value **registered at the
 * provider** — Auth0's *Allowed Logout URLs*, Keycloak's *Valid post logout
 * redirect URIs* — and those are matched exactly, not by prefix. Our own two
 * deployments are registered differently: the HF Space as
 * `https://…hf.space/signin`, the ngrok tunnel as the bare origin with no
 * path. An unregistered value does not fail quietly; the provider refuses the
 * logout and shows its own error page instead of signing anyone out.
 *
 * Empty (or `/`) means the origin itself. Anything else is normalised to a
 * single leading slash so `signin`, `/signin` and `/signin/` cannot produce
 * three different URLs, only one of which is registered.
 */
function normalisePostLogoutPath(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed || trimmed === "/") return "";
  const withSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  return withSlash.endsWith("/") ? withSlash.slice(0, -1) : withSlash;
}

export const OIDC_POST_LOGOUT_PATH = normalisePostLogoutPath(
  process.env.NEXT_PUBLIC_OIDC_POST_LOGOUT_PATH ?? "/signin",
);

function randomHex(bytes = 32): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

interface BeginOidcOptions {
  /** `signup` asks the provider for its registration screen. */
  mode?: AuthorizeMode;
  /**
   * Force the provider to re-authenticate instead of reusing its session.
   *
   * This is the fix for "when I want to sign in as another user, it just
   * automatically signs me in the older account". Signing out cleared the
   * token in this browser and nothing else — the provider's own session
   * cookie survived, so the next authorize request was answered silently from
   * it, with no login screen and the previous account's identity.
   *
   * `prompt=login` (OIDC Core 1.0 §3.1.2.1) requires reauthentication.
   * `select_account` is the gentler form, offering a chooser when the
   * provider has several sessions; providers that do not support it are
   * required to fall through rather than fail, and every major one treats
   * `login` as the stronger guarantee. `login` is what "sign in as someone
   * else" actually means, so that is the default here.
   */
  prompt?: "login" | "select_account";
  /**
   * Path to land on after a successful exchange, for people who are not staff
   * and have no dashboard to be sent to (a taxpayer who signed up from the
   * assistant should come back to the assistant).
   */
  returnTo?: string;
}

/**
 * Build the authorization URL and navigate to it.
 *
 * Throws rather than navigating when the provider cannot be discovered, so the
 * caller can show why instead of sending the browser to a URL built from
 * guesses. The PKCE verifier and CSRF state go to sessionStorage: they must
 * survive the redirect but die with the tab.
 */
/**
 * True when this document is inside a frame it cannot navigate out of.
 *
 * Reading `window.top.location` throws cross-origin, which is exactly the
 * embedded case; a same-origin frame would read fine and does not matter here.
 */
export function isEmbedded(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.self !== window.top;
  } catch {
    return true; // cross-origin access threw — definitely framed
  }
}

export async function beginOidcFlow({
  mode = "signin",
  returnTo,
  prompt,
}: BeginOidcOptions = {}): Promise<void> {
  if (!OIDC_CONFIGURED) {
    throw new Error("No identity provider is configured for this deployment.");
  }

  // Embedded (the Hugging Face Space frames this app): hand the flow to a new
  // top-level tab instead of redirecting here.
  //
  // Identity providers refuse to be framed — Auth0 serves its Universal Login
  // with X-Frame-Options, so redirecting inside the frame produced
  // "dev-….auth0.com refused to connect." The authorize request itself was
  // correct: it reached /u/signup with prompt=create. It just arrived in a
  // window the provider will not draw in.
  //
  // Escaping the frame directly is not available. The Space's iframe sandbox is
  // `allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts
  // …` with **no** allow-top-navigation, so window.top.location is blocked.
  //
  // A popup could load the provider — verified, it escapes the sandbox — but
  // window.open() returns null there, so the opener link is severed and the new
  // context does not inherit sessionStorage. The PKCE verifier lives in
  // sessionStorage, so the callback would land with nothing to exchange. Moving
  // it to localStorage to work around that would widen a single-use secret's
  // lifetime beyond the tab for every user, framed or not.
  //
  // So the new tab starts its own flow: it loads this page top-level, where the
  // ordinary same-tab redirect works exactly as it does for everyone else, with
  // its own sessionStorage. `?continue=` tells it to begin without a second
  // click.
  if (isEmbedded()) {
    const target = new URL(mode === "signup" ? "/signup" : "/signin", window.location.origin);
    target.searchParams.set("continue", mode);
    if (returnTo) target.searchParams.set("returnTo", returnTo);
    // Carried through, or the new tab starts an ordinary flow and the
    // provider signs the person straight back in as whoever it remembers.
    if (prompt) target.searchParams.set("prompt", prompt);
    window.open(target.toString(), "_blank", "noopener");
    return;
  }

  // Ask the provider where its endpoints are rather than assuming a vendor's
  // URL layout; every provider publishes this and they all differ.
  const endpoints = await discoverOidc(OIDC_ISSUER);

  const verifier = randomHex();
  const challenge = await pkceChallenge(verifier);
  const state = randomHex().slice(0, 24);

  sessionStorage.setItem(PKCE_VERIFIER_KEY, verifier);
  sessionStorage.setItem(OIDC_STATE_KEY, state);
  // Carry the token endpoint over so the callback does not have to discover
  // again; it re-discovers if this is missing.
  sessionStorage.setItem(TOKEN_ENDPOINT_KEY, endpoints.token_endpoint);
  // Remembered for sign-out, which can happen days later in a tab that never
  // ran this leg — see END_SESSION_ENDPOINT_KEY.
  if (endpoints.end_session_endpoint) {
    localStorage.setItem(END_SESSION_ENDPOINT_KEY, endpoints.end_session_endpoint);
  }
  if (returnTo) sessionStorage.setItem(OIDC_RETURN_TO_KEY, returnTo);
  else sessionStorage.removeItem(OIDC_RETURN_TO_KEY);

  const url = new URL(endpoints.authorization_endpoint);
  url.searchParams.set("client_id", OIDC_CLIENT_ID);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", OIDC_SCOPE);
  url.searchParams.set("redirect_uri", `${window.location.origin}${OIDC_REDIRECT_PATH}`);
  url.searchParams.set("state", state);
  url.searchParams.set("code_challenge", challenge);
  url.searchParams.set("code_challenge_method", "S256");
  // No `nonce`, deliberately. It is OPTIONAL for the authorization-code flow
  // (OIDC Core 1.0 §3.1.2.1) and its job is to bind an *ID token* to this
  // browser session so a stolen one cannot be replayed. This client never
  // accepts an ID token: the callback reads `access_token` and nothing else,
  // and identity comes from /v1/me, which the backend answers only after
  // verifying that token against the provider's JWKS. So a nonce here would be
  // sent, never checked, and prove nothing — the same shape of dead control as
  // a setting that changes no behaviour.
  //
  // CSRF is covered by `state` (the callback refuses a mismatch) and code
  // interception by PKCE S256 above.
  //
  // Add it the moment anything starts reading the id_token — and validate the
  // claim in the callback in the same change, or it is still decoration.
  if (OIDC_AUDIENCE) url.searchParams.set("audience", OIDC_AUDIENCE);
  if (mode === "signup") {
    url.searchParams.set("prompt", "create");
    url.searchParams.set("screen_hint", "signup");
  } else if (prompt) {
    url.searchParams.set("prompt", prompt);
  }

  window.location.assign(url.toString());
}

/**
 * End the provider's session as well as this browser's — RP-Initiated Logout
 * 1.0 §2.
 *
 * Without this, "sign out" was a local gesture: the token was dropped and the
 * provider's session cookie stayed. Pressing sign-in again produced no login
 * screen at all, just an immediate redirect back with a fresh token for the
 * same person — reported as "when I want to sign in as another user it just
 * automatically signs me in the older account". The account was never signed
 * out of; only this application had forgotten about it.
 *
 * Returns false when the provider publishes no `end_session_endpoint`, or
 * when this deployment has no identity provider at all. The caller has
 * already cleared local state by then, so a false means "signed out here, but
 * the provider still remembers you" — which is exactly when the sign-in page
 * should offer to force a fresh login instead.
 *
 * `id_token_hint` is not sent because this client never accepts an ID token
 * (see the note on `nonce` above — it reads `access_token` and nothing else).
 * The spec's alternative applies: `client_id` MUST accompany
 * `post_logout_redirect_uri` when the hint is absent, and that is what goes
 * out. Some providers then show a confirmation screen, which is correct
 * behaviour rather than a bug — without a hint they cannot know which session
 * the request is for.
 */
export function endOidcSession(): boolean {
  if (typeof window === "undefined" || !OIDC_CONFIGURED) return false;
  const endSession = localStorage.getItem(END_SESSION_ENDPOINT_KEY);
  if (!endSession) return false;

  let url: URL;
  try {
    url = new URL(endSession);
  } catch {
    // A stored value that is not a URL is corrupt, not a reason to throw in
    // the middle of signing someone out.
    localStorage.removeItem(END_SESSION_ENDPOINT_KEY);
    return false;
  }

  url.searchParams.set("client_id", OIDC_CLIENT_ID);
  url.searchParams.set(
    "post_logout_redirect_uri",
    `${window.location.origin}${OIDC_POST_LOGOUT_PATH}`,
  );
  url.searchParams.set("state", randomHex(8));
  window.location.assign(url.toString());
  return true;
}

/** Clear the single-use redirect state (called by the callback once consumed). */
export function clearOidcFlowState(): void {
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  sessionStorage.removeItem(OIDC_STATE_KEY);
  sessionStorage.removeItem(TOKEN_ENDPOINT_KEY);
  sessionStorage.removeItem(OIDC_RETURN_TO_KEY);
  // END_SESSION_ENDPOINT_KEY is deliberately NOT cleared here: it is not
  // single-use state, it is the address sign-out will need long after this
  // redirect is finished.
}
