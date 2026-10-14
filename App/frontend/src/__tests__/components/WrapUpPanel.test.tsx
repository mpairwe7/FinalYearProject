/**
 * The officer's wrap-up records how satisfied the caller said they were.
 *
 * The caller is on the phone and cannot tap a rating, so the officer asks and
 * records the answer — required, or "the caller didn't say". The officer's
 * own rating of the AI is a separate, optional thing, offered only when the
 * AI answered something, and never sent unless chosen.
 */
import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const wrapUpCall = vi.fn();
vi.mock("@/services/officerCallSession", () => ({ wrapUpCall: (...args: unknown[]) => wrapUpCall(...args) }));

import { WrapUpPanel } from "../../components/staff/calls/WrapUpPanel";

function renderPanel(props: Partial<React.ComponentProps<typeof WrapUpPanel>> = {}) {
  const onFinished = vi.fn();
  render(<WrapUpPanel callId="call-1" initialNote="Explained the VAT refund." onFinished={onFinished} {...props} />);
  return { onFinished };
}

const save = () => fireEvent.click(screen.getByRole("button", { name: /Save & become available/ }));

beforeEach(() => {
  wrapUpCall.mockReset();
  wrapUpCall.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("WrapUpPanel — caller satisfaction", () => {
  it("will not save until the caller's satisfaction is recorded", async () => {
    renderPanel();
    save();
    expect(await screen.findByRole("alert")).toHaveTextContent(/how satisfied the caller said they were/);
    expect(wrapUpCall).not.toHaveBeenCalled();
  });

  it("sends the level the caller gave", async () => {
    const { onFinished } = renderPanel();
    fireEvent.click(screen.getByRole("radio", { name: "2 of 5, Dissatisfied" }));
    save();
    await waitFor(() => expect(onFinished).toHaveBeenCalled());
    expect(wrapUpCall).toHaveBeenCalledWith({
      outcome: "resolved",
      note: "Explained the VAT refund.",
      ticket_action: undefined,
      caller_satisfaction: 2,
      caller_satisfaction_status: "answered",
    });
  });

  it("records that the caller didn't say, and clears a level chosen before", async () => {
    renderPanel();
    fireEvent.click(screen.getByRole("radio", { name: "4 of 5, Satisfied" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "The caller didn't say" }));
    expect(screen.getByRole("radio", { name: "4 of 5, Satisfied" })).toHaveAttribute("aria-checked", "false");
    save();
    await waitFor(() => expect(wrapUpCall).toHaveBeenCalled());
    expect(wrapUpCall.mock.calls[0][0]).toMatchObject({ caller_satisfaction_status: "not_said" });
    expect(wrapUpCall.mock.calls[0][0]).not.toHaveProperty("caller_satisfaction");
  });
});

describe("WrapUpPanel — the officer's rating of the AI", () => {
  it("is not offered when the AI answered nothing", () => {
    renderPanel({ aiAnswered: false });
    expect(screen.queryByText(/Rate the AI's handling/)).not.toBeInTheDocument();
  });

  it("is optional: nothing is sent unless a star is chosen", async () => {
    renderPanel({ aiAnswered: true });
    expect(screen.getByText(/Rate the AI's handling before the transfer \(optional\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "5 of 5, Very satisfied" }));
    save();
    await waitFor(() => expect(wrapUpCall).toHaveBeenCalled());
    expect(wrapUpCall.mock.calls[0][0]).not.toHaveProperty("rating");
  });

  it("sends the stars and the note when chosen", async () => {
    renderPanel({ aiAnswered: true });
    fireEvent.click(screen.getByRole("radio", { name: "5 of 5, Very satisfied" }));
    fireEvent.click(screen.getByRole("button", { name: "3 of 5 stars" }));
    expect(screen.getByRole("button", { name: "3 of 5 stars" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByRole("textbox", { name: "Notes on the AI's handling" }), {
      target: { value: "Missed the PRN question" },
    });
    save();
    await waitFor(() => expect(wrapUpCall).toHaveBeenCalled());
    expect(wrapUpCall.mock.calls[0][0]).toMatchObject({ rating: 3, rating_note: "Missed the PRN question" });
  });
});
