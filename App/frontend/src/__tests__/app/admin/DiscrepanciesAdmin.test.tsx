import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiscrepanciesWorkbench } from "../../../app/admin/discrepancies/page";
import { analyticsApi } from "../../../services/analyticsApi";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
  },
});

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const mockDiscrepancies = {
  discrepancies: [
    {
      id: "disc-12345678",
      status: "pending",
      priority: "high",
      discrepancy_type: "rate_mismatch",
      frequency_count: 3,
      bot_statement: "Presumptive tax is 2% across all brackets.",
      user_correction: "Second Schedule has fixed tax or percentage by turnover bracket.",
      cited_sources: ["income_tax_act.pdf"],
      admin_note: "Needs verification against FY 2026/27 schedule",
      created_at: 1791230000,
    },
  ],
  stats: {
    total: 4,
    pending: 1,
    verified: 2,
    dismissed: 1,
  },
};

describe("DiscrepanciesWorkbench (/admin/discrepancies)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
    vi.spyOn(analyticsApi, "discrepancies").mockResolvedValue(mockDiscrepancies as never);
    vi.spyOn(analyticsApi, "precedences").mockResolvedValue({
      precedences: [
        {
          id: "prec-1",
          topic: "PRESUMPTIVE TAX",
          rule_statement: "Turnover UGX 10M-30M with records pays 0.4% above 10M.",
          statute_reference: "Income Tax Act Second Schedule",
          enabled: true,
          created_at: 1791230000,
        },
      ],
    } as never);
    vi.spyOn(analyticsApi, "tombstones").mockResolvedValue({
      tombstones: [
        {
          id: "tomb-1",
          source_uri: "legacy_vat_2019.pdf",
          chunk_id: "chunk-99",
          reason: "Superseded by FY 2026/27 Amendment",
          created_at: 1791230000,
        },
      ],
    } as never);
  });

  it("renders KPI stats, discrepancy diff cards, and hotfix controls for ura_admin", async () => {
    render(
      <DiscrepanciesWorkbench
        who={{ authenticated: true, role: "ura_admin", email: "admin@ura.go.ug" }}
      />,
      { wrapper }
    );

    expect(await screen.findByText("Report #12345678")).toBeInTheDocument();
    expect(screen.getByText("Reported 3x")).toBeInTheDocument();
    expect(screen.getByText("Challenged Bot Statement")).toBeInTheDocument();
    expect(screen.getByText("Taxpayer Correction")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Export 1 as CSV/i })).toBeEnabled();

    const verifyBtn = screen.getByRole("button", { name: /Verify & Hotfix/i });
    fireEvent.click(verifyBtn);
    expect(screen.getByText(/Verify Bug & Deploy Hotfixes/i)).toBeInTheDocument();
  });

  it("enforces read-only governance mode for ura_auditor and ura_staff", async () => {
    render(
      <DiscrepanciesWorkbench
        who={{ authenticated: true, role: "ura_auditor", email: "auditor@ura.go.ug" }}
      />,
      { wrapper }
    );

    expect(await screen.findByText("Read-only for your role")).toBeInTheDocument();
    expect(await screen.findByText("Report #12345678")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Verify & Hotfix/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Dismiss \(Doesn't Apply\)/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Delete$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove$/i })).not.toBeInTheDocument();
  });

  it("surfaces an accessible error alert when a verification mutation fails", async () => {
    vi.spyOn(analyticsApi, "verifyDiscrepancy").mockRejectedValue(new Error("Database write locked"));
    render(
      <DiscrepanciesWorkbench
        who={{ authenticated: true, role: "ura_admin", email: "admin@ura.go.ug" }}
      />,
      { wrapper }
    );

    fireEvent.click(await screen.findByRole("button", { name: /Verify & Hotfix/i }));
    fireEvent.click(screen.getByRole("button", { name: /Confirm & Apply Hotfixes/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Action could not be completed");
    expect(alert).toHaveTextContent("Database write locked");
  });
});
