/**
 * Audit trail page. It exists because the QA audit found the ledger had no
 * reader: an auditor could not see who changed or read a case, nor show the
 * record was unaltered. These pin that the integrity verdict leads the page,
 * that a broken chain is said plainly, and that filters reach the API. The
 * follow-up review added seals: a chain rewritten with every fingerprint
 * recomputed only shows against a seal, and sealing must pause while the
 * record fails its check.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AuditTrailPage from "../../app/admin/audit/page";
import { analyticsApi } from "../../services/analyticsApi";
import type { AuditVerification } from "../../services/analyticsApi";

vi.mock("../../components/StaffGuard", () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role: "ura_auditor", email: "auditor@ura.go.ug" }),
}));

const INTACT: AuditVerification = {
  ledger_enabled: true,
  valid: true,
  rows_checked: 2,
  first_seq: 1,
  last_seq: 2,
  head_hash: "f".repeat(64),
  scope: "full",
  breaks: [],
  anchors_checked: 0,
  anchor_breaks: [],
  latest_anchor: null,
  unsealed_rows: 2,
  verified_at: 1_790_000_000,
};

const EVENTS = [
  {
    seq: 2,
    event_id: "e-2",
    event_type: "staff.ticket_updated",
    actor: "officer-1",
    ts: 1_790_000_100,
    payload: { actor_role: "ura_staff", ticket_id: "t-1", status: "resolved" },
    row_hash: "a".repeat(64),
  },
  {
    seq: 1,
    event_id: "e-1",
    event_type: "staff.ticket_viewed",
    actor: "auditor-9",
    ts: 1_790_000_000,
    payload: { actor_role: "ura_auditor", ticket_id: "t-1" },
    row_hash: "b".repeat(64),
  },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuditTrailPage />
    </QueryClientProvider>,
  );
}

describe("Audit trail", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(analyticsApi, "auditVerify").mockResolvedValue(INTACT);
    vi.spyOn(analyticsApi, "auditEvents").mockResolvedValue({
      ledger_enabled: true,
      events: EVENTS,
      next_before_seq: null,
    });
  });

  it("leads with the integrity verdict and lists events in plain words", async () => {
    renderPage();
    expect(await screen.findByText("Record intact")).toBeInTheDocument();
    expect(screen.getByText("2 events (#1 to #2) checked")).toBeInTheDocument();
    expect(await screen.findByText("Changed a ticket")).toBeInTheDocument();
    expect(screen.getByText("Opened a ticket and its transcript")).toBeInTheDocument();
    expect(screen.getByText("ticket id: t-1 · status: resolved")).toBeInTheDocument();
  });

  it("says plainly when the record has been altered", async () => {
    vi.spyOn(analyticsApi, "auditVerify").mockResolvedValue({
      ...INTACT,
      valid: false,
      breaks: [{ seq: 1, event_id: "e-1", reason: "payload_hash mismatch" }],
    });
    renderPage();
    expect(await screen.findByText("Record altered")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Event #1 fails its check (payload_hash mismatch)");
    expect(screen.getByRole("alert")).toHaveTextContent("Sealing is paused");
    expect(screen.queryByRole("button", { name: "Seal now" })).not.toBeInTheDocument();
  });

  it("names a rewrite that only the seal caught", async () => {
    vi.spyOn(analyticsApi, "auditVerify").mockResolvedValue({
      ...INTACT,
      valid: false,
      anchors_checked: 1,
      anchor_breaks: [
        { anchor_id: "a-1", first_seq: 1, last_seq: 40, reason: "merkle_root mismatch: a sealed row's content changed" },
      ],
    });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Events #1 to #40 no longer match their seal (merkle_root mismatch",
    );
  });

  it("seals on request and says what was sealed", async () => {
    const seal = vi.spyOn(analyticsApi, "auditSeal").mockResolvedValue({
      sealed: true,
      anchor: {
        anchor_id: "a-2",
        first_seq: 1,
        last_seq: 2,
        merkle_root: "c".repeat(64),
        head_hash: "f".repeat(64),
        created_at: 1_790_000_200,
      },
    });
    renderPage();
    expect(await screen.findByText(/Not sealed yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Seal now" }));
    expect(await screen.findByText("Sealed #1 to #2.")).toBeInTheDocument();
    expect(seal).toHaveBeenCalledTimes(1);
  });

  it("has nothing to seal when every event is already sealed", async () => {
    vi.spyOn(analyticsApi, "auditVerify").mockResolvedValue({
      ...INTACT,
      anchors_checked: 1,
      unsealed_rows: 0,
      latest_anchor: {
        anchor_id: "a-1",
        first_seq: 1,
        last_seq: 2,
        merkle_root: "c".repeat(64),
        head_hash: "f".repeat(64),
        created_at: 1_790_000_100,
      },
    });
    renderPage();
    expect(await screen.findByText(/Sealed through #2 on .*; nothing written since\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Seal now" })).toBeDisabled();
  });

  it("warns when this deployment is not recording", async () => {
    vi.spyOn(analyticsApi, "auditEvents").mockResolvedValue({
      ledger_enabled: false,
      events: [],
      next_before_seq: null,
    });
    renderPage();
    expect(await screen.findByText("This deployment is not recording new events")).toBeInTheDocument();
  });

  it("sends the chosen filters to the API", async () => {
    renderPage();
    await screen.findByText("Changed a ticket");
    fireEvent.change(screen.getByLabelText("Show"), { target: { value: "staff.ticket_viewed" } });
    fireEvent.change(screen.getByLabelText("By person"), { target: { value: "auditor-9" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() =>
      expect(analyticsApi.auditEvents).toHaveBeenLastCalledWith(
        expect.objectContaining({ eventType: "staff.ticket_viewed", actor: "auditor-9" }),
      ),
    );
  });

  it("offers the loaded events as a CSV export", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Export 2 as CSV" })).toBeEnabled();
  });
});
