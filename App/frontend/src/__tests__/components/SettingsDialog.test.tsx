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
import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsDialog from "../../components/settings/SettingsDialog";
import { ANALYTICS_CONSENT_KEY, getAnalyticsConsent } from "../../lib/analyticsConsent";
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
  useVoiceStore.setState({ voiceId: "" });
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

  it("saves the narration voice for the next TTS call", () => {
    renderDialog({ tab: "voice" });
    fireEvent.change(screen.getByLabelText("Narration voice"), {
      target: { value: "en-GB-SoniaNeural" },
    });
    expect(useVoiceStore.getState().voiceId).toBe("en-GB-SoniaNeural");
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
    // A token the backend refuses: /v1/me answers `authenticated: false`.
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, "stale.jwt.value");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ authenticated: false, role: "public" }),
      } as Response),
    );

    renderDialog({ tab: "account" });
    expect(await screen.findByText(/does not accept it/i)).toBeInTheDocument();
    vi.unstubAllGlobals();
  });
});
