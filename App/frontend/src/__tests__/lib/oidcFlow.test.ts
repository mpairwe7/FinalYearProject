/**
 * Starting the OIDC redirect.
 *
 * The properties worth pinning are the ones a browser will not complain about
 * if they break — it just ends up on the wrong screen:
 *
 * - Sign-up must carry a registration hint, or someone who clicked "Sign up"
 *   lands on a login form with no way to register. Both spellings are sent
 *   (`prompt=create` from the OIDC registration spec, `screen_hint=signup` for
 *   Auth0) because the deployment could be either kind of provider.
 * - PKCE has to be S256 with the verifier kept for the callback leg; a missing
 *   verifier surfaces much later as an opaque token-exchange failure.
 * - An unconfigured deployment must throw rather than navigate somewhere built
 *   from empty strings.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const DISCOVERY = {
  issuer: "https://tenant.us.auth0.com/",
  authorization_endpoint: "https://tenant.us.auth0.com/authorize",
  token_endpoint: "https://tenant.us.auth0.com/oauth/token",
};

const assign = vi.fn();

/**
 * Import the module fresh with the given env, since it reads the
 * NEXT_PUBLIC_OIDC_* values once at load time (they are build-time inlined).
 */
async function loadFlow(env: Record<string, string>) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  return import("../../lib/oidcFlow");
}

/** The URL the flow navigated to, parsed. */
function navigatedUrl(): URL {
  expect(assign).toHaveBeenCalledTimes(1);
  return new URL(assign.mock.calls[0][0] as string);
}

beforeEach(() => {
  assign.mockClear();
  sessionStorage.clear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { origin: "https://assistant.ura.go.ug", assign },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => DISCOVERY,
  } as Response));
  // Deterministic PKCE: the real implementations exist in the browser, but the
  // assertions below are about what reaches the provider, not about entropy.
  vi.stubGlobal("crypto", {
    ...globalThis.crypto,
    getRandomValues: (buf: Uint8Array) => buf.fill(7),
    subtle: { digest: async () => new Uint8Array(32).fill(9).buffer },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

const CONFIGURED = {
  NEXT_PUBLIC_OIDC_ISSUER: "https://tenant.us.auth0.com",
  NEXT_PUBLIC_OIDC_CLIENT_ID: "client-123",
};

describe("beginOidcFlow", () => {
  it("sends an authorization-code request with PKCE S256", async () => {
    const { beginOidcFlow } = await loadFlow(CONFIGURED);
    await beginOidcFlow();

    const url = navigatedUrl();
    expect(url.origin + url.pathname).toBe("https://tenant.us.auth0.com/authorize");
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("client_id")).toBe("client-123");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).toBeTruthy();
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://assistant.ura.go.ug/signin/callback",
    );
  });

  it("keeps the verifier and state for the callback leg", async () => {
    const { beginOidcFlow, OIDC_STATE_KEY, PKCE_VERIFIER_KEY } = await loadFlow(CONFIGURED);
    await beginOidcFlow();

    const url = navigatedUrl();
    expect(sessionStorage.getItem(PKCE_VERIFIER_KEY)).toBeTruthy();
    expect(sessionStorage.getItem(OIDC_STATE_KEY)).toBe(url.searchParams.get("state"));
    // The token endpoint travels with it so the callback need not re-discover.
    expect(sessionStorage.getItem("ura_oidc_token_endpoint")).toBe(DISCOVERY.token_endpoint);
  });

  it("asks for the registration screen in signup mode", async () => {
    const { beginOidcFlow } = await loadFlow(CONFIGURED);
    await beginOidcFlow({ mode: "signup", returnTo: "/" });

    const url = navigatedUrl();
    expect(url.searchParams.get("prompt")).toBe("create");
    expect(url.searchParams.get("screen_hint")).toBe("signup");
    expect(sessionStorage.getItem("ura_oidc_return_to")).toBe("/");
  });

  it("does not ask for registration when signing in", async () => {
    const { beginOidcFlow } = await loadFlow(CONFIGURED);
    await beginOidcFlow({ mode: "signin" });

    const url = navigatedUrl();
    expect(url.searchParams.get("prompt")).toBeNull();
    expect(url.searchParams.get("screen_hint")).toBeNull();
    expect(sessionStorage.getItem("ura_oidc_return_to")).toBeNull();
  });

  it("includes the audience only when one is configured", async () => {
    const { beginOidcFlow } = await loadFlow({
      ...CONFIGURED,
      NEXT_PUBLIC_OIDC_AUDIENCE: "https://api.ura.go.ug",
    });
    await beginOidcFlow();
    expect(navigatedUrl().searchParams.get("audience")).toBe("https://api.ura.go.ug");
  });

  it("throws instead of navigating when no provider is configured", async () => {
    const { beginOidcFlow, OIDC_CONFIGURED } = await loadFlow({
      NEXT_PUBLIC_OIDC_ISSUER: "",
      NEXT_PUBLIC_OIDC_CLIENT_ID: "",
    });
    expect(OIDC_CONFIGURED).toBe(false);
    await expect(beginOidcFlow()).rejects.toThrow(/No identity provider/);
    expect(assign).not.toHaveBeenCalled();
  });

  it("surfaces a discovery failure rather than guessing the URL layout", async () => {
    const { beginOidcFlow } = await loadFlow(CONFIGURED);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404 } as Response));
    await expect(beginOidcFlow()).rejects.toThrow(/discovery failed/);
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("clearOidcFlowState", () => {
  it("removes every single-use value the redirect left behind", async () => {
    const { beginOidcFlow, clearOidcFlowState } = await loadFlow(CONFIGURED);
    await beginOidcFlow({ mode: "signup", returnTo: "/" });
    clearOidcFlowState();
    for (const key of [
      "ura_pkce_verifier",
      "ura_oidc_state",
      "ura_oidc_token_endpoint",
      "ura_oidc_return_to",
    ]) {
      expect(sessionStorage.getItem(key)).toBeNull();
    }
  });
});
