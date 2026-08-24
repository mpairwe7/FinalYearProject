/**
 * OIDC discovery.
 *
 * Endpoints are discovered rather than assumed, because every provider lays its
 * URLs out differently — Keycloak `/protocol/openid-connect/auth`, Auth0
 * `/authorize`, Entra `/oauth2/v2.0/authorize`.
 *
 * The whitespace cases are regressions: the deployed issuer was configured as
 * `https://tenant.us.auth0.com ` with a trailing space, which survives every
 * visual check and turns the discovery URL into `…auth0.com%20/.well-known/…`.
 * That surfaces as an opaque browser NetworkError, not a config error.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { discoverOidc, TOKEN_ENDPOINT_KEY } from "../../lib/oidc";

const DOC = {
  issuer: "https://tenant.us.auth0.com/",
  authorization_endpoint: "https://tenant.us.auth0.com/authorize",
  token_endpoint: "https://tenant.us.auth0.com/oauth/token",
};

function mockFetch(body: unknown, ok = true, status = 200) {
  const spy = vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("discoverOidc", () => {
  it("requests the well-known document at the issuer", async () => {
    const spy = mockFetch(DOC);
    const endpoints = await discoverOidc("https://tenant.us.auth0.com");
    expect(spy.mock.calls[0][0]).toBe(
      "https://tenant.us.auth0.com/.well-known/openid-configuration",
    );
    expect(endpoints.token_endpoint).toBe(DOC.token_endpoint);
    expect(endpoints.authorization_endpoint).toBe(DOC.authorization_endpoint);
  });

  it("tolerates the trailing slash Auth0 shows on its domain", async () => {
    const spy = mockFetch(DOC);
    await discoverOidc("https://tenant.us.auth0.com/");
    expect(spy.mock.calls[0][0]).toBe(
      "https://tenant.us.auth0.com/.well-known/openid-configuration",
    );
  });

  it("tolerates stray whitespace from a CI variable or env file", async () => {
    const spy = mockFetch(DOC);
    await discoverOidc("  https://tenant.us.auth0.com/  ");
    expect(spy.mock.calls[0][0]).toBe(
      "https://tenant.us.auth0.com/.well-known/openid-configuration",
    );
  });

  it("collapses repeated trailing slashes rather than doubling them", async () => {
    const spy = mockFetch(DOC);
    await discoverOidc("https://tenant.us.auth0.com//");
    expect(spy.mock.calls[0][0]).toBe(
      "https://tenant.us.auth0.com/.well-known/openid-configuration",
    );
  });

  it("names the URL when the provider is unreachable", async () => {
    const spy = vi.fn().mockRejectedValue(new Error("NetworkError"));
    vi.stubGlobal("fetch", spy);
    await expect(discoverOidc("https://tenant.us.auth0.com")).rejects.toThrow(
      /could not reach the identity provider/i,
    );
    // The message must point at connect-src, which is the usual cause and is
    // invisible in the browser's own error.
    await expect(discoverOidc("https://tenant.us.auth0.com")).rejects.toThrow(/connect-src/);
  });

  it("reports a non-OK discovery response with its status", async () => {
    mockFetch({}, false, 404);
    await expect(discoverOidc("https://tenant.us.auth0.com")).rejects.toThrow(/HTTP 404/);
  });

  it("rejects a document missing the endpoints it needs", async () => {
    mockFetch({ issuer: "https://tenant.us.auth0.com/" });
    await expect(discoverOidc("https://tenant.us.auth0.com")).rejects.toThrow(
      /missing authorization_endpoint\/token_endpoint/,
    );
  });

  it("falls back to the requested issuer when the document omits it", async () => {
    mockFetch({
      authorization_endpoint: DOC.authorization_endpoint,
      token_endpoint: DOC.token_endpoint,
    });
    const endpoints = await discoverOidc("https://tenant.us.auth0.com/");
    expect(endpoints.issuer).toBe("https://tenant.us.auth0.com");
  });

  it("exports a stable sessionStorage key for the callback leg", () => {
    // The sign-in and callback pages are separate documents; a rename on one side
    // only would silently force a second discovery round-trip.
    expect(TOKEN_ENDPOINT_KEY).toBe("ura_oidc_token_endpoint");
  });

  /**
   * The logout endpoint is how sign-out reaches the provider. Without it,
   * signing out only makes this application forget who you were, and the next
   * sign-in is answered silently from the provider's surviving cookie.
   */
  it("reads the logout endpoint when the provider publishes one", async () => {
    mockFetch({ ...DOC, end_session_endpoint: "https://tenant.us.auth0.com/oidc/logout" });
    const endpoints = await discoverOidc("https://tenant.us.auth0.com");
    expect(endpoints.end_session_endpoint).toBe("https://tenant.us.auth0.com/oidc/logout");
  });

  it("does not treat a missing logout endpoint as a broken document", async () => {
    // Optional in the spec, and a provider without one is usable for
    // everything except remote logout — which the caller degrades from.
    mockFetch(DOC);
    const endpoints = await discoverOidc("https://tenant.us.auth0.com");
    expect(endpoints.end_session_endpoint).toBeUndefined();
    expect(endpoints.token_endpoint).toBe(DOC.token_endpoint);
  });
});
