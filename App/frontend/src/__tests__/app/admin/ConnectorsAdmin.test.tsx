import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectorBoard } from "../../../app/admin/connectors/page";

const connector = {
  id: "efris",
  name: "EFRIS",
  description: "Electronic fiscal invoicing simulator",
  connected: true,
  healthy: true,
  status: "active",
  live: false,
  mode: "simulation",
  tools: ["efris_fiscal_invoice"],
};

const chatConnector = {
  namespace: "efris",
  label: "EFRIS",
  description: "Review approved taxpayer information.",
  operation_count: 2,
  read_only: true,
};

function response(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

describe("connector admin board", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("labels connector health as local simulation status", async () => {
    vi.stubGlobal("fetch", vi.fn((url: RequestInfo | URL) => {
      if (String(url) === "/api/v1/connectors?view=chat") {
        return response({ ok: true, enabled: false, connectors: [] });
      }
      return response({ ok: true, live: false, mode: "simulation", connectors: [connector] });
    }));

    render(<ConnectorBoard />);

    expect(await screen.findByText("These are local simulations")).toBeInTheDocument();
    expect(screen.getByText(/healthy locally/)).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText(/No live filing or payment is made/)).toBeInTheDocument();
    expect(screen.getByText("Chat integrations are disabled")).toBeInTheDocument();
  });

  it("shows reviewed remote chat integrations separately from local simulations", async () => {
    const fetchMock = vi.fn((url: RequestInfo | URL) => {
      if (String(url) === "/api/v1/connectors?view=chat") {
        return response({ ok: true, enabled: true, connectors: [chatConnector] });
      }
      return response({ ok: true, live: false, mode: "simulation", connectors: [connector] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ConnectorBoard />);

    const section = await screen.findByRole("region", { name: "Taxpayer-chat integrations" });
    expect(await within(section).findByText("Available for chat")).toBeInTheDocument();
    expect(within(section).getByText("Read-only")).toBeInTheDocument();
    expect(within(section).getByText("2 approved operations · efris")).toBeInTheDocument();
    expect(within(section).getByText("Review approved taxpayer information.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/connectors?view=chat",
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(screen.getByText("Local simulation", { exact: true })).toBeInTheDocument();
  });

  it("waits for the authenticated toggle response before refreshing status", async () => {
    let connected = true;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/connectors?view=chat") {
        return response({ ok: true, enabled: true, connectors: [chatConnector] });
      }
      if (init?.method === "POST" && url.endsWith("/toggle")) {
        connected = false;
        return response({ ok: true, connected });
      }
      return response({
        ok: true,
        live: false,
        mode: "simulation",
        connectors: [{ ...connector, connected }],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ConnectorBoard />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable EFRIS" }));

    await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument());
    const toggleCall = fetchMock.mock.calls.find(([url]) => String(url) === "/api/v1/connectors/efris/toggle");
    expect(toggleCall).toBeDefined();
    expect(JSON.parse(String(toggleCall?.[1]?.body))).toEqual({ enable: false });
    expect(toggleCall?.[1]?.headers).toBeDefined();
  });

  const diagnostics = {
    ok: true,
    enabled: true,
    environment: "development",
    protocol_version: "2026-07-28",
    servers: [
      {
        namespace: "efris",
        label: "EFRIS",
        mode: "simulation",
        status: "ready",
        reachable: true,
        credential: "shared_token",
        tools_listed: 2,
        tools_offered: 1,
        tools: [
          {
            name: "efris_taxpayer_status",
            title: "Taxpayer status",
            problems: [],
            risk: "low",
            required_scopes: [],
            offered_to: ["public", "verified_taxpayer"],
          },
          {
            name: "efris_issue_invoice",
            title: "Issue invoice",
            problems: ["not_read_only", "needs_confirmation"],
            risk: "high",
            required_scopes: [],
            offered_to: [],
          },
        ],
      },
      {
        namespace: "payment_system",
        label: "Payments",
        mode: "simulation",
        status: "not_answering",
        reachable: false,
        credential: "none",
        tools_listed: 0,
        tools_offered: 0,
        tools: [],
      },
    ],
    refused_bindings: [{ namespace: "ledger", variable: "MCP_SERVER_URL_LEDGER", problem: "it is not an http(s) URL" }],
  };

  function withDiagnostics() {
    vi.stubGlobal("fetch", vi.fn((url: RequestInfo | URL) => {
      if (String(url) === "/api/v1/connectors/diagnostics") return response(diagnostics);
      if (String(url) === "/api/v1/connectors?view=chat") {
        return response({ ok: true, enabled: true, connectors: [chatConnector] });
      }
      return response({ ok: true, live: false, mode: "simulation", connectors: [connector] });
    }));
  }

  it("explains why each bound server is, or is not, offered in chat", async () => {
    withDiagnostics();
    render(<ConnectorBoard />);

    const panel = await screen.findByRole("region", { name: "Onboarding diagnostics" });
    const summary = await within(panel).findByTestId("connector-diagnostics-summary");
    expect(summary).toHaveTextContent("Chat connectors are switched on");
    expect(summary).toHaveTextContent("1 of 2 operations qualify for chat");

    const servers = within(panel).getByRole("list", { name: "Bound MCP servers" });
    const efris = within(servers).getAllByRole("listitem").find((item) => item.textContent?.includes("EFRIS"));
    expect(efris).toHaveTextContent("Offered in chat");
    expect(efris).toHaveTextContent("Shared token");
    expect(efris).toHaveTextContent("1 of 2 operations offered");
    expect(efris).toHaveTextContent(
      "Issue invoice — is not declared read-only (readOnlyHint: true); requires confirmation, which a chat lookup cannot give",
    );
    expect(efris).toHaveTextContent("Taxpayer status — offered to anonymous taxpayers and signed-in taxpayers");

    const payments = within(servers).getAllByRole("listitem").find((item) => item.textContent?.includes("Payments"));
    expect(payments).toHaveTextContent("Not answering");
    expect(payments).toHaveTextContent("No credential");

    const refused = within(panel).getByRole("list", { name: "Settings that did not bind a server" });
    expect(refused).toHaveTextContent("MCP_SERVER_URL_LEDGER");
    expect(refused).toHaveTextContent("it is not an http(s) URL");
    // Addresses never reach the console, only the variable that was refused.
    expect(panel.textContent).not.toContain("://");
  });

  it("checks each setup step against the deployment as it is now", async () => {
    withDiagnostics();
    render(<ConnectorBoard />);
    await screen.findByTestId("connector-diagnostics-summary");

    fireEvent.click(screen.getByRole("button", { name: "Add connector" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a connector" });
    const steps = within(dialog).getAllByRole("listitem");

    expect(steps[1]).toHaveTextContent("Bind it to the deployment");
    expect(steps[1]).toHaveTextContent("Needs attention");
    expect(steps[1]).toHaveTextContent("2 servers bound, 1 not answering, some without a usable credential, 1 setting refused.");
    expect(steps[2]).toHaveTextContent("Needs attention");
    expect(steps[2]).toHaveTextContent("1 of 2 operations qualify");
    expect(steps[3]).toHaveTextContent("Done");
  });

  it("says when diagnostics could not be read, and leaves the steps unchecked", async () => {
    vi.stubGlobal("fetch", vi.fn((url: RequestInfo | URL) => {
      if (String(url) === "/api/v1/connectors/diagnostics") return response({ ok: false });
      if (String(url) === "/api/v1/connectors?view=chat") {
        return response({ ok: true, enabled: false, connectors: [] });
      }
      return response({ ok: true, live: false, mode: "simulation", connectors: [connector] });
    }));
    render(<ConnectorBoard />);

    const panel = await screen.findByRole("region", { name: "Onboarding diagnostics" });
    expect(await within(panel).findByText("Connector diagnostics are unavailable.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add connector" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a connector" });
    expect(within(dialog).getAllByText("Not checked")).toHaveLength(3);
  });
});

