/**
 * useChatStore — Zustand store unit tests.
 *
 * Covers: state shape, addTurns cap, updateLastTurn, locale switch, reset.
 * Aligned with RTM REQ-08 (loading indicator via state) and ISO 25010 §7.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { useChatStore, createTurn, type ChatTurn } from "../../store/useChatStore";

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
});
