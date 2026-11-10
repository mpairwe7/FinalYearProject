import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import PortalCopilotCard from "../../components/PortalCopilotCard";
import { CONFIRM_ARM_DELAY_MS, isBrowserRaisedClick } from "../../lib/confirmGuard";

/**
 * The Allow button on a confirmation card (ADR-0038, G549). This file leaves
 * the guard as it ships; PortalCopilotCard.test.tsx switches it off, because
 * jsdom cannot raise a click the browser would call trusted.
 */
const confirmation = {
  card_type: "confirmation_required",
  task_id: "payment_generate_prn",
  title: "Review payment",
  request_state: { signed: true },
  idempotency_key: "ura_task_first",
  display_fields: [{ name: "amount", label: "Amount", value: "1200" }],
  fields: [{ name: "amount", label: "Amount", input_type: "number", required: true, value: "1200" }],
};

describe("isBrowserRaisedClick", () => {
  it("counts only a click the browser raised from real input", () => {
    expect(isBrowserRaisedClick({ isTrusted: true })).toBe(true);
    expect(isBrowserRaisedClick({ isTrusted: false })).toBe(false);
    expect(isBrowserRaisedClick({ isTrusted: true, nativeEvent: { isTrusted: false } })).toBe(false);
    expect(isBrowserRaisedClick({})).toBe(false);
  });
});

describe("the Allow button on a confirmation card", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("crypto", webcrypto);
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const arm = () => act(() => void vi.advanceTimersByTime(CONFIRM_ARM_DELAY_MS));

  it("is not available the instant the card appears", () => {
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    expect(screen.getByTestId("portal-task-allow-btn")).toBeDisabled();
    arm();
    expect(screen.getByTestId("portal-task-allow-btn")).toBeEnabled();
  });

  it("waits again when the details to confirm change", () => {
    const { rerender } = render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    arm();
    rerender(
      <PortalCopilotCard conversationId="c" copilot={{ ...confirmation, idempotency_key: "ura_task_second" }} />,
    );
    expect(screen.getByTestId("portal-task-allow-btn")).toBeDisabled();
  });

  it("leaves Disallow available at once", () => {
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    expect(screen.getByTestId("portal-task-disallow-btn")).toBeEnabled();
  });

  it("sends nothing for a click a script made", async () => {
    render(<PortalCopilotCard conversationId="c" copilot={confirmation} />);
    arm();
    // jsdom dispatches this itself, as a page script or an extension would: isTrusted is false.
    fireEvent.click(screen.getByTestId("portal-task-allow-btn"));
    await act(async () => void (await vi.advanceTimersByTimeAsync(50)));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
