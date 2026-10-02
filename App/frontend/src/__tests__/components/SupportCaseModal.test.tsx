import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SupportCaseModal } from "../../components/SupportCaseModal";
import { analyticsApi } from "../../services/analyticsApi";

const mockCase = {
  ok: true,
  ticket_id: "abcdef12-3456-7890-abcd-ef1234567890",
  reference: "TIC-ABCDEF12",
  status: "assigned",
  status_label: "In Review by URA Officer",
  priority: "normal",
  team: "domestic_taxes",
  team_label: "Domestic Taxes - Objections & Advisory",
  assignee: "officer.wamala@ura.go.ug",
  assignee_display: "Officer Wamala (Senior Tax Officer)",
  reason: "Rental tax calculation dispute",
  user_query: "My rental expense deduction was disallowed",
  officer_reply: "I reviewed your rental schedule and approved the 50% deduction.",
  reply_at: 1789726800,
  reply_delivered: true,
  created_at: 1789726400,
  resolved_at: 0,
  transcript: [
    { user_message: "My rental deduction was disallowed", bot_reply: "Let me check" },
  ],
  can_reply: true,
};

describe("SupportCaseModal", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render when closed or no ticketId", () => {
    const { container } = render(
      <SupportCaseModal isOpen={false} onClose={vi.fn()} ticketId="abcdef12" />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders case reference, status, and verified officer reply", async () => {
    vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValueOnce(mockCase);

    render(
      <SupportCaseModal
        isOpen={true}
        onClose={vi.fn()}
        ticketId="abcdef12-3456-7890-abcd-ef1234567890"
      />
    );

    expect(await screen.findByText(/Rental tax calculation dispute/i)).toBeInTheDocument();
    expect(screen.getByText("#TIC-ABCDEF12")).toBeInTheDocument();
    expect(screen.getByText(/Officer Wamala/i)).toBeInTheDocument();
    expect(screen.getByText(/I reviewed your rental schedule/i)).toBeInTheDocument();
    expect(screen.getByText(/Domestic Taxes/i)).toBeInTheDocument();
  });

  it("allows taxpayer to type and send a follow-up reply", async () => {
    vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue(mockCase);
    const replySpy = vi.spyOn(analyticsApi, "replyToPublicTicket").mockResolvedValueOnce({
      ok: true,
      ticket_id: mockCase.ticket_id,
      status: "assigned",
      message: "Received",
    });

    render(
      <SupportCaseModal
        isOpen={true}
        onClose={vi.fn()}
        ticketId={mockCase.ticket_id}
      />
    );

    await screen.findByText(/Rental tax calculation dispute/i);
    const textarea = screen.getByPlaceholderText(/Type your message or PRN here/i);
    await userEvent.type(textarea, "Here is my PRN: 2260012345678");

    const sendBtn = screen.getByRole("button", { name: /Send to Officer/i });
    expect(sendBtn).not.toBeDisabled();
    await userEvent.click(sendBtn);

    expect(replySpy).toHaveBeenCalledWith(mockCase.ticket_id, "Here is my PRN: 2260012345678");
    expect(await screen.findByText(/Message sent to officer/i)).toBeInTheDocument();
  });

  it("calls onClose when close button clicked", async () => {
    vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValueOnce(mockCase);
    const onClose = vi.fn();

    render(
      <SupportCaseModal
        isOpen={true}
        onClose={onClose}
        ticketId={mockCase.ticket_id}
      />
    );

    await screen.findByText(/Rental tax calculation dispute/i);
    const closeBtn = screen.getByRole("button", { name: /Close support case room/i });
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalled();
  });
});
