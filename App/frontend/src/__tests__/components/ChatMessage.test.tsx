/**
 * ChatMessage — component unit tests.
 *
 * Covers: user vs assistant rendering, citations, escalation, feedback,
 * grounding badge, WCAG roles/labels.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ChatMessage from "../../components/ChatMessage";
import { useChatStore, type ChatTurn } from "../../store/useChatStore";
import { formatNextShift } from "../../lib/turnMeta";

const OFF_DUTY = { onDuty: false, hours: "07:00–23:00 EAT", nextOnDuty: "2026-10-17T07:00:00+03:00" };

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
      conversationId="conv-1"
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

const attachmentTurn: ChatTurn = {
  ...userTurn,
  id: "u2",
  content: "What does this receipt say?",
  attachments: [{ id: "a".repeat(32), name: "receipt.pdf", docType: "receipt" }],
};

describe("ChatMessage", () => {
  it('updates changed workflow options even when its ID and step number stay the same', () => {
    const workflow = { id: 'w', name: 'Payment', status: 'active' as const, step_index: 1, options: ['Old option'] };
    const props = { userQuery: '', locale: 'en', playingTurnId: null, ttsLoading: null,
      isTransitioning: false, onListen: vi.fn(), conversationId: 'conv-1', onActionClick: vi.fn() };
    const { rerender } = render(<ChatMessage {...props} turn={{ ...assistantTurn, workflow }} />);
    expect(screen.getByRole('button', { name: 'Old option' })).toBeInTheDocument();
    rerender(<ChatMessage {...props} turn={{ ...assistantTurn, workflow: { ...workflow, options: ['Corrected option'] } }} />);
    expect(screen.getByText('Corrected option')).toBeInTheDocument();
    expect(screen.queryByText('Old option')).not.toBeInTheDocument();
  });

  it('renders a portal confirmation arriving after the answer text', () => {
    const props = { userQuery: '', locale: 'en', playingTurnId: null, ttsLoading: null,
      isTransitioning: false, onListen: vi.fn(), conversationId: 'conv-1' };
    const { rerender } = render(<ChatMessage {...props} turn={assistantTurn} />);
    rerender(<ChatMessage {...props} turn={{ ...assistantTurn, portalCopilot: {
      card_type: 'confirmation_required', title: 'Review corrected payment', task_id: 'payment_generate_prn',
      display_fields: [{ name: 'amount', label: 'Amount', value: '1200' }],
    } }} />);
    expect(screen.getByText('Review corrected payment')).toBeInTheDocument();
    expect(screen.getByText('1200')).toBeInTheDocument();
  });

  it.each([{ actionsDisabled: true }, { phaseLabel: 'Thinking' }])('hides stale/busy workflow controls: %s', (overrides) => {
    renderMsg({ ...assistantTurn, workflow: { id: 'w', name: 'Payment', status: 'active', options: ['Submit stale option'] }, nextActions: ['Advance'] },
      { onActionClick: vi.fn(), ...overrides });
    expect(screen.queryByText('Submit stale option')).not.toBeInTheDocument();
    expect(screen.queryByText('Advance')).not.toBeInTheDocument();
  });

  it('marks interrupted answers and withholds reassuring badges and actions', () => {
    renderMsg({ ...assistantTurn, responseIncomplete: true, nextActions: ['Advance'] }, { onActionClick: vi.fn() });
    expect(screen.getByRole('status')).toHaveTextContent('interrupted');
    expect(screen.queryByText('Well grounded')).not.toBeInTheDocument();
    expect(screen.queryByText('Advance')).not.toBeInTheDocument();
  });
  it("renders user message with user icon", () => {
    renderMsg(userTurn);
    expect(screen.getByText("What is VAT?")).toBeInTheDocument();
    // Role is announced to screen readers (visually hidden), not shown as a label.
    expect(screen.getByText("You said")).toBeInTheDocument();
  });

  it("renders assistant message with bot icon", () => {
    renderMsg(assistantTurn);
    expect(screen.getByText("VAT is 18% in Uganda.")).toBeInTheDocument();
    expect(screen.getByText("Assistant replied")).toBeInTheDocument();
  });

  it("renders attachment chips with a report download on user turns", () => {
    renderMsg(attachmentTurn);
    expect(screen.getByText("receipt.pdf")).toBeInTheDocument();
    expect(screen.getByText("Receipt")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Download analysis report for receipt\.pdf/ }),
    ).toBeInTheDocument();
  });

  it("copies a user message to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderMsg(userTurn);
    fireEvent.click(screen.getByRole("button", { name: "Copy message" }));
    expect(writeText).toHaveBeenCalledWith("What is VAT?");
    expect(await screen.findByRole("button", { name: "Message copied" })).toBeInTheDocument();
  });

  it("copies the assistant reply to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderMsg(assistantTurn);
    fireEvent.click(screen.getByRole("button", { name: "Copy reply" }));
    expect(writeText).toHaveBeenCalledWith("VAT is 18% in Uganda.");
    expect(await screen.findByRole("button", { name: "Reply copied" })).toBeInTheDocument();
  });

  it("folds the answer's sources into a pill that lists them on demand", () => {
    renderMsg(assistantTurn);
    const pill = screen.getByRole("button", { name: "Sources (1)" });
    expect(screen.queryByText("URA FAQ")).not.toBeInTheDocument();
    fireEvent.click(pill);
    expect(screen.getByText("URA FAQ")).toBeInTheDocument();
  });

  it("puts official forms in the same pill instead of a card grid", () => {
    renderMsg({
      ...assistantTurn,
      resources: [
        {
          id: "tcc_online",
          title: "Tax clearance certificate",
          type: "online_form",
          url: "https://ura.go.ug/en/tax-clearance/",
        },
      ],
    });
    expect(screen.queryByText("Official URA Forms, Templates & Sources")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sources (2)" }));
    expect(screen.getByText("Tax clearance certificate").closest("a")).toHaveAttribute(
      "href",
      "https://ura.go.ug/en/tax-clearance/",
    );
  });

  it("shows a guided step's forms in the pill", () => {
    renderMsg({
      ...assistantTurn,
      citations: [],
      workflow: {
        id: "tcc_apply",
        name: "Tax clearance",
        status: "active",
        resources: [
          { id: "tcc_online", title: "Tax clearance certificate", type: "online_form", url: "https://ura.go.ug/en/tax-clearance/" },
        ],
      },
    });
    expect(screen.getByRole("button", { name: "Sources (1)" })).toBeInTheDocument();
  });

  it("rerenders when streaming metadata adds citations without changing content", () => {
    const withoutMetadata: ChatTurn = {
      ...assistantTurn,
      citations: [],
      faithfulnessScore: null,
    };
    const { rerender } = renderMsg(withoutMetadata);

    expect(screen.queryByRole("button", { name: "Sources (1)" })).not.toBeInTheDocument();

    rerender(
      <ChatMessage
        turn={assistantTurn}
        userQuery="What is VAT?"
        locale="en"
        playingTurnId={null}
        ttsLoading={null}
        isTransitioning={false}
        onListen={vi.fn()}
        conversationId="conv-1"
      />,
    );

    expect(screen.getByRole("button", { name: "Sources (1)" })).toBeInTheDocument();
    expect(screen.getByText(/Well grounded/)).toBeInTheDocument();
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

  it("names its Listen button in the reader's language (G201)", () => {
    try {
      useChatStore.setState({ locale: "sw" });
      renderMsg(assistantTurn, { locale: "sw" });
      const listen = screen.getByRole("button", { name: /^Sikiliza kwa / });
      expect(listen).toHaveTextContent("Sikiliza");
    } finally {
      useChatStore.setState({ locale: "en" });
    }
  });

  it("outside officers' hours, says when the handoff will be read (G196)", () => {
    renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: OFF_DUTY });
    const notice = screen.getByTestId("escalation-off-duty");
    expect(notice).toHaveTextContent("Officers work 07:00–23:00 EAT. Your question is queued");
    // The next shift by name: it can be days away.
    expect(notice).toHaveTextContent(`will reply here from ${formatNextShift(OFF_DUTY.nextOnDuty, "en")}`);
  });

  it("without a next shift, says when they are next on duty", () => {
    renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: { ...OFF_DUTY, nextOnDuty: null } });
    expect(screen.getByTestId("escalation-off-duty")).toHaveTextContent("when they are next on duty");
  });

  it("does not offer the closed desk's phone lines", () => {
    renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: OFF_DUTY });
    const alert = screen.getByRole("alert");
    expect(alert).not.toHaveTextContent("0800 117 000");
    expect(alert).toHaveTextContent("WhatsApp 0772 140 000");
  });

  it("shows the phone lines while officers are on duty", () => {
    renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: { ...OFF_DUTY, onDuty: true } });
    expect(screen.getByRole("alert")).toHaveTextContent("0800 117 000");
  });

  it("updates when only the officers' hours change", () => {
    const props = {
      userQuery: "What is VAT?",
      locale: "en",
      playingTurnId: null,
      ttsLoading: null,
      isTransitioning: false,
      onListen: vi.fn(),
      conversationId: "conv-1",
    };
    const onDuty = { ...assistantTurn, escalationRequired: true, officerHours: { ...OFF_DUTY, onDuty: true } };
    const { rerender } = render(<ChatMessage turn={onDuty} {...props} />);
    expect(screen.queryByTestId("escalation-off-duty")).toBeNull();
    rerender(<ChatMessage turn={{ ...onDuty, officerHours: OFF_DUTY }} {...props} />);
    expect(screen.getByTestId("escalation-off-duty")).toBeInTheDocument();
  });

  it("during officers' hours, adds nothing", () => {
    renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: { ...OFF_DUTY, onDuty: true } });
    expect(screen.queryByTestId("escalation-off-duty")).toBeNull();
  });

  it("speaks the reader's language, Runyankole and Acholi included", () => {
    try {
      useChatStore.setState({ locale: "sw" });
      const { unmount } = renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: OFF_DUTY });
      expect(screen.getByRole("alert")).toHaveTextContent("Inapendekezwa afisa wa URA aangalie hili");
      expect(screen.getByTestId("escalation-off-duty")).toHaveTextContent("Maafisa wanafanya kazi 07:00–23:00 EAT.");
      unmount();
      useChatStore.setState({ locale: "ach" });
      renderMsg({ ...assistantTurn, escalationRequired: true, officerHours: OFF_DUTY });
      expect(screen.getByTestId("escalation-off-duty")).toHaveTextContent("Lutic gitiyo 07:00–23:00 EAT.");
    } finally {
      useChatStore.setState({ locale: "en" });
    }
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

  it("rates an AI answer, and names which answer of which conversation it is", async () => {
    const fetchMock = vi.fn(
      async (_url: RequestInfo | URL, _init?: RequestInit) =>
        ({ ok: true, json: async () => ({ id: "fb-1" }) }) as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    renderMsg({ ...assistantTurn, serverTurnId: "turn-abc123" }, { answerNumber: 2 });
    fireEvent.click(screen.getByRole("button", { name: "Unhelpful response" }));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ rating: "down", conversation_id: "conv-1", turn_id: "turn-abc123", turn_index: 2 });
    vi.unstubAllGlobals();
  });

  it("offers no thumbs on an officer's reply or on the greeting: neither is an AI answer", () => {
    const { unmount } = renderMsg({ ...assistantTurn, id: "o1", retrievalMode: "officer_reply" });
    expect(screen.queryByRole("button", { name: "Helpful response" })).not.toBeInTheDocument();
    unmount();
    renderMsg({ ...assistantTurn, id: "greeting-0", content: "Hello" });
    expect(screen.queryByRole("button", { name: "Helpful response" })).not.toBeInTheDocument();
  });

  it("says an officer's message is from the officer, by name, not from the assistant", () => {
    renderMsg({ ...assistantTurn, id: "o2", retrievalMode: "officer_reply", officerName: "Officer Nakato", content: "Which period?" });
    expect(screen.getByText("URA officer · Officer Nakato")).toBeInTheDocument();
    expect(screen.getByText("Officer Nakato replied")).toHaveClass("sr-only");
    expect(screen.queryByText("Assistant replied")).not.toBeInTheDocument();
  });

  it("shows Listen button for assistant messages", () => {
    renderMsg(assistantTurn);
    expect(screen.getByLabelText(/Listen in English/)).toBeInTheDocument();
  });

  it("shows Stop button when playing", () => {
    renderMsg(assistantTurn, { playingTurnId: "a1" });
    expect(screen.getByLabelText(/Stop listening/)).toBeInTheDocument();
  });

  it("lets a read-aloud that is still loading be stopped", () => {
    renderMsg(assistantTurn, { ttsLoading: "a1" });
    const stop = screen.getByLabelText(/Stop listening/);
    expect(stop).toBeEnabled();
  });

  it("has correct article semantic element", () => {
    const { container } = renderMsg(userTurn);
    expect(container.querySelector("article")).toBeInTheDocument();
  });

  it("renders suggested next action chips and handles clicks", () => {
    const onActionClick = vi.fn();
    const actionTurn: ChatTurn = {
      ...assistantTurn,
      nextActions: ["Calculate VAT for my figures", "Explore VAT Registration"],
    };
    renderMsg(actionTurn, { onActionClick });

    const btn1 = screen.getByRole("button", { name: /Calculate VAT for my figures/ });
    const btn2 = screen.getByRole("button", { name: /Explore VAT Registration/ });
    expect(btn1).toBeInTheDocument();
    expect(btn2).toBeInTheDocument();

    fireEvent.click(btn1);
    expect(onActionClick).toHaveBeenCalledWith("Calculate VAT for my figures");
  });

  it("renders statutory calculator and taxpayer education provenance badges", () => {
    const { rerender } = renderMsg({
      ...assistantTurn,
      faithfulnessScore: null,
      retrievalMode: "calculator",
    });
    expect(screen.getByText(/Official Statutory Calculator/)).toBeInTheDocument();

    rerender(
      <ChatMessage
        turn={{
          ...assistantTurn,
          faithfulnessScore: null,
          retrievalMode: "education",
        }}
        userQuery="What is VAT?"
        locale="en"
        playingTurnId={null}
        ttsLoading={null}
        isTransitioning={false}
        onListen={vi.fn()}
        conversationId="conv-1"
      />,
    );
    expect(screen.getByText(/Verified Taxpayer Education/)).toBeInTheDocument();
  });

  it("renders statutory deadline calendar sync banner when deadline is detected", () => {
    renderMsg({
      ...assistantTurn,
      content: "PAYE returns and payments are due by the 15th day of each month under the Tax Procedures Code Act.",
    });
    expect(screen.getByLabelText(/Statutory Tax Deadline Sync/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download calendar reminder/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open in Google Calendar/i })).toBeInTheDocument();
  });
});

describe("ChatMessage — services an answer used", () => {
  it("names each service, says when it is demo data, and how its lookup ended", () => {
    renderMsg({
      ...assistantTurn,
      citations: [],
      connectorsUsed: [
        { namespace: "efris", label: "EFRIS", mode: "simulation", outcome: "found" },
        { namespace: "ursb", label: "URSB", mode: "live", outcome: "no_record" },
      ],
    });

    const list = screen.getByRole("list", { name: "Looked up in" });
    expect(list).toHaveTextContent("EFRIS");
    expect(list).toHaveTextContent("Demo data");
    expect(list).toHaveTextContent("Record found");
    expect(list).toHaveTextContent("URSB");
    expect(list).toHaveTextContent("No matching record");
  });

  it("says which chosen services the answer went without, and why", () => {
    renderMsg({
      ...assistantTurn,
      connectorNotices: [
        { namespace: "payment_system", label: "Payments", reason: "unavailable" },
        { namespace: "ursb", label: "URSB", reason: "not_offered" },
      ],
    });

    expect(
      screen.getByText("Payments is not answering right now, so this answer does not include a lookup there."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("URSB is no longer available to this account, so it was removed from this chat."),
    ).toBeInTheDocument();
  });

  it("shows nothing about services on an answer that used none", () => {
    renderMsg(assistantTurn);
    expect(screen.queryByRole("list", { name: "Looked up in" })).not.toBeInTheDocument();
  });
});

