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
}

/** sessionStorage key holding the token endpoint across the redirect. */
export const TOKEN_ENDPOINT_KEY = "ura_oidc_token_endpoint";

const DISCOVERY_PATH = "/.well-known/openid-configuration";

/**
 * Fetch the provider's discovery document.
 *
 * Trailing slashes matter: Auth0's issuer is conventionally written with one
 * (`https://tenant.auth0.com/`), so naive concatenation yields a double slash
 * that some providers 404.
 */
export async function discoverOidc(issuer: string): Promise<OidcEndpoints> {
  const base = issuer.replace(/\/+$/, "");
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
  };
}
