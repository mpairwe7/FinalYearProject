/**
 * The taxpayer's side of a live chat with an officer.
 *
 * The flow: the case waits (the assistant still answers); an officer takes it
 * (a notice says who, their messages appear, the composer sends to them); the
 * officer ends it (the rating is asked once); then a notice hands the taxpayer
 * back to the assistant and the case is cleared. Nothing is shown twice — not
 * on a second poll, not after a reload — and nothing is appended while an
 * answer is streaming into the last turn.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useOfficerChat } from "../../hooks/useOfficerChat";
import { analyticsApi, type OfficerChatThread } from "../../services/analyticsApi";
import { createTurn, useChatStore } from "../../store/useChatStore";

const TICKET = "abc12345-6789";

function thread(patch: Partial<OfficerChatThread> = {}): OfficerChatThread {
  return {
    ticket_id: TICKET,
    chat_state: "waiting",
    officer_name: "",
    messages: [],
    last_seq: 0,
    csat: { requested: false, submitted: false },
    ...patch,
  };
}

const officerTurns = () => useChatStore.getState().chat.filter((t) => t.retrievalMode === "officer_reply");
const notices = () => useChatStore.getState().chat.filter((t) => t.notice).map((t) => t.notice);

beforeEach(() => {
  useChatStore.getState().reset();
  useChatStore.getState().ensureActiveConversationId();
  useChatStore.getState().addTurns([createTurn("user", "Why was I assessed twice?"), createTurn("assistant", "Let me pass you to an officer.")]);
  useChatStore.getState().setActiveTicketId(TICKET);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useOfficerChat", () => {
  it("stays out of the way while the case waits", async () => {
    const poll = vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(thread());
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(poll).toHaveBeenCalledWith(TICKET, 0));
    expect(result.current.state).toBe("waiting");
    expect(result.current.withOfficer).toBe(false);
    expect(notices()).toEqual([]);
  });

  it("announces the officer, shows their messages once, and remembers where it got to", async () => {
    const poll = vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(
      thread({
        chat_state: "officer",
        officer_name: "Officer Nakato",
        messages: [
          { seq: 1, sender: "officer", text: "Which period?", officer_name: "Officer Nakato", created_at: 1 },
          { seq: 2, sender: "taxpayer", text: "March 2026.", created_at: 2 },
        ],
        last_seq: 2,
      }),
    );
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(result.current.withOfficer).toBe(true));
    expect(result.current.officerName).toBe("Officer Nakato");
    expect(notices()).toEqual(["officer_joined"]);
    // The taxpayer's own message was on screen already; only the officer's is added.
    expect(officerTurns().map((t) => [t.content, t.officerName])).toEqual([["Which period?", "Officer Nakato"]]);
    expect(useChatStore.getState().officerChat).toMatchObject({ ticketId: TICKET, lastSeq: 2, state: "officer" });
    const conv = useChatStore.getState().conversations.find((c) => c.id === useChatStore.getState().activeConversationId);
    expect(conv?.officerChat?.lastSeq).toBe(2);

    // A second poll that hears the same message again adds nothing.
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await waitFor(() => expect(poll).toHaveBeenCalledTimes(2));
    expect(poll).toHaveBeenLastCalledWith(TICKET, 2);
    expect(officerTurns()).toHaveLength(1);
    expect(notices()).toEqual(["officer_joined"]);
  });

  it("sends the taxpayer's message to the officer and shows it", async () => {
    vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(thread({ chat_state: "officer", officer_name: "Officer Nakato" }));
    const reply = vi.spyOn(analyticsApi, "replyToPublicTicket").mockResolvedValue({ ok: true, ticket_id: TICKET, status: "assigned", message: "ok" });
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(result.current.withOfficer).toBe(true));
    let delivered = false;
    await act(async () => {
      delivered = await result.current.sendToOfficer("Here is my PRN.");
    });
    expect(delivered).toBe(true);
    expect(reply).toHaveBeenCalledWith(TICKET, "Here is my PRN.", "en");
    expect(useChatStore.getState().chat.at(-1)).toMatchObject({ role: "user", content: "Here is my PRN." });
  });

  it("keeps the message when it did not reach the officer", async () => {
    vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(thread({ chat_state: "officer" }));
    vi.spyOn(analyticsApi, "replyToPublicTicket").mockRejectedValue(new Error("500 Internal Server Error"));
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(result.current.withOfficer).toBe(true));
    let delivered = true;
    await act(async () => {
      delivered = await result.current.sendToOfficer("Hello?");
    });
    expect(delivered).toBe(false);
    expect(result.current.sendFailed).toBe(true);
    expect(useChatStore.getState().chat.some((t) => t.content === "Hello?")).toBe(false);
  });

  it("asks for the rating once the officer ends the chat, then hands back to the assistant", async () => {
    vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(
      thread({ chat_state: "ended", officer_name: "Officer Nakato", csat: { requested: true, submitted: false }, last_seq: 3 }),
    );
    const csat = vi.spyOn(analyticsApi, "submitTicketCsat").mockResolvedValue({ ok: true, ticket_id: TICKET, status: "answered" });
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(result.current.ratingOpen).toBe(true));
    await act(async () => {
      await result.current.submitRating(4, "Helpful.");
    });
    expect(csat).toHaveBeenCalledWith(TICKET, 4, "Helpful.");
    expect(notices()).toEqual(["officer_left"]);
    expect(useChatStore.getState().activeTicketId).toBeNull();
    expect(useChatStore.getState().officerChat).toBeNull();
    expect(result.current.state).toBeNull();
    expect(result.current.ratingOpen).toBe(false);
  });

  it("counts a skip, and hands back without waiting for it", async () => {
    vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(
      thread({ chat_state: "ended", csat: { requested: true, submitted: false } }),
    );
    const csat = vi.spyOn(analyticsApi, "submitTicketCsat").mockReturnValue(new Promise(() => undefined));
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(result.current.ratingOpen).toBe(true));
    act(() => result.current.skipRating());
    expect(csat).toHaveBeenCalledWith(TICKET, null);
    expect(notices()).toEqual(["officer_left"]);
    expect(useChatStore.getState().activeTicketId).toBeNull();
  });

  it("does not ask again for a chat already rated (another tab)", async () => {
    vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(
      thread({ chat_state: "ended", csat: { requested: true, submitted: true } }),
    );
    const { result } = renderHook(() => useOfficerChat());
    await waitFor(() => expect(notices()).toEqual(["officer_left"]));
    expect(result.current.ratingOpen).toBe(false);
  });

  it("appends nothing while an answer is streaming, and catches up when it finishes", async () => {
    const poll = vi.spyOn(analyticsApi, "publicTicketMessages").mockResolvedValue(
      thread({
        chat_state: "officer",
        officer_name: "Officer Nakato",
        messages: [{ seq: 1, sender: "officer", text: "I have your case.", created_at: 1 }],
        last_seq: 1,
      }),
    );
    const { rerender } = renderHook(({ hold }) => useOfficerChat({ hold }), { initialProps: { hold: true } });
    await new Promise((r) => setTimeout(r, 20));
    expect(poll).not.toHaveBeenCalled();
    expect(officerTurns()).toHaveLength(0);
    rerender({ hold: false });
    await waitFor(() => expect(officerTurns()).toHaveLength(1));
  });

  it("stops following a case that is gone", async () => {
    const poll = vi.spyOn(analyticsApi, "publicTicketMessages").mockRejectedValue(new Error("404 Not Found"));
    renderHook(() => useOfficerChat());
    await waitFor(() => expect(poll).toHaveBeenCalledTimes(1));
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await new Promise((r) => setTimeout(r, 20));
    expect(poll).toHaveBeenCalledTimes(1);
  });
});
