/**
 * The sidebar's account block.
 *
 * This is where sign-in and sign-up are reachable from the assistant itself, so
 * the tests check the two things that would quietly break the entry points:
 * the links pointing at the real routes, and Settings staying reachable in
 * every state (most of what it controls needs no account).
 *
 * The staff case is included because the menu is built from the role: an
 * auditor must not be offered the agent queue.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AccountRail from "../../components/AccountRail";
import { AUTH_TOKEN_STORAGE_KEY, getAuthToken } from "../../lib/authSession";
import { accountApi, ApiError, type Identity } from "../../services/accountApi";

function renderRail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onOpenSettings = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <div className="chatv2">
        <AccountRail onOpenSettings={onOpenSettings} />
      </div>
    </QueryClientProvider>,
  );
  return { ...view, onOpenSettings };
}

function signIn(identity: Partial<Identity> = {}) {
  localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "header.payload.signature");
  vi.spyOn(accountApi, "me").mockResolvedValue({
    authenticated: true,
    role: "verified_taxpayer",
    email: "amina.k@example.ug",
    external_id: "auth0|abc123",
    tenant_id: "default",
    ...identity,
  });
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("AccountRail", () => {
  it("offers sign-in and sign-up when nobody is signed in", () => {
    renderRail();
    expect(screen.getByRole("link", { name: /Sign in/ })).toHaveAttribute("href", "/signin");
    expect(screen.getByRole("link", { name: /Sign up/ })).toHaveAttribute("href", "/signup");
  });

  it("keeps settings reachable while signed out", () => {
    const { onOpenSettings } = renderRail();
    fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
    expect(onOpenSettings).toHaveBeenCalled();
  });

  it("shows who is signed in once the backend confirms the token", async () => {
    signIn();
    renderRail();
    expect(await screen.findByText("amina.k@example.ug")).toBeInTheDocument();
    expect(screen.getByText(/Taxpayer/)).toBeInTheDocument();
    // The signed-out call to action is gone.
    expect(screen.queryByRole("link", { name: /Sign up/ })).toBeNull();
  });

  it("signs out from the account menu", async () => {
    signIn();
    renderRail();
    fireEvent.click(await screen.findByRole("button", { name: /amina\.k@example\.ug/ }));
    const menu = screen.getByRole("menu", { name: "Account" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: /Sign out/ }));
    expect(getAuthToken()).toBe("");
  });

  it("lists only the operations pages the role can open", async () => {
    signIn({ role: "ura_auditor", email: "auditor@ura.go.ug" });
    renderRail();
    fireEvent.click(await screen.findByRole("button", { name: /auditor@ura\.go\.ug/ }));
    const menu = screen.getByRole("menu", { name: "Account" });

    expect(within(menu).getByRole("menuitem", { name: /Operations overview/ })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Analytics/ })).toBeInTheDocument();
    // The agent queue is for ura_staff / ura_admin.
    expect(within(menu).queryByRole("menuitem", { name: /My queue/ })).toBeNull();
  });

  it("does not present an unverified token as a signed-in identity", async () => {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "stale.jwt.value");
    // What the backend ACTUALLY does with an expired or wrong-issuer token:
    // auth/dependencies.py raises HTTPException(401). The 200-with-
    // `authenticated: false` body is only returned when no Authorization header
    // was sent, so mocking that here would have tested an impossible response.
    vi.spyOn(accountApi, "me").mockRejectedValue(
      new ApiError(401, "invalid token: Signature has expired"),
    );
    renderRail();
    expect(await screen.findByText(/no longer valid/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sign in/ })).toBeInTheDocument();
  });

  it("tells a down backend apart from a refused token", async () => {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "good.jwt.value");
    vi.spyOn(accountApi, "me").mockRejectedValue(new ApiError(503, "auth not configured"));
    renderRail();
    // Not "your sign-in is no longer valid" — the token was never judged.
    expect(await screen.findByText(/Sign in to keep your conversations/i)).toBeInTheDocument();
  });
});
