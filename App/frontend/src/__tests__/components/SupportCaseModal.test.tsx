import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SupportCaseModal } from "../../components/SupportCaseModal";
import { analyticsApi } from "../../services/analyticsApi";
import { useChatStore } from "../../store/useChatStore";

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

  it("is the case's details, not a second place to write, while the officer chats in the main conversation", async () => {
    vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue(mockCase);
    render(
      <SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId="abcdef12-3456-7890-abcd-ef1234567890" liveChat />
    );
    expect(await screen.findByText(/Rental tax calculation dispute/i)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Your chat with the officer continues in the main conversation.");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
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

  // G628: the room said "A URA Officer is Reviewing Your Case … has been
  // assigned" whatever the case's status, a load failure included.
  describe("says where the case really stands", () => {
    const TID = "abcdef12-3456-7890-abcd-ef1234567890";
    const waiting = { ...mockCase, officer_reply: "", reply_at: 0 };

    it("an open case is waiting for an officer, not being reviewed", async () => {
      vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue({ ...waiting, status: "open" });
      render(<SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId={TID} />);
      expect(await screen.findByText(/waiting for an officer to pick it up/i)).toBeInTheDocument();
      expect(screen.queryByText(/Reviewing Your Case/i)).toBeNull();
      expect(screen.queryByText(/has been assigned/i)).toBeNull();
    });

    it("an assigned case names the team that has it", async () => {
      vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue({ ...waiting, status: "assigned" });
      render(<SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId={TID} />);
      expect(await screen.findByText(/An officer in Domestic Taxes - Objections & Advisory has your case/i)).toBeInTheDocument();
    });

    it("a case closed without action says so and how to get help", async () => {
      vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue({ ...waiting, status: "wontfix", can_reply: false });
      render(<SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId={TID} />);
      expect(await screen.findByText(/closed without further action/i)).toBeInTheDocument();
      expect(screen.queryByText(/waiting for an officer/i)).toBeNull();
    });

    it("a case that cannot be loaded promises nothing", async () => {
      vi.spyOn(analyticsApi, "publicTicketStatus").mockRejectedValue(new Error("404 Not Found"));
      render(<SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId={TID} />);
      expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
      expect(screen.queryByText(/waiting for an officer/i)).toBeNull();
      expect(screen.queryByText(/Reviewing Your Case/i)).toBeNull();
    });

    it("speaks the taxpayer's language", async () => {
      useChatStore.setState({ locale: "sw" });
      try {
        vi.spyOn(analyticsApi, "publicTicketStatus").mockResolvedValue({ ...waiting, status: "open" });
        render(<SupportCaseModal isOpen={true} onClose={vi.fn()} ticketId={TID} />);
        expect(await screen.findByText(/inasubiri afisa aichukue/i)).toBeInTheDocument();
      } finally {
        useChatStore.setState({ locale: "en" });
      }
    });
  });
});
