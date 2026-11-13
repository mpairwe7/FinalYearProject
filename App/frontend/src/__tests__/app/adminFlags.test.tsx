/**
 * Feature-flag console — the way back out.
 *
 * An override beats FLAG_* and is replayed into the registry on every boot, so
 * a flag toggled once from here kept ignoring its environment for good. The
 * page could pin a flag and had no control to unpin it; these cases hold the
 * reset in place and keep it off the rows where it would mislead.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AdminFlagsPage from "../../app/admin/flags/page";
import { analyticsApi } from "../../services/analyticsApi";

let role = "ura_admin";

vi.mock("../../components/StaffGuard", () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role, email: `${role}@ura.go.ug` }),
}));

const FLAGS = [
  {
    name: "hyde",
    default: false,
    description: "Hypothetical document embeddings",
    enabled: true,
    overridden: true,
    protected: false,
    rollout: null,
  },
  {
    name: "graph_fusion",
    default: false,
    description: "Fuse graph neighbours into retrieval",
    enabled: false,
    overridden: false,
    protected: false,
    rollout: null,
  },
  {
    name: "auth_required",
    default: true,
    description: "Reject unauthenticated callers",
    enabled: true,
    overridden: true,
    protected: true,
    rollout: null,
  },
];

function renderFlags() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminFlagsPage />
    </QueryClientProvider>,
  );
}

function row(name: string): HTMLElement {
  return screen.getByText(name).closest("tr") as HTMLElement;
}

describe("Feature flags console", () => {
  beforeEach(() => {
    role = "ura_admin";
    vi.restoreAllMocks();
    vi.spyOn(analyticsApi, "flags").mockResolvedValue({
      flags: FLAGS,
      overrides_are_ephemeral: false,
    });
    vi.spyOn(analyticsApi, "clearFlag").mockResolvedValue({
      name: "hyde",
      enabled: false,
      overridden: false,
    });
    vi.spyOn(analyticsApi, "setFlag").mockResolvedValue({
      name: "hyde",
      enabled: true,
      ephemeral: false,
    });
  });

  it("offers a reset on an overridden flag", async () => {
    renderFlags();
    await screen.findByText("hyde");
    fireEvent.click(within(row("hyde")).getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(analyticsApi.clearFlag).toHaveBeenCalledWith("hyde"));
  });

  it("offers no reset where there is no override to drop", async () => {
    renderFlags();
    await screen.findByText("graph_fusion");
    expect(within(row("graph_fusion")).queryByRole("button", { name: "Reset" })).toBeNull();
  });

  it("keeps reset off protected flags, which cannot be set from here either", async () => {
    renderFlags();
    await screen.findByText("auth_required");
    expect(within(row("auth_required")).queryByRole("button", { name: "Reset" })).toBeNull();
  });

  it("gives an administrator both controls on an overridden flag", async () => {
    renderFlags();
    await screen.findByText("hyde");
    expect(within(row("hyde")).getByRole("switch")).toBeInTheDocument();
    expect(within(row("hyde")).getByRole("button", { name: "Reset" })).toBeInTheDocument();
  });

  it("is read-only for an auditor", async () => {
    role = "ura_auditor";
    renderFlags();
    await screen.findByText("hyde");
    // The API agrees: PATCH and DELETE both 403 for ura_auditor.
    expect(within(row("hyde")).queryByRole("button", { name: "Reset" })).toBeNull();
    expect(within(row("hyde")).queryByRole("switch")).toBeNull();
  });

  it("says an override survives a restart rather than dying with the pod", async () => {
    renderFlags();
    // The note used to read "lost when it is replaced", which is the opposite
    // of what boot rehydration does and is why nobody looked for a reset.
    expect(await screen.findByText(/replayed whenever\s+a process\s+restarts/)).toBeInTheDocument();
  });

  it("says a change reaches every API process within the sync interval (G612)", async () => {
    // It used to say "this replica only", which was true until every process
    // re-read the stored overrides; now the calls kill switch works from here.
    vi.spyOn(analyticsApi, "flags").mockResolvedValue({
      flags: FLAGS,
      overrides_are_ephemeral: false,
      scope: "every_process_sharing_this_database",
      sync_interval_seconds: 5,
      synced_at: 1_700_000_000,
    });
    renderFlags();
    expect(
      await screen.findByText(/reach every API process at its next sync, every 5 s/),
    ).toBeInTheDocument();
    // A promise of "within 5 s" would be wrong for a process that cannot read
    // the store; the note says how to confirm instead.
    expect(screen.getByText(/confirm the calls switch from/)).toBeInTheDocument();
    expect(screen.queryByText(/this replica only/)).toBeNull();
  });

  it("says the other processes apply a change when they restart if the sync is off", async () => {
    vi.spyOn(analyticsApi, "flags").mockResolvedValue({
      flags: FLAGS,
      overrides_are_ephemeral: false,
      scope: "this_process_until_the_others_restart",
      sync_interval_seconds: 0,
      synced_at: null,
    });
    renderFlags();
    expect(await screen.findByText(/reach the other API processes when they restart/)).toBeInTheDocument();
  });

  it("claims no reach while the flag list has not arrived", async () => {
    // A restart-only notice beside a spinner, or after an error, stated a
    // sync setting the console had not been told.
    vi.spyOn(analyticsApi, "flags").mockReturnValue(new Promise(() => {}));
    renderFlags();
    expect(await screen.findByText(/How far a change here reaches/)).toBeInTheDocument();
    expect(screen.queryByText(/at its next sync/)).toBeNull();
    expect(screen.queryByText(/when they restart/)).toBeNull();
  });

  // G638: the console called overrides durable on the Space, labelled a
  // stored override "this replica", and switched the figure hold off in one click.
  describe("what a change reaches, and the safety controls", () => {
    const SAFETY = {
      name: "stream_figure_hold",
      default: true,
      description: "Hold streamed figures until the guards have read them",
      enabled: true,
      overridden: false,
      stored: false,
      protected: false,
      rollout: null,
    };

    it("says overrides are lost on restart where they are", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({
        flags: FLAGS, overrides_are_ephemeral: true, overrides_durability: "ephemeral",
      } as never);
      renderFlags();
      expect(await screen.findByText(/lost when this deployment restarts/i)).toBeInTheDocument();
      expect(screen.queryByText(/reports overrides as durable/i)).toBeNull();
    });

    it("labels a stored override as reaching every process", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({
        flags: [{ ...FLAGS[0], stored: true }], overrides_are_ephemeral: false, overrides_durability: "durable",
        scope: "every_process_sharing_this_database", sync_interval_seconds: 5,
      } as never);
      renderFlags();
      await screen.findByText("hyde");
      expect(within(row("hyde")).getByText("stored · every process")).toBeInTheDocument();
      expect(within(row("hyde")).queryByText("this replica")).toBeNull();
    });

    it("asks before switching a safety control off", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({ flags: [SAFETY], overrides_are_ephemeral: false } as never);
      renderFlags();
      await screen.findByText("stream_figure_hold");
      fireEvent.click(within(row("stream_figure_hold")).getByRole("switch"));
      expect(analyticsApi.setFlag).not.toHaveBeenCalled();
      const confirm = screen.getByRole("alertdialog", { name: /Turn off stream_figure_hold/ });
      expect(confirm).toHaveTextContent(/before the guards have read them/i);
      fireEvent.click(within(confirm).getByRole("button", { name: /^Turn it off$/ }));
      await waitFor(() => expect(analyticsApi.setFlag).toHaveBeenCalledWith("stream_figure_hold", false));
    });

    // CodeRabbit on PR 730: Reset drops the override, so an enabled safety
    // control falls back to FLAG_* or its default, which may be off.
    it("asks before resetting an enabled safety control, and resets rather than saving off", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({
        flags: [{ ...SAFETY, name: "spoken_figure_guard", default: false, overridden: true, stored: true }],
        overrides_are_ephemeral: false,
      } as never);
      renderFlags();
      await screen.findByText("spoken_figure_guard");
      fireEvent.click(within(row("spoken_figure_guard")).getByRole("button", { name: /^Reset$/ }));
      expect(analyticsApi.clearFlag).not.toHaveBeenCalled();
      const confirm = screen.getByRole("alertdialog", { name: /Reset spoken_figure_guard/ });
      expect(confirm).toHaveTextContent(/default \(off\)/i);
      fireEvent.click(within(confirm).getByRole("button", { name: /^Reset it$/ }));
      await waitFor(() => expect(analyticsApi.clearFlag).toHaveBeenCalledWith("spoken_figure_guard"));
      expect(analyticsApi.setFlag).not.toHaveBeenCalled();
    });

    // CodeRabbit on PR 730: the confirmation claimed every process within one
    // sync interval even where the sync is off.
    it("says how far turning a control off reaches, as the page note does", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({
        flags: [SAFETY], overrides_are_ephemeral: false, sync_interval_seconds: 0,
      } as never);
      renderFlags();
      await screen.findByText("stream_figure_hold");
      fireEvent.click(within(row("stream_figure_hold")).getByRole("switch"));
      const confirm = screen.getByRole("alertdialog", { name: /Turn off stream_figure_hold/ });
      expect(confirm).toHaveTextContent(/when they next start/i);
      expect(confirm).not.toHaveTextContent(/within one sync interval/i);
    });

    // CodeRabbit on PR 730: the confirmation sits after the whole table; focus
    // stayed on the switch, so the dialog could open out of sight.
    it("moves focus into the confirmation, and back to the switch when kept on", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({ flags: [SAFETY], overrides_are_ephemeral: false } as never);
      renderFlags();
      await screen.findByText("stream_figure_hold");
      const toggle = within(row("stream_figure_hold")).getByRole("switch");
      toggle.focus();
      fireEvent.click(toggle);
      await waitFor(() => expect(screen.getByRole("button", { name: /^Keep it on$/ })).toHaveFocus());
      fireEvent.click(screen.getByRole("button", { name: /^Keep it on$/ }));
      await waitFor(() => expect(within(row("stream_figure_hold")).getByRole("switch")).toHaveFocus());
    });

    it("can be kept on", async () => {
      vi.spyOn(analyticsApi, "flags").mockResolvedValue({ flags: [SAFETY], overrides_are_ephemeral: false } as never);
      renderFlags();
      await screen.findByText("stream_figure_hold");
      fireEvent.click(within(row("stream_figure_hold")).getByRole("switch"));
      fireEvent.click(screen.getByRole("button", { name: /^Keep it on$/ }));
      expect(screen.queryByRole("alertdialog")).toBeNull();
      expect(analyticsApi.setFlag).not.toHaveBeenCalled();
    });
  });
});
