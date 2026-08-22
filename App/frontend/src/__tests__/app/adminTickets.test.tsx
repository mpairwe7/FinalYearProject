/**
 * Staff ticket queue.
 *
 * Two properties carry the feature and both are easy to lose in a UI:
 *
 * - The transcript is rendered in full. Summarising it would recreate
 *   the exact problem the ticket exists to solve — the taxpayer having
 *   to explain themselves again.
 * - `officer_reply` and `staff_note` are separate inputs going to
 *   separate fields. One reaches the taxpayer; one never does. A single
 *   merged "notes" box would be a privacy incident.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaffTicketQueue } from "../../app/admin/tickets/page";
import { analyticsApi } from "../../services/analyticsApi";

const TICKET = {
  id: "tkt-1",
  conversation_id: "conv-1",
  status: "open",
  priority: "high",
  reason: "User explicitly asked for a human",
  user_query: "my TIN is not working",
  bot_reply: "I could not resolve that.",
  created_at: Date.now() / 1000 - 7200,
  updated_at: Date.now() / 1000,
  handoff: {
    summary: "User needs human help with account specific.",
    topic: "account_specific",
    sentiment: "frustration",
    transfer_style: "warm",
    opening_guidance: "Acknowledge the frustration before anything else.",
    required_details: ["TIN", "date of the failed attempt"],
  },
};

const DETAIL = {
  ...TICKET,
  staff_note: "INTERNAL: caller verified by phone",
  transcript: [
    { user_message: "my TIN is not working", bot_reply: "Let me check.", created_at: 1 },
    { user_message: "still nothing", bot_reply: "I could not resolve that.", created_at: 2 },
  ],
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <StaffTicketQueue />
    </QueryClientProvider>,
  );
}

describe("StaffTicketQueue", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/tickets");
    vi.restoreAllMocks();
    vi.spyOn(analyticsApi, "tickets").mockResolvedValue({
      count: 1,
      status_filter: "open",
      limit: 50,
      offset: 0,
      teams: ["customs", "disputes", "general"],
      tickets: [TICKET],
    });
    vi.spyOn(analyticsApi, "ticket").mockResolvedValue(DETAIL);
    vi.spyOn(analyticsApi, "ticketSla").mockResolvedValue({
      period_days: 30,
      tickets: 4,
      responded: 3,
      resolved: 2,
      awaiting_first_response: 1,
      median_response_seconds: 900,
      median_resolution_seconds: 7200,
    });
    vi.spyOn(analyticsApi, "updateTicket").mockResolvedValue({ status: "ok" });
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({ status: "ok", viewers: [] });
  });

  it("lists queued tickets with their priority", async () => {
    renderPage();
    expect(await screen.findByText(/User explicitly asked for a human/)).toBeInTheDocument();
    // Scoped to the queue: "high" is also a filter button.
    const queue = screen.getByLabelText("Ticket queue");
    expect(within(queue).getByText("high")).toBeInTheDocument();
  });

  it("surfaces how long a taxpayer has been waiting", async () => {
    renderPage();
    // Seeded two hours ago.
    expect(await screen.findByTitle(/waiting since/)).toHaveTextContent("2h");
  });

  it("shows what is still awaiting a first response", async () => {
    renderPage();
    expect(await screen.findByText(/Awaiting first response/)).toBeInTheDocument();
  });

  it("renders the whole transcript, not a summary", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    // Both turns, both sides.
    expect(await screen.findByText("Let me check.")).toBeInTheDocument();
    expect(screen.getByText("still nothing")).toBeInTheDocument();
    expect(screen.getByText(/2 turns/)).toBeInTheDocument();
  });

  it("flags a warm transfer and shows the opening guidance", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    expect(await screen.findByText("warm transfer")).toBeInTheDocument();
    expect(screen.getByText(/Acknowledge the frustration/)).toBeInTheDocument();
  });

  it("sends an officer reply to officer_reply, not staff_note", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    const box = await screen.findByLabelText("Reply to the taxpayer");
    fireEvent.change(box, { target: { value: "Your TIN was reactivated." } });
    fireEvent.click(screen.getByRole("button", { name: /send reply/i }));

    await waitFor(() =>
      expect(analyticsApi.updateTicket).toHaveBeenCalledWith("tkt-1", {
        officer_reply: "Your TIN was reactivated.",
      }),
    );
  });

  it("sends an internal note to staff_note, not officer_reply", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    const box = await screen.findByLabelText("Internal note");
    fireEvent.change(box, { target: { value: "called, no answer" } });
    fireEvent.click(screen.getByRole("button", { name: /save note/i }));

    await waitFor(() =>
      expect(analyticsApi.updateTicket).toHaveBeenCalledWith("tkt-1", {
        staff_note: "called, no answer",
      }),
    );
  });

  it("inserts a canned reply without sending it", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    fireEvent.click(await screen.findByRole("button", { name: "Acknowledge the wait" }));
    const box = screen.getByLabelText("Reply to the taxpayer") as HTMLTextAreaElement;
    expect(box.value).toMatch(/Thank you for waiting/);
    expect(analyticsApi.updateTicket).not.toHaveBeenCalled();
  });

  it("blocks a reply when the case is assigned to someone else", async () => {
    vi.spyOn(analyticsApi, "ticket").mockResolvedValue({
      ...DETAIL,
      assignee: "other@ura.go.ug",
    });
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, refetchInterval: false } },
          })
        }
      >
        <StaffTicketQueue
          who={{ authenticated: true, role: "ura_staff", email: "officer@ura.go.ug" }}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    expect(await screen.findByText(/Assigned to other@ura.go.ug/)).toBeInTheDocument();
    const box = await screen.findByLabelText("Reply to the taxpayer");
    fireEvent.change(box, { target: { value: "Your TIN was reactivated." } });
    expect(screen.getByRole("button", { name: /send reply/i })).toBeDisabled();
  });

  it("will not send an empty reply", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    expect(await screen.findByRole("button", { name: /send reply/i })).toBeDisabled();
  });

  it("changes status through the API", async () => {
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    // Wait for the detail to load, then scope to it: "resolved" is also
    // a status filter in the toolbar.
    await screen.findByText("Let me check.");
    const detail = screen.getByLabelText("Ticket detail");
    fireEvent.click(within(detail).getByRole("button", { name: /resolved/i }));
    await waitFor(() =>
      expect(analyticsApi.updateTicket).toHaveBeenCalledWith("tkt-1", { status: "resolved" }),
    );
  });

  it("filters the queue by priority", async () => {
    renderPage();
    await screen.findByText(/User explicitly asked for a human/);
    fireEvent.click(screen.getByRole("button", { name: "urgent" }));
    await waitFor(() =>
      expect(analyticsApi.tickets).toHaveBeenCalledWith("open", 50, "urgent", ""),
    );
  });

  it("filters the queue by team", async () => {
    renderPage();
    await screen.findByText(/User explicitly asked for a human/);
    fireEvent.click(await screen.findByRole("button", { name: "disputes" }));
    await waitFor(() =>
      expect(analyticsApi.tickets).toHaveBeenCalledWith("open", 50, "", "disputes"),
    );
  });

  it("takes the team list from the server, not a hardcoded org chart", async () => {
    renderPage();
    // Rendered from the response's `teams`, so a deployment that renames
    // a team does not need a frontend change.
    expect(await screen.findByRole("button", { name: "customs" })).toBeInTheDocument();
    // The reset control shows "any" under a "Team" group label; its accessible
    // name spells out what it resets, so it cannot collide with the priority
    // reset beside it.
    expect(screen.getByRole("button", { name: "All teams" })).toBeInTheDocument();
  });

  it("says so plainly when nothing is waiting", async () => {
    vi.spyOn(analyticsApi, "tickets").mockResolvedValue({
      count: 0,
      status_filter: "open",
      limit: 50,
      offset: 0,
      teams: [],
      tickets: [],
    });
    renderPage();
    expect(await screen.findByText("Nothing matches")).toBeInTheDocument();
    expect(screen.getByText(/No case is currently new/i)).toBeInTheDocument();
  });

  it("filters the queue by search text", async () => {
    renderPage();
    await screen.findByText(/User explicitly asked for a human/);
    fireEvent.change(screen.getByLabelText("Search tickets"), {
      target: { value: "no-such-case" },
    });
    expect(screen.getByText("Nothing matches")).toBeInTheDocument();
    expect(
      screen.getByText("No case in this status matches the current filters."),
    ).toBeInTheDocument();
  });

  it("claims a ticket for the signed-in officer", async () => {
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false, refetchInterval: false } },
          })
        }
      >
        <StaffTicketQueue
          who={{ authenticated: true, role: "ura_staff", email: "officer@ura.go.ug" }}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    fireEvent.click(await screen.findByRole("button", { name: /assign to me/i }));
    await waitFor(() =>
      expect(analyticsApi.updateTicket).toHaveBeenCalledWith("tkt-1", {
        assignee: "officer@ura.go.ug",
        status: "assigned",
      }),
    );
  });

  it("opens a ticket from the URL without a second click", async () => {
    window.history.replaceState(null, "", "/admin/tickets?ticket=tkt-1");
    renderPage();
    expect(await screen.findByText("Let me check.")).toBeInTheDocument();
  });

  it("does not claim a transcript it was not given", async () => {
    vi.spyOn(analyticsApi, "ticket").mockResolvedValue({ ...DETAIL, transcript: [] });
    renderPage();
    fireEvent.click(await screen.findByText(/User explicitly asked for a human/));
    expect(
      await screen.findByText("No transcript was captured for this ticket."),
    ).toBeInTheDocument();
  });
});
