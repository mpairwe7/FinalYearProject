import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OutboxBoard } from "../../../app/admin/outbox/page";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false },
  },
});

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

const mockOutboxData = {
  items: [
    {
      id: "nid-1234567890",
      user_id: "user-1",
      channel: "email",
      provider: "resend",
      status: "delivered",
      created_at: 1791230000,
      sent_at: 1791230001,
      delivered_at: 1791230002,
      provider_msg_id: "resend_msg_001",
      live: true,
      payload: { recipient: "taxpayer@ura.go.ug", message: "VAT reminder" },
    },
    {
      id: "nid-0987654321",
      user_id: "user-2",
      channel: "sms",
      provider: "africastalking",
      status: "queued",
      created_at: 1791230010,
      live: true,
      payload: { recipient: "+256701234567", message: "PAYE filing due" },
    },
  ],
  live: true,
  providers: {
    email: { configured: true, backend: "resend", status: "active", live: true },
    sms: { configured: true, backend: "africastalking", status: "active", live: true },
    webhook: { configured: true, backend: "http_hmac", status: "active", live: true },
    in_app: { configured: true, backend: "sqlite_inbox", status: "active", live: true },
  },
  stats: {
    total: 2,
    queued: 1,
    sent: 0,
    delivered: 1,
    failed: 0,
  },
};

describe("OutboxAdmin Board", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    queryClient.clear();
  });

  it("renders live notification engine status and provider indicators", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockOutboxData,
      })
    );

    render(<OutboxBoard />, { wrapper });

    expect(await screen.findByText("Live Notification Engine Active")).toBeInTheDocument();
    expect(screen.getByText("EMAIL: resend")).toBeInTheDocument();
    expect(screen.getByText("SMS: africastalking")).toBeInTheDocument();
    expect(screen.getByText("taxpayer@ura.go.ug")).toBeInTheDocument();
    expect(screen.getByText("+256701234567")).toBeInTheDocument();
    expect(screen.getByText("delivered")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("opens test notification form and allows input", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockOutboxData,
      })
    );

    render(<OutboxBoard />, { wrapper });

    const testBtn = await screen.findByText("✉️ Send Live Test");
    fireEvent.click(testBtn);

    expect(screen.getByText("Send Live Test Notification")).toBeInTheDocument();
    expect(screen.getByDisplayValue("taxpayer@ura.go.ug")).toBeInTheDocument();
    expect(screen.getByText("Send Now")).toBeInTheDocument();
  });
});
