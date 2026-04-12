/**
 * ChatMessage — component unit tests.
 *
 * Covers: user vs assistant rendering, citations, escalation, feedback,
 * grounding badge, WCAG roles/labels.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ChatMessage from "../../components/ChatMessage";
import type { ChatTurn } from "../../store/useChatStore";

function renderMsg(turn: ChatTurn, overrides = {}) {
  return render(
    <ChatMessage
      turn={turn}
      userQuery="What is VAT?"
      locale="en"
      playingTurnId={null}
      ttsLoading={null}
      isTransitioning={false}
      onListen={vi.fn()}
      {...overrides}
    />,
  );
}

const userTurn: ChatTurn = {
  id: "u1",
  role: "user",
  content: "What is VAT?",
  timestamp: Date.now(),
};

const assistantTurn: ChatTurn = {
  id: "a1",
  role: "assistant",
  content: "VAT is 18% in Uganda.",
  timestamp: Date.now(),
  citations: [{ ref: "[1]", source: "URA FAQ", page: "5", section: "VAT" }],
  faithfulnessScore: 0.85,
  retrievalMode: "hybrid",
};

describe("ChatMessage", () => {
  it("renders user message with user icon", () => {
    renderMsg(userTurn);
    expect(screen.getByText("What is VAT?")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
  });

  it("renders assistant message with bot icon", () => {
    renderMsg(assistantTurn);
    expect(screen.getByText("VAT is 18% in Uganda.")).toBeInTheDocument();
    expect(screen.getByText("assistant")).toBeInTheDocument();
  });

  it("shows citations with source details", () => {
    renderMsg(assistantTurn);
    expect(screen.getByText(/Sources \(1\)/)).toBeInTheDocument();
    expect(screen.getByText("URA FAQ")).toBeInTheDocument();
  });

  it("shows 'Well grounded' badge for high faithfulness", () => {
    renderMsg(assistantTurn);
    expect(screen.getByText(/Well grounded/)).toBeInTheDocument();
  });

  it("shows 'Verify with URA' badge for low faithfulness", () => {
    renderMsg({
      ...assistantTurn,
      faithfulnessScore: 0.3,
    });
    expect(screen.getByText(/Verify with URA/)).toBeInTheDocument();
  });

  it("shows escalation banner when escalation required", () => {
    renderMsg({
      ...assistantTurn,
      escalationRequired: true,
      escalationReason: "low confidence",
    });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Human review recommended");
    expect(alert).toHaveTextContent("low confidence");
  });

  it("does NOT show escalation banner on greeting", () => {
    renderMsg({
      id: "greeting-0",
      role: "assistant",
      content: "Hi!",
      timestamp: Date.now(),
      escalationRequired: true,
    });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows Listen button for assistant messages", () => {
    renderMsg(assistantTurn);
    expect(screen.getByLabelText(/Listen in English/)).toBeInTheDocument();
  });

  it("shows Stop button when playing", () => {
    renderMsg(assistantTurn, { playingTurnId: "a1" });
    expect(screen.getByLabelText(/Stop listening/)).toBeInTheDocument();
  });

  it("has correct article semantic element", () => {
    const { container } = renderMsg(userTurn);
    expect(container.querySelector("article")).toBeInTheDocument();
  });
});
