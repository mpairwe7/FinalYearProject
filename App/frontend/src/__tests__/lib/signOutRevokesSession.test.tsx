/**
 * Signing out ends the session on the server too (G304, ADR-0053).
 *
 * Both sign-outs only dropped the token in this browser and ended the
 * provider's session; the token itself stayed valid until it expired, so a
 * copy of it (another tab, another device, a leaked one) kept working on the
 * API and kept a staff socket open. Now each one calls `POST /v1/me/logout`
 * with the token BEFORE clearing it, with `keepalive` so the request survives
 * the navigation to the provider's logout. A failed call never stops the
 * sign-out: it must always complete here.
 */
import React from "react";
import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/oidcFlow", () => ({
  // Ending the provider session navigates away; here it only says it did.
  endOidcSession: vi.fn(() => true),
}));

import { StaffNav } from "../../components/StaffGuard";
import { useIdentity } from "../../hooks/useIdentity";
import { getAuthToken, setAuthToken } from "../../lib/authSession";
import { endOidcSession } from "../../lib/oidcFlow";

// A fake, unsigned test token ({"sub":"okello"}, signature "sig"): no secret.
const TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJva2VsbG8ifQ.c2ln"; // gitleaks:allow

interface Seen {
  url: string;
  init: RequestInit | undefined;
  /** The token in storage when the request went out. */
  tokenAtCall: string;
}

let seen: Seen[];

function mockFetch(logout: () => Promise<Response>) {
  seen = [];
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    seen.push({ url, init, tokenAtCall: getAuthToken() });
    if (url.endsWith("/v1/me/logout")) return logout();
    return Promise.resolve(
      new Response(JSON.stringify({ authenticated: true, role: "ura_staff" }), { status: 200 }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function logoutCalls(): Seen[] {
  return seen.filter((call) => call.url.endsWith("/v1/me/logout"));
}

function expectLogoutSentWithTheToken() {
  const calls = logoutCalls();
  expect(calls).toHaveLength(1);
  const [{ url, init, tokenAtCall }] = calls;
  expect(url).toBe("/api/v1/me/logout");
  expect(init?.method).toBe("POST");
  expect(init?.keepalive).toBe(true);
  expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
  // Sent before the token was cleared.
  expect(tokenAtCall).toBe(TOKEN);
}

function renderIdentity() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useIdentity(), { wrapper });
}

function signOutFromTheConsole() {
  render(
    <StaffNav
      who={{ authenticated: true, role: "ura_staff", email: "officer@ura.go.ug" }}
      current="/agent"
      connected
      onOpenPalette={() => undefined}
      onOpenSettings={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /Officer/ }));
  const menu = screen.getByRole("menu", { name: "Account" });
  fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }));
}

beforeEach(() => {
  localStorage.clear();
  setAuthToken(TOKEN, "oidc");
  vi.mocked(endOidcSession).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useIdentity().signOut", () => {
  it("tells the server before it forgets the token", () => {
    mockFetch(() => Promise.resolve(new Response(null, { status: 200 })));
    const { result } = renderIdentity();

    act(() => result.current.signOut());

    expectLogoutSentWithTheToken();
    expect(getAuthToken()).toBe("");
    expect(endOidcSession).toHaveBeenCalledTimes(1);
  });

  it("still signs out when the server cannot be reached", async () => {
    mockFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const { result } = renderIdentity();

    act(() => result.current.signOut());
    await act(async () => {
      await Promise.resolve();
    });

    expect(logoutCalls()).toHaveLength(1);
    expect(getAuthToken()).toBe("");
    expect(endOidcSession).toHaveBeenCalledTimes(1);
  });

  it("still signs out when fetch itself throws", () => {
    const fetchMock = mockFetch(() => Promise.resolve(new Response(null, { status: 200 })));
    const { result } = renderIdentity();
    fetchMock.mockImplementation(() => {
      throw new TypeError("String contains non ISO-8859-1 code point");
    });

    expect(() => act(() => result.current.signOut())).not.toThrow();
    expect(getAuthToken()).toBe("");
  });

  it("sends nothing when there is no token to revoke", () => {
    localStorage.clear();
    mockFetch(() => Promise.resolve(new Response(null, { status: 200 })));
    const { result } = renderIdentity();

    act(() => result.current.signOut());

    expect(logoutCalls()).toHaveLength(0);
  });
});

describe("the staff console's sign-out", () => {
  it("tells the server before it forgets the token", () => {
    mockFetch(() => Promise.resolve(new Response(null, { status: 200 })));

    signOutFromTheConsole();

    expectLogoutSentWithTheToken();
    expect(getAuthToken()).toBe("");
    expect(endOidcSession).toHaveBeenCalledTimes(1);
  });

  it("still signs out when the server answers with an error", async () => {
    mockFetch(() => Promise.resolve(new Response(JSON.stringify({ detail: "x" }), { status: 503 })));

    signOutFromTheConsole();
    await act(async () => {
      await Promise.resolve();
    });

    expect(logoutCalls()).toHaveLength(1);
    expect(getAuthToken()).toBe("");
    expect(endOidcSession).toHaveBeenCalledTimes(1);
  });
});
