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
    expect(await screen.findByText(/replayed when\s+it restarts/)).toBeInTheDocument();
  });
});
