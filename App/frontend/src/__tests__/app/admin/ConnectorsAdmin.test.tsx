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
});
