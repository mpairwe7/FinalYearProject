/**
 * The officer's view of a live chat with a taxpayer, and ending it.
 *
 * The chat is its own thread, in order: the taxpayer's messages after
 * escalation are no longer rendered as empty "Assistant" lines in the
 * escalation transcript. Ending the chat asks first — the taxpayer is told,
 * asked to rate it and handed back to the assistant — and says how it ended.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TicketCase } from "../../components/staff/TicketCase";
import { analyticsApi, type TicketDetail } from "../../services/analyticsApi";

const NOW = Date.now() / 1000;

const LIVE: TicketDetail = {
  id: "tkt-chat-1",
  status: "assigned",
  priority: "normal",
  reason: "VAT assessed twice",
  created_at: NOW - 3600,
  assignee: "officer@ura.go.ug",
  locale: "en",
  modality: "text",
  transcript: [
    { user_message: "Why was I assessed twice?", bot_reply: "Let me pass you to an officer.", created_at: NOW - 3600 },
    { user_message: "March 2026.", bot_reply: "", created_at: NOW - 800, sender: "taxpayer" },
  ],
  messages: [
    { seq: 1, sender: "officer", text: "Which period?", officer_name: "Officer Nakato", created_at: NOW - 900 },
    { seq: 2, sender: "taxpayer", text: "March 2026.", created_at: NOW - 800 },
  ],
  chat: { state: "officer", officer_name: "Officer Nakato", ended_at: null, ended_by_name: "", outcome: "", rating: null },
} as TicketDetail;

function renderCase(ticket: TicketDetail, role = "ura_staff") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onPatch = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <TicketCase
        ticket={ticket}
        who={{ authenticated: true, role, email: "officer@ura.go.ug" }}
        pending={false}
        isError={false}
        isSuccess={false}
        onPatch={onPatch}
      />
    </QueryClientProvider>,
  );
  return { onPatch };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TicketCase — live chat", () => {
  it("shows the chat as its own thread, and the escalation transcript without the follow-ups", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase(LIVE);
    const chat = screen.getByRole("region", { name: "Chat with the taxpayer" });
    const items = within(chat).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector(".st-turn-who")?.textContent)).toEqual(["Officer Nakato", "Taxpayer"]);
    expect(chat.textContent).toMatch(/live — Officer Nakato has the chat, the assistant is paused/);
    const transcript = screen.getByRole("region", { name: "Conversation as it stood at escalation" });
    expect(within(transcript).getAllByRole("listitem")).toHaveLength(1);
    expect(transcript.textContent).not.toContain("March 2026.");
  });

  it("shows a case from before the thread existed in the same shape", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase({ ...LIVE, messages: [], officer_reply: "Cancelled the duplicate.", reply_at: NOW - 700 } as TicketDetail);
    const chat = screen.getByRole("region", { name: "Chat with the taxpayer" });
    expect(within(chat).getAllByRole("listitem").map((li) => li.querySelector("p")?.textContent)).toEqual([
      "March 2026.",
      "Cancelled the duplicate.",
    ]);
  });

  it("ends the chat only after asking, with the reply as the closing message", async () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    const endChat = vi
      .spyOn(analyticsApi, "endChat")
      .mockResolvedValue({ status: "ok", ticket_id: LIVE.id, chat_state: "ended" });
    renderCase(LIVE);
    expect(screen.getByRole("button", { name: "Send & end chat" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Reply to the taxpayer" }), {
      target: { value: "I have cancelled the duplicate. Goodbye." },
    });
    fireEvent.click(screen.getByRole("button", { name: "End chat" }));
    const panel = screen.getByRole("group", { name: "End the chat" });
    expect(panel.textContent).toMatch(/asked to rate it, and handed back to the\s+assistant/);
    expect(panel.textContent).toContain("Your reply above is sent as the closing message.");
    fireEvent.click(within(panel).getByRole("radio", { name: "Closed without resolution" }));
    fireEvent.click(within(panel).getByRole("button", { name: "End chat" }));
    await waitFor(() =>
      expect(endChat).toHaveBeenCalledWith(LIVE.id, {
        outcome: "wontfix",
        closing_message: "I have cancelled the duplicate. Goodbye.",
        closing_message_localized: undefined,
      }),
    );
    await waitFor(() => expect(screen.queryByRole("group", { name: "End the chat" })).not.toBeInTheDocument());
  });

  it("keeps chatting when the officer changes their mind", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    const endChat = vi.spyOn(analyticsApi, "endChat");
    renderCase(LIVE);
    fireEvent.click(screen.getByRole("button", { name: "End chat" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep chatting" }));
    expect(screen.queryByRole("group", { name: "End the chat" })).not.toBeInTheDocument();
    expect(endChat).not.toHaveBeenCalled();
  });

  it("shows how an ended chat was rated, and offers no End chat", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase({
      ...LIVE,
      status: "resolved",
      messages: [...(LIVE.messages ?? []), { seq: 3, sender: "system", text: "chat_ended", created_at: NOW - 600 }],
      chat: {
        state: "ended",
        officer_name: "Officer Nakato",
        ended_at: NOW - 600,
        ended_by_name: "Officer Nakato",
        outcome: "resolved",
        rating: { status: "answered", score: 2, comment: "Slow." },
      },
    } as TicketDetail);
    const chat = screen.getByRole("region", { name: "Chat with the taxpayer" });
    expect(chat.textContent).toMatch(/Chat ended by Officer Nakato · resolved/);
    expect(chat.textContent).toMatch(/Taxpayer rated the chat 2\/5 · Dissatisfied/);
    expect(screen.queryByRole("button", { name: "End chat" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send and resolve" })).toBeInTheDocument();
  });

  it("is read-only for an auditor", () => {
    renderCase(LIVE, "ura_auditor");
    expect(screen.getByRole("region", { name: "Chat with the taxpayer" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End chat" })).not.toBeInTheDocument();
    expect(screen.getByText(/Read-only view/)).toBeInTheDocument();
  });

  it("shows no chat on a call's ticket", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase({ ...LIVE, modality: "voice", messages: [], chat: undefined } as TicketDetail);
    expect(screen.queryByRole("region", { name: "Chat with the taxpayer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "End chat" })).not.toBeInTheDocument();
  });
});

describe("TicketCase — the handoff brief (Phase 38)", () => {
  it("says why the ticket is prioritised, how fast to answer, and what not to ask again", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase({
      ...LIVE,
      priority: "urgent",
      handoff: {
        summary: "User needs human help with account specific.",
        required_details: ["any reference number shown in the URA portal"],
        escalation: {
          tier: "priority",
          priority: "urgent",
          sla_seconds: 30,
          affect: "hardship",
          reasons: ["affect:hardship", "stakes:enforcement"],
        },
        already_given: ["tax type", "filing period"],
      },
    } as TicketDetail);

    const why = screen.getByTestId("handoff-escalation");
    expect(why).toHaveTextContent("Prioritised: in hardship · enforcement action (account, goods or premises)");
    expect(why).toHaveTextContent("respond within 30 s");
    expect(screen.getByTestId("handoff-already-given")).toHaveTextContent(
      "Already given — do not ask again: tax type, filing period",
    );
    expect(screen.getByText("any reference number shown in the URA portal")).toBeInTheDocument();
  });

  it("shows nothing extra on a ticket raised before the policy existed", () => {
    vi.spyOn(analyticsApi, "heartbeatPresence").mockResolvedValue({} as never);
    renderCase({ ...LIVE, handoff: { summary: "Needs help." } } as TicketDetail);
    expect(screen.queryByTestId("handoff-escalation")).not.toBeInTheDocument();
    expect(screen.queryByTestId("handoff-already-given")).not.toBeInTheDocument();
  });
});
