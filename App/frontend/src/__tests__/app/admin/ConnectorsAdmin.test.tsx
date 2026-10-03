import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function response(body: unknown) {
  return Promise.resolve({ ok: true, json: async () => body });
}

describe("connector admin board", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("labels connector health as local simulation status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ok: true,
      live: false,
      mode: "simulation",
      connectors: [connector],
    })));

    render(<ConnectorBoard />);

    expect(await screen.findByText("These are local simulations")).toBeInTheDocument();
    expect(screen.getByText(/healthy locally/)).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByText(/No live filing or payment is made/)).toBeInTheDocument();
  });

  it("waits for the authenticated toggle response before refreshing status", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ ok: true, live: false, mode: "simulation", connectors: [connector] }))
      .mockResolvedValueOnce(response({ ok: true, connected: false }))
      .mockResolvedValueOnce(response({ ok: true, live: false, mode: "simulation", connectors: [{ ...connector, connected: false }] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ConnectorBoard />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable EFRIS" }));

    await waitFor(() => expect(screen.getByText("Disabled")).toBeInTheDocument());
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/connectors/efris/toggle");
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ enable: false });
    expect(fetchMock.mock.calls[1][1]?.headers).toBeDefined();
  });
});
