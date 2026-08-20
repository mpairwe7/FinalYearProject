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

  it("disables send button when loading without onStop", () => {
    render(<ChatInput {...defaults} message="test" isLoading />);
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("shows stop in the primary slot while loading when onStop is set", async () => {
    const onStop = vi.fn();
    render(<ChatInput {...defaults} message="" isLoading onStop={onStop} />);
    expect(screen.queryByLabelText("Send message")).not.toBeInTheDocument();
    const stop = screen.getByLabelText("Stop generating");
    expect(stop).toBeEnabled();
    await userEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
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
    // `defaults` has voiceMode: false, i.e. dictation — where the checkmark
    // inserts text rather than sending. This asserted "Send recording" for that
    // case, which is the copy bug the "recording panel" tests below now cover
    // on both flows.
    render(<ChatInput {...defaults} isRecording />);
    expect(screen.getByLabelText("Cancel recording")).toBeInTheDocument();
    expect(screen.getByLabelText("Stop and insert text")).toBeInTheDocument();
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
          { ...readyAttachment, clientId: "c2", status: "error", error: "Over the 40 MB limit" },
          { ...readyAttachment, clientId: "c3", status: "uploading" },
        ]}
      />,
    );
    expect(screen.getByText("Over the 40 MB limit")).toBeInTheDocument();
    expect(screen.getByLabelText("Analysing attachment...")).toBeDisabled();
  });

  /**
   * `processing` is the gap between "I stopped recording" and "the transcript
   * arrived", which only exists on the server-ASR dictation path. The mic has
   * to stop claiming it is listening and stop accepting taps: the audio is
   * already uploaded, so a second tap cancels nothing and only reads as broken.
   */
  describe("dictation states", () => {
    it("reads as listening and offers to stop while recording", () => {
      render(<ChatInput {...defaults} speechState="listening" />);
      const mic = screen.getByLabelText("Stop listening");
      expect(mic).toBeEnabled();
      expect(mic.className).toContain("btn-recording");
    });

    it("reads as transcribing and refuses taps while the transcript is in flight", () => {
      const onMicClick = vi.fn();
      render(<ChatInput {...defaults} speechState="processing" onMicClick={onMicClick} />);
      const mic = screen.getByLabelText("Transcribing");
      expect(mic).toBeDisabled();
      // Not the red recording pulse — the mic is no longer listening.
      expect(mic.className).not.toContain("btn-recording");
      expect(mic.className).toContain("is-processing");
      expect(mic).toHaveAttribute("data-tip", "Transcribing…");
    });

    it("returns to the plain dictate affordance when idle", () => {
      render(<ChatInput {...defaults} speechState="idle" />);
      const mic = screen.getByLabelText("Start speaking");
      expect(mic).toBeEnabled();
      expect(mic).toHaveAttribute("data-tip", "Dictate");
    });
  });

  /**
   * Recording replaces the whole composer with a waveform panel, and its
   * checkmark means two different things: in voice mode it sends the utterance
   * as a turn, in dictation it only drops the transcript into the box. The
   * panel used to say "Send recording" for both, promising the wrong outcome to
   * anyone dictating.
   */
  describe("recording panel", () => {
    it("offers to send the utterance in voice mode", () => {
      render(<ChatInput {...defaults} isRecording voiceMode onVoiceModeChange={vi.fn()} />);
      expect(screen.getByLabelText("Send recording")).toBeInTheDocument();
      expect(screen.getByText(/Tap checkmark to send/)).toBeInTheDocument();
    });

    it("offers to insert the text when dictating, and does not claim to send", () => {
      render(<ChatInput {...defaults} isRecording voiceMode={false} onVoiceModeChange={vi.fn()} />);
      expect(screen.getByLabelText("Stop and insert text")).toBeInTheDocument();
      expect(screen.queryByLabelText("Send recording")).not.toBeInTheDocument();
      expect(screen.getByText(/add what you said to the message/)).toBeInTheDocument();
    });

    it("always offers a way out of a recording", () => {
      render(<ChatInput {...defaults} isRecording onCancelRecording={vi.fn()} />);
      expect(screen.getByLabelText("Cancel recording")).toBeEnabled();
    });

    /**
     * The trap that broke three CI runs: while recording, the composer is
     * replaced wholesale, so the mic button — and its "Stop listening" label —
     * does not exist, even though speechState is 'listening' at the same time.
     * Three e2e tests waited on that label and timed out. Pinning it here means
     * the next person writing a recording test sees why it is not there.
     */
    it("shows a dictation result in place of the standing disclaimer", () => {
      const { rerender } = render(<ChatInput {...defaults} />);
      // Nothing to report yet — the usual footer.
      expect(screen.getByText(/can make mistakes/)).toBeInTheDocument();

      // Dictation heard nothing. Before this the composer just sat there empty
      // with no explanation, which reads as the button being broken.
      rerender(<ChatInput {...defaults} dictationNotice="Didn't catch that. Try again, or type your question." />);
      const notice = screen.getByText(/Didn't catch that/);
      expect(notice).toBeInTheDocument();
      expect(notice).toHaveAttribute("role", "status");
      expect(screen.queryByText(/can make mistakes/)).not.toBeInTheDocument();
    });

    it("has no mic button while recording, in either flow", () => {
      for (const voiceMode of [true, false]) {
        const { unmount } = render(
          <ChatInput {...defaults} isRecording speechState="listening" voiceMode={voiceMode} />,
        );
        expect(screen.queryByLabelText("Stop listening")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Start speaking")).not.toBeInTheDocument();
        unmount();
      }
    });
  });
});
