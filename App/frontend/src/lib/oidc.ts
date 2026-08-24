/**
 * OIDC provider discovery.
 *
 * The sign-in pages originally built the authorization and token URLs by
 * appending Keycloak's paths (`/protocol/openid-connect/auth`, `.../token`) to
 * the issuer. That only ever worked for Keycloak: Auth0 serves `/authorize` and
 * `/oauth/token`, Entra ID uses `/oauth2/v2.0/authorize`, and Okta uses
 * `/v1/authorize`. Discovery is the part of the spec that exists precisely so a
 * client does not have to know any of that (OIDC Discovery 1.0 §4).
 */

export interface OidcEndpoints {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  /**
   * RP-Initiated Logout 1.0 §2. Optional: not every provider publishes one,
   * and a deployment without it can only clear the token in this browser —
   * which is precisely the state that made "sign in as someone else" sign you
   * back in as yourself.
   */
  end_session_endpoint?: string;
}

/** sessionStorage key holding the token endpoint across the redirect. */
export const TOKEN_ENDPOINT_KEY = "ura_oidc_token_endpoint";

/**
 * localStorage key holding the provider's logout endpoint.
 *
 * localStorage, not sessionStorage, unlike everything else the flow stashes:
 * the other values are single-use secrets that must die with the tab, and this
 * one has to outlive the whole session so sign-out — which can happen days
 * later, in a tab that never ran the sign-in leg — does not have to re-fetch
 * a discovery document before it can log anyone out. It is a public URL from
 * a public document.
 */
export const END_SESSION_ENDPOINT_KEY = "ura_oidc_end_session_endpoint";

const DISCOVERY_PATH = "/.well-known/openid-configuration";

/**
 * Fetch the provider's discovery document.
 *
 * Trailing slashes matter: Auth0's issuer is conventionally written with one
 * (`https://tenant.auth0.com/`), so naive concatenation yields a double slash
 * that some providers 404.
 */
export async function discoverOidc(issuer: string): Promise<OidcEndpoints> {
  // Trim before stripping slashes. These values arrive from CI variables and env
  // files, where a trailing space is easy to introduce and invisible on screen —
  // it would otherwise be percent-encoded into the host and fail as an opaque
  // network error rather than a bad-configuration message.
  const base = issuer.trim().replace(/\/+$/, "");
  const url = `${base}${DISCOVERY_PATH}`;

  let res: Response;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (err) {
    // Almost always CSP or an unreachable issuer — say which, since the browser's
    // own message for a blocked fetch is just "NetworkError".
    throw new Error(
      `could not reach the identity provider at ${url} (${(err as Error).message}). ` +
        "If the provider origin is missing from connect-src, the browser blocks this silently.",
    );
  }
  if (!res.ok) {
    throw new Error(`identity provider discovery failed at ${url} (HTTP ${res.status})`);
  }

  const doc = (await res.json()) as Partial<OidcEndpoints>;
  if (!doc.authorization_endpoint || !doc.token_endpoint) {
    throw new Error(`discovery document at ${url} is missing authorization_endpoint/token_endpoint`);
  }
  return {
    issuer: doc.issuer || base,
    authorization_endpoint: doc.authorization_endpoint,
    token_endpoint: doc.token_endpoint,
    // Not required — a provider that omits it simply cannot be logged out
    // remotely, and the caller degrades to a local sign-out.
    end_session_endpoint: doc.end_session_endpoint,
  };
}
