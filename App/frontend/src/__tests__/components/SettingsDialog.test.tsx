/**
 * Settings.
 *
 * The point of these tests is that the controls are wired to real state rather
 * than to local component state that dies with the dialog: the theme control
 * writes the theme store, the language control writes the chat store, and the
 * analytics switch writes the same consent key the privacy banner uses.
 *
 * Also covered: the honest signed-out states. A profile has nowhere to live
 * without an account, and a settings panel that silently discards edits is
 * worse than one that says so.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsDialog from "../../components/settings/SettingsDialog";
import {
  ANALYTICS_CONSENT_KEY,
  getAnalyticsConsent,
  setAnalyticsConsent,
} from "../../lib/analyticsConsent";
import { AUTH_TOKEN_STORAGE_KEY } from "../../lib/authSession";
import { getThemePref } from "../../lib/theme";
import { useChatStore } from "../../store/useChatStore";
import { useVoiceStore } from "../../store/useVoiceStore";

function renderDialog(props: Partial<React.ComponentProps<typeof SettingsDialog>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = vi.fn();
  const onTabChange = vi.fn();
  const onAutoNarrateChange = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <div className="chatv2">
        <SettingsDialog
          open
          tab="general"
          onClose={onClose}
          onTabChange={onTabChange}
          autoNarrate={false}
          onAutoNarrateChange={onAutoNarrateChange}
          speechReady
          blogUrl="https://blog.example"
          {...props}
        />
      </div>
    </QueryClientProvider>,
  );
  return { ...view, onClose, onTabChange, onAutoNarrateChange };
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  useChatStore.setState({ locale: "en", conversations: [] });
  useVoiceStore.setState({ voiceByLocale: {} });
});

describe("SettingsDialog", () => {
  it("renders nothing until it is opened", () => {
    renderDialog({ open: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("presents the sections as a tablist and reports the chosen one", () => {
    const { onTabChange } = renderDialog();
    const tabs = screen.getByRole("tablist", { name: "Settings sections" });
    expect(within(tabs).getByRole("tab", { name: "General" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    fireEvent.click(within(tabs).getByRole("tab", { name: "Privacy & data" }));
    // Controlled: the dialog asks the page to switch rather than switching itself.
    expect(onTabChange).toHaveBeenCalledWith("privacy");
  });

  it("closes on Escape", () => {
    const { onClose } = renderDialog();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("writes the theme preference the rest of the app reads", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(getThemePref()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("writes the response language to the chat store", () => {
    renderDialog();
    fireEvent.change(screen.getByLabelText("Response language"), { target: { value: "lg" } });
    expect(useChatStore.getState().locale).toBe("lg");
  });

  it("saves a narration voice against the language it belongs to", async () => {
    // The catalogue is served, not hardcoded — a deployment without Sunbird
    // offers fewer voices, and the panel has to reflect that.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          sunbird_configured: true,
          voices: {
            en: [
              { id: "en-US-AriaNeural", provider: "edge_tts", native: false, default: true, available: true },
              { id: "en-GB-SoniaNeural", provider: "edge_tts", native: false, default: false, available: true },
            ],
            lg: [
              { id: "salt_lug_0001", provider: "sunbird", native: true, default: true, available: true },
              { id: "waxal_lug_0004", provider: "sunbird", native: true, default: false, available: true },
            ],
          },
        }),
      } as Response),
    );

    renderDialog({ tab: "voice" });

    // Luganda's second speaker is stored under `lg`, not globally — an English
    // narration must not inherit a Luganda voice it cannot synthesise.
    const luganda = await screen.findByRole("radiogroup", {
      name: "Narration voice for Luganda",
    });
    fireEvent.click(within(luganda).getByRole("radio", { name: /Voice 2/ }));
    expect(useVoiceStore.getState().voiceByLocale).toEqual({ lg: "waxal_lug_0004" });

    // Re-picking the default clears the choice rather than pinning it, so the
    // locale keeps following the backend if that default ever moves.
    fireEvent.click(within(luganda).getByRole("radio", { name: /Voice 1/ }));
    expect(useVoiceStore.getState().voiceByLocale).toEqual({});
    vi.unstubAllGlobals();
  });

  it("follows the consent banner while the panel is open", () => {
    renderDialog({ tab: "privacy" });
    const toggle = screen.getByRole("switch", { name: "Anonymous product analytics" });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    // The banner renders above this dialog and writes the same key, so a
    // first-time visitor can accept it without closing settings. A snapshot
    // taken on open would leave this switch disagreeing with storage.
    act(() => setAnalyticsConsent(true));
    expect(toggle).toHaveAttribute("aria-checked", "true");

    act(() => setAnalyticsConsent(false));
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("hands narration changes back to the page that owns the state", () => {
    const { onAutoNarrateChange } = renderDialog({ tab: "voice" });
    fireEvent.click(screen.getByRole("switch", { name: "Narrate replies aloud" }));
    expect(onAutoNarrateChange).toHaveBeenCalledWith(true);
  });

  it("toggles analytics consent through the same key as the privacy banner", () => {
    renderDialog({ tab: "privacy" });
    const toggle = screen.getByRole("switch", { name: "Anonymous product analytics" });
    expect(toggle).toHaveAttribute("aria-checked", "false");

    fireEvent.click(toggle);
    expect(getAnalyticsConsent()).toBe(true);
    expect(localStorage.getItem(ANALYTICS_CONSENT_KEY)).toBe("true");
    expect(toggle).toHaveAttribute("aria-checked", "true");
  });

  it("confirms before deleting the conversation history", () => {
    useChatStore.setState({
      conversations: [
        {
          id: "c1",
          title: "VAT question",
          preview: "…",
          turns: [],
          createdAt: 1,
          updatedAt: 2,
        },
      ],
    });
    renderDialog({ tab: "privacy" });

    fireEvent.click(screen.getByRole("button", { name: "Delete all" }));
    const confirm = screen.getByRole("alertdialog");
    expect(within(confirm).getByText("Delete all conversations?")).toBeInTheDocument();
    // Still there while the question is on screen.
    expect(useChatStore.getState().conversations).toHaveLength(1);

    fireEvent.click(within(confirm).getByRole("button", { name: "Delete all" }));
    expect(useChatStore.getState().conversations).toHaveLength(0);
  });

  it("says a profile needs an account instead of losing the edits", () => {
    renderDialog({ tab: "profile" });
    expect(screen.getByText(/needs a sign-in/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
    expect(screen.getByRole("link", { name: "create an account" })).toHaveAttribute(
      "href",
      "/signup",
    );
  });

  it("offers both auth entry points on the account tab when signed out", () => {
    renderDialog({ tab: "account" });
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
    expect(screen.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/signup",
    );
  });

  it("does not claim a stale token is a signed-in session", async () => {
    // A token the backend refuses comes back as 401 from
    // auth/dependencies.py, not as a 200 carrying `authenticated: false` —
    // that body is only sent when no Authorization header was present.
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "stale.jwt.value");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "invalid token: Signature has expired" }),
      } as Response),
    );

    renderDialog({ tab: "account" });
    expect(await screen.findByText(/does not accept it/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });

  it("does not tell someone to sign in when the backend is simply down", async () => {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "good.jwt.value");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ detail: "auth not configured" }),
      } as Response),
    );

    renderDialog({ tab: "profile" });
    expect(await screen.findByText(/backend cannot be reached/i)).toBeInTheDocument();
    expect(screen.queryByText(/needs a sign-in/i)).toBeNull();
    vi.unstubAllGlobals();
  });

  it("offers to retry, not to sign in again, when the backend is unreachable", async () => {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "good.jwt.value");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({ detail: "auth not configured" }),
      } as Response),
    );

    renderDialog({ tab: "account" });
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
