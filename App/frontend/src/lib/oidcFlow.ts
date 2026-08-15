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

import { discoverOidc, TOKEN_ENDPOINT_KEY } from "./oidc";

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
export async function beginOidcFlow({
  mode = "signin",
  returnTo,
}: BeginOidcOptions = {}): Promise<void> {
  if (!OIDC_CONFIGURED) {
    throw new Error("No identity provider is configured for this deployment.");
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
  if (OIDC_AUDIENCE) url.searchParams.set("audience", OIDC_AUDIENCE);
  if (mode === "signup") {
    url.searchParams.set("prompt", "create");
    url.searchParams.set("screen_hint", "signup");
  }

  window.location.assign(url.toString());
}

/** Clear the single-use redirect state (called by the callback once consumed). */
export function clearOidcFlowState(): void {
  sessionStorage.removeItem(PKCE_VERIFIER_KEY);
  sessionStorage.removeItem(OIDC_STATE_KEY);
  sessionStorage.removeItem(TOKEN_ENDPOINT_KEY);
  sessionStorage.removeItem(OIDC_RETURN_TO_KEY);
}
