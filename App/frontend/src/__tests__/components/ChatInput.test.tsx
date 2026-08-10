/**
 * ChatInput — component unit tests.
 *
 * Covers: rendering, user input, Enter to send, disabled states, a11y labels.
 * Aligned with WCAG 2.1 AA (keyboard navigation, aria-labels).
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChatInput from "../../components/ChatInput";
import type { PendingAttachment } from "../../lib/attachments";

const defaults = {
  message: "",
  isLoading: false,
  isRecording: false,
  isTransitioning: false,
  speechUnavailable: false,
  speechState: "idle",
  voiceMode: false,
  onMessageChange: vi.fn(),
  onSend: vi.fn(),
  onMicClick: vi.fn(),
};

describe("ChatInput", () => {
  it("renders input, mic button, and send button", () => {
    render(<ChatInput {...defaults} />);
    expect(screen.getByLabelText("Type your message")).toBeInTheDocument();
    expect(screen.getByLabelText("Send message")).toBeInTheDocument();
    expect(screen.getByLabelText("Start speaking")).toBeInTheDocument();
  });

  it("fires onMessageChange on typing", async () => {
    const onChange = vi.fn();
    render(<ChatInput {...defaults} onMessageChange={onChange} />);
    const input = screen.getByLabelText("Type your message");
    await userEvent.type(input, "VAT");
    expect(onChange).toHaveBeenCalled();
  });

  it("fires onSend on Enter key", async () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaults} message="Hello" onSend={onSend} />);
    const input = screen.getByLabelText("Type your message");
    await userEvent.type(input, "{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire onSend on Shift+Enter", async () => {
    const onSend = vi.fn();
    render(<ChatInput {...defaults} message="Hello" onSend={onSend} />);
    const input = screen.getByLabelText("Type your message");
    await userEvent.type(input, "{Shift>}{Enter}{/Shift}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disables send button when message is empty", () => {
    render(<ChatInput {...defaults} message="" />);
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("disables send button when loading", () => {
    render(<ChatInput {...defaults} message="test" isLoading />);
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("disables mic button when speech unavailable", () => {
    render(<ChatInput {...defaults} speechUnavailable />);
    expect(screen.getByLabelText("Start speaking")).toBeDisabled();
  });

  it("shows voice mode placeholder when voiceMode is true", () => {
    render(<ChatInput {...defaults} voiceMode />);
    expect(
      screen.getByPlaceholderText(/Voice mode on/),
    ).toBeInTheDocument();
  });

  it("shows recording controls when isRecording", () => {
    render(<ChatInput {...defaults} isRecording />);
    expect(screen.getByLabelText("Cancel recording")).toBeInTheDocument();
    expect(screen.getByLabelText("Send recording")).toBeInTheDocument();
  });
});

describe("ChatInput attachments", () => {
  const readyAttachment: PendingAttachment = {
    clientId: "c1",
    name: "receipt.pdf",
    sizeBytes: 2048,
    status: "ready",
    documentId: "d".repeat(32),
    docType: "receipt",
  };

  it("renders the attach button only when onAttachFiles is provided", () => {
    const { unmount } = render(<ChatInput {...defaults} onAttachFiles={vi.fn()} />);
    expect(screen.getByLabelText(/Attach a document/)).toBeInTheDocument();
    unmount();
    render(<ChatInput {...defaults} />);
    expect(screen.queryByLabelText(/Attach a document/)).not.toBeInTheDocument();
  });

  it("renders ready chips with doc type and fires remove", async () => {
    const onRemove = vi.fn();
    render(
      <ChatInput
        {...defaults}
        onAttachFiles={vi.fn()}
        onRemoveAttachment={onRemove}
        attachments={[readyAttachment]}
      />,
    );
    expect(screen.getByText("receipt.pdf")).toBeInTheDocument();
    expect(screen.getByText(/Receipt/)).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText("Remove receipt.pdf"));
    expect(onRemove).toHaveBeenCalledWith("c1");
  });

  it("marks failed uploads and disables send while analysing", () => {
    render(
      <ChatInput
        {...defaults}
        message="What is this?"
        onAttachFiles={vi.fn()}
        attachments={[
          { ...readyAttachment, clientId: "c2", status: "error", error: "Over the 10 MB limit" },
          { ...readyAttachment, clientId: "c3", status: "uploading" },
        ]}
      />,
    );
    expect(screen.getByText("Over the 10 MB limit")).toBeInTheDocument();
    expect(screen.getByLabelText("Analysing attachment...")).toBeDisabled();
  });
});
