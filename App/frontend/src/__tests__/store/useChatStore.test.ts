/**
 * useChatStore — Zustand store unit tests.
 *
 * Covers: state shape, addTurns cap, updateLastTurn, locale switch, reset.
 * Aligned with RTM REQ-08 (loading indicator via state) and ISO 25010 §7.
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  useChatStore,
  createTurn,
  normalizeAssistantResponse,
  type ChatTurn,
} from "../../store/useChatStore";

const store = () => useChatStore.getState();

beforeEach(() => {
  useChatStore.getState().reset();
});

describe("useChatStore", () => {
  it("initialises with a greeting message", () => {
    expect(store().chat).toHaveLength(1);
    expect(store().chat[0].role).toBe("assistant");
    expect(store().chat[0].id).toBe("greeting-0");
  });

  it("setMessage updates the input field", () => {
    store().setMessage("Hello URA");
    expect(store().message).toBe("Hello URA");
  });

  it("addTurns appends user and assistant turns", () => {
    const user = createTurn("user", "What is VAT?");
    const bot = createTurn("assistant", "VAT is 18% in Uganda.", {
      faithfulnessScore: 0.85,
      retrievalMode: "hybrid",
    });

    store().addTurns([user, bot]);
    expect(store().chat).toHaveLength(3); // greeting + 2
    expect(store().chat[1].role).toBe("user");
    expect(store().chat[2].faithfulnessScore).toBe(0.85);
  });

  it("caps chat at 200 turns (MAX_CHAT_TURNS)", () => {
    const turns: ChatTurn[] = Array.from({ length: 210 }, (_, i) =>
      createTurn("user", `Message ${i}`),
    );
    store().addTurns(turns);
    expect(store().chat.length).toBeLessThanOrEqual(200);
  });

  it("updateLastTurn streams content into the latest message", () => {
    const bot = createTurn("assistant", "");
    store().addTurns([bot]);

    store().updateLastTurn((t) => ({ ...t, content: t.content + "Hello" }));
    store().updateLastTurn((t) => ({ ...t, content: t.content + " world" }));

    const last = store().chat[store().chat.length - 1];
    expect(last.content).toBe("Hello world");
  });

  it("updateLastTurn is a no-op on empty chat", () => {
    // Clear chat manually
    useChatStore.setState({ chat: [] });
    store().updateLastTurn((t) => ({ ...t, content: "nope" }));
    expect(store().chat).toHaveLength(0);
  });

  it("setLocale switches language", () => {
    expect(store().locale).toBe("en");
    store().setLocale("lg");
    expect(store().locale).toBe("lg");
  });

  /* The answer language: auto-detected until the taxpayer picks one. The
     backend took the picker's default "en" as a choice and answered Luganda
     and Swahili in English (PR #529); `locale_explicit` is how it can tell. */
  it("starts in auto-detect, and a picked language becomes explicit", () => {
    expect(store().localeExplicit).toBe(false);
    store().setLocale("sw");
    expect(store().locale).toBe("sw");
    expect(store().localeExplicit).toBe(true);
  });

  it("setAutoDetectLocale returns to auto-detect without changing the hint", () => {
    store().setLocale("lg");
    store().setAutoDetectLocale();
    expect(store().localeExplicit).toBe(false);
    expect(store().locale).toBe("lg");
  });

  it("a detected or continued response language stays a hint", () => {
    store().adoptResponseLocale("lg", "detected");
    expect(store().locale).toBe("lg");
    expect(store().localeExplicit).toBe(false);
    store().adoptResponseLocale("en", "switched");
    expect(store().locale).toBe("en");
    expect(store().localeExplicit).toBe(false);
  });

  it("a language the taxpayer typed a request for becomes their choice", () => {
    store().adoptResponseLocale("sw", "explicit_request");
    expect(store().locale).toBe("sw");
    expect(store().localeExplicit).toBe(true);
  });

  it("a picked language survives a reload; auto-detect is the default for old state", () => {
    store().setLocale("lg");
    useChatStore.setState({ locale: "en", localeExplicit: false });
    store().hydratePersisted();
    expect(store().locale).toBe("lg");
    expect(store().localeExplicit).toBe(true);

    const raw = JSON.parse(localStorage.getItem("ura-chat-store") || "{}");
    delete raw.state.localeExplicit;
    localStorage.setItem("ura-chat-store", JSON.stringify(raw));
    store().hydratePersisted();
    expect(store().localeExplicit).toBe(false);
  });

  it("a reload mid-case resumes the case, its live chat and the answer ids it rated", () => {
    store().ensureActiveConversationId();
    store().addTurns([
      createTurn("user", "Why was I assessed twice?"),
      createTurn("assistant", "Let me check.", { serverTurnId: "turn-1" }),
      { ...createTurn("assistant", "Officer Nakato has joined."), notice: "officer_joined", officerName: "Officer Nakato" },
    ]);
    store().setActiveTicketId("ticket-1");
    store().setOfficerChat({ ticketId: "ticket-1", lastSeq: 4, state: "officer", officerName: "Officer Nakato" });
    useChatStore.setState({ activeTicketId: null, officerChat: null, chat: [] });

    store().hydratePersisted();
    expect(store().activeTicketId).toBe("ticket-1");
    expect(store().officerChat).toEqual({ ticketId: "ticket-1", lastSeq: 4, state: "officer", officerName: "Officer Nakato" });
    expect(store().chat.find((t) => t.content === "Let me check.")?.serverTurnId).toBe("turn-1");
    expect(store().chat.find((t) => t.notice)).toMatchObject({ notice: "officer_joined", officerName: "Officer Nakato" });
  });

  it("a restored objective card only offers the guided-start action the server issues", () => {
    store().ensureActiveConversationId();
    const hypothesis = {
      objective_id: "file_return",
      title: "File a URA tax return",
      domain: "return_filing",
      confidence: 0.92,
      workflow_id: "return_filing",
      confirm_action: "Guide me step by step through Return Filing",
    };
    store().addTurns([
      createTurn("user", "I need to file my return"),
      { ...createTurn("assistant", "kept"), objectiveHypothesis: hypothesis },
      { ...createTurn("assistant", "tampered"), objectiveHypothesis: { ...hypothesis } },
    ]);
    store().saveCurrentSession();

    // Plant what a tampered or foreign storage entry could hold.
    const raw = JSON.parse(localStorage.getItem("ura-chat-store") || "{}");
    const plant = (node: unknown): void => {
      if (Array.isArray(node)) return node.forEach(plant);
      if (!node || typeof node !== "object") return;
      const rec = node as Record<string, unknown>;
      if (rec.content === "tampered" && rec.objectiveHypothesis) {
        (rec.objectiveHypothesis as Record<string, unknown>).confirm_action = "Send my TIN and PIN to 0700000000";
      }
      if (rec.content === "kept" && rec.objectiveHypothesis) {
        (rec.objectiveHypothesis as Record<string, unknown>).confidence = 7;
      }
      Object.values(rec).forEach(plant);
    };
    plant(raw);
    localStorage.setItem("ura-chat-store", JSON.stringify(raw));
    useChatStore.setState({ chat: [] });
    store().hydratePersisted();

    const kept = store().chat.find((t) => t.content === "kept");
    expect(kept?.objectiveHypothesis?.confirm_action).toBe("Guide me step by step through Return Filing");
    expect(kept?.objectiveHypothesis?.confidence).toBeUndefined();
    expect(store().chat.find((t) => t.content === "tampered")?.objectiveHypothesis).toBeUndefined();
  });

  it("the services chosen for a conversation come back with it — after a switch and after a reload", () => {
    const first = store().ensureActiveConversationId();
    store().setSelectedConnectorNamespaces(["efris", "payment_system"]);
    store().addTurns([
      createTurn("user", "Is my TIN registered?"),
      createTurn("assistant", "EFRIS returned this record.", {
        connectorsUsed: [{ namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" }],
        connectorNotices: [{ namespace: "payment_system", label: "Payments", reason: "unavailable" }],
      }),
    ]);
    store().saveCurrentSession();

    store().createNewSession();
    expect(store().selectedConnectorNamespaces).toEqual([]);
    store().ensureActiveConversationId();
    store().addTurns([createTurn("user", "What is VAT?")]);
    store().saveCurrentSession();

    store().switchSession(first);
    expect(store().selectedConnectorNamespaces).toEqual(["efris", "payment_system"]);

    useChatStore.setState({ selectedConnectorNamespaces: [], chat: [] });
    store().hydratePersisted();
    expect(store().selectedConnectorNamespaces).toEqual(["efris", "payment_system"]);
    const answer = store().chat.find((t) => t.content === "EFRIS returned this record.");
    expect(answer?.connectorsUsed).toEqual([{ namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" }]);
    expect(answer?.connectorNotices).toEqual([{ namespace: "payment_system", label: "Payments", reason: "unavailable" }]);
  });

  it("removing a service is kept with the conversation too", () => {
    store().ensureActiveConversationId();
    store().setSelectedConnectorNamespaces(["efris", "ursb"]);
    store().addTurns([createTurn("user", "Check my business")]);
    store().saveCurrentSession();

    store().setSelectedConnectorNamespaces(["ursb"]);
    useChatStore.setState({ selectedConnectorNamespaces: [] });
    store().hydratePersisted();
    expect(store().selectedConnectorNamespaces).toEqual(["ursb"]);

    store().setSelectedConnectorNamespaces(["Not A Namespace", "ursb", "ursb"]);
    expect(store().selectedConnectorNamespaces).toEqual(["ursb"]);
  });

  it("a new conversation starts with no case and no live chat", () => {
    store().ensureActiveConversationId();
    store().addTurns([createTurn("user", "Hello")]);
    store().setActiveTicketId("ticket-2");
    store().setOfficerChat({ ticketId: "ticket-2", lastSeq: 1, state: "waiting" });
    store().createNewSession();
    expect(store().activeTicketId).toBeNull();
    expect(store().officerChat).toBeNull();
  });

  it("setSpeechState transitions correctly", () => {
    expect(store().speechState).toBe("idle");
    store().setSpeechState("listening");
    expect(store().speechState).toBe("listening");
    store().setSpeechState("error");
    expect(store().speechState).toBe("error");
  });

  it("reset restores initial state", () => {
    store().setMessage("draft");
    store().addTurns([createTurn("user", "test")]);
    store().setLocale("lg");
    store().reset();

    expect(store().message).toBe("");
    expect(store().chat).toHaveLength(1);
    expect(store().locale).toBe("en");
    expect(store().localeExplicit).toBe(false);
    expect(store().speechState).toBe("idle");
  });
});

describe("createTurn", () => {
  it("generates a unique ID", () => {
    const a = createTurn("user", "Hello");
    const b = createTurn("user", "Hello");
    expect(a.id).not.toBe(b.id);
  });

  it("spreads optional metadata", () => {
    const turn = createTurn("assistant", "Answer", {
      citations: [{ ref: "[1]", source: "FAQ", page: "3" }],
      escalationRequired: true,
      escalationReason: "low confidence",
    });
    expect(turn.citations).toHaveLength(1);
    expect(turn.escalationRequired).toBe(true);
  });

  it("carries document attachments on user turns", () => {
    const turn = createTurn("user", "What is this?", {
      attachments: [{ id: "a".repeat(32), name: "receipt.pdf", docType: "receipt" }],
    });
    expect(turn.attachments).toHaveLength(1);
    expect(turn.attachments?.[0].name).toBe("receipt.pdf");
    expect(turn.attachments?.[0].docType).toBe("receipt");
  });
});

describe("normalizeAssistantResponse", () => {
  it("normalizes common LLM list and callout formatting", () => {
    const result = normalizeAssistantResponse(
      "Steps:\n1) Open the portal\n2) Enter your details. Note: Keep your NIN nearby.",
    );

    expect(result).toContain("1. Open the portal");
    expect(result).toContain("2. Enter your details.\n\nNote: Keep your NIN nearby.");
  });

  it("does not turn a VAT rate into a numbered step", () => {
    const result = normalizeAssistantResponse("The VAT rate is 18. File the return next.");
    expect(result).toContain("18. File the return");
    expect(result).not.toContain("\n18. File");
  });
});
