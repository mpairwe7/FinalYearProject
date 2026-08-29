/**
 * HumanHandoff — the taxpayer's own way into the officer queue.
 *
 * Reported as "escalation to human agents — how do we do that". The queue and
 * the officer console already existed; what did not was a way for the person
 * being failed to say so. Every route into that queue was a judgement the
 * system made on their behalf.
 *
 * What these assert is mostly about honesty: the control must not report a
 * handoff that did not happen, and it must show the reference, because a
 * request that leaves no receipt reads as not having happened at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HumanHandoff from "../../components/HumanHandoff";
import ChatMessage from "../../components/ChatMessage";
import type { ChatTurn } from "../../store/useChatStore";

const originalFetch = globalThis.fetch;

function mockEscalate(body: unknown, ok = true) {
  const fetchMock = vi.fn().mockResolvedValue({ ok, json: async () => body });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

beforeEach(() => {
  globalThis.fetch = originalFetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("HumanHandoff", () => {
  it("offers a person and posts the conversation it belongs to", async () => {
    const fetchMock = mockEscalate({
      ok: true,
      ticket_id: "abcdef1234567890",
      status: "open",
      reused_existing: false,
      message: "A URA officer has been asked to look at this.",
    });

    render(<HumanHandoff conversationId="conv-9" locale="en" reason="What is VAT?" />);
    await userEvent.click(screen.getByTestId("handoff-request"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/escalate");
    const sent = JSON.parse((init as RequestInit).body as string);
    expect(sent.conversation_id).toBe("conv-9");
    expect(sent.reason).toBe("What is VAT?");
    expect(sent.locale).toBe("en");
  });

  it("shows the reference so the taxpayer has a receipt to quote", async () => {
    mockEscalate({
      ok: true,
      ticket_id: "abcdef1234567890",
      status: "open",
      reused_existing: false,
      message: "A URA officer has been asked to look at this.",
    });

    render(<HumanHandoff conversationId="conv-9" locale="en" />);
    await userEvent.click(screen.getByTestId("handoff-request"));

    expect(await screen.findByText("abcdef12")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("asked to look at this");
    // The offer is gone: pressing again would open a second ticket for the
    // same conversation and put two officers on it.
    expect(screen.queryByTestId("handoff-request")).not.toBeInTheDocument();
  });

  it("never claims a person is coming when the queue refused", async () => {
    mockEscalate({
      ok: false,
      status: "queue_disabled",
      ticket_id: "",
      message: "This assistant cannot pass your question to an officer right now.",
    });

    render(<HumanHandoff conversationId="conv-9" locale="en" />);
    await userEvent.click(screen.getByTestId("handoff-request"));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("cannot pass your question to an officer");
    // Still offered, because the person still needs the way out.
    expect(screen.getByTestId("handoff-request")).toBeInTheDocument();
  });

  it("says so when the request never reached the backend", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline")) as unknown as typeof fetch;

    render(<HumanHandoff conversationId={null} locale="en" />);
    await userEvent.click(screen.getByTestId("handoff-request"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/offline/i);
  });
});

/**
 * Where it appears. An offer that is always on screen is chrome, and on a good
 * answer it reads as the assistant hedging — so it shows exactly where the
 * assistant has failed: it escalated, it abstained, or it scored its own
 * grounding low.
 */
describe("when a person is offered", () => {
  const base = {
    id: "a1",
    role: "assistant",
    content: "The standard VAT rate is 18%.",
    citations: [],
    faithfulnessScore: 0.9,
    retrievalMode: "hybrid",
    escalationRequired: false,
    escalationReason: "",
  } as unknown as ChatTurn;

  function renderTurn(turn: ChatTurn) {
    return render(
      <ChatMessage
        turn={turn}
        userQuery="What is VAT?"
        locale="en"
        playingTurnId={null}
        ttsLoading={null}
        isTransitioning={false}
        onListen={vi.fn()}
        conversationId="conv-1"
      />,
    );
  }

  it("is not offered on a well-grounded answer", () => {
    renderTurn(base);
    expect(screen.queryByTestId("handoff-request")).not.toBeInTheDocument();
  });

  it("is offered when the answer scored its own grounding low", () => {
    renderTurn({ ...base, faithfulnessScore: 0.4 });
    expect(screen.getByTestId("handoff-request")).toBeInTheDocument();
  });

  it("is offered when the assistant abstained", () => {
    renderTurn({ ...base, retrievalMode: "abstained", faithfulnessScore: null });
    expect(screen.getByTestId("handoff-request")).toBeInTheDocument();
  });

  it("is offered when the turn was escalated", () => {
    renderTurn({ ...base, escalationRequired: true });
    expect(screen.getByTestId("handoff-request")).toBeInTheDocument();
  });
});
