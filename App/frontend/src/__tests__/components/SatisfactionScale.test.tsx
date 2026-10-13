/**
 * The five-face satisfaction scale, and the rating dialog a taxpayer sees when
 * an officer ends their chat.
 *
 * The scale is a radio group: one tab stop, arrows move and choose, and every
 * face has a spoken name — a face alone is not an answer anyone can hear. The
 * dialog asks once; Skip and closing are both a skip, and a comment only goes
 * with a rating.
 */
import React, { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SatisfactionScale } from "../../components/rating/SatisfactionScale";
import { ServiceRatingDialog } from "../../components/ServiceRatingDialog";

function Scale({ initial = null as number | null }) {
  const [value, setValue] = useState<number | null>(initial);
  return <SatisfactionScale value={value} onChange={setValue} groupLabel="How satisfied are you?" />;
}

describe("SatisfactionScale", () => {
  it("is one radio group with a spoken name for every face", () => {
    render(<Scale />);
    const group = screen.getByRole("radiogroup", { name: "How satisfied are you?" });
    const faces = screen.getAllByRole("radio");
    expect(group).toBeInTheDocument();
    expect(faces.map((f) => f.getAttribute("aria-label"))).toEqual([
      "1 of 5, Very dissatisfied",
      "2 of 5, Dissatisfied",
      "3 of 5, Neutral",
      "4 of 5, Satisfied",
      "5 of 5, Very satisfied",
    ]);
    // One tab stop, on the middle face until something is chosen.
    expect(faces.filter((f) => f.tabIndex === 0)).toEqual([faces[2]]);
  });

  it("chooses with a click and says the choice in words", () => {
    render(<Scale />);
    fireEvent.click(screen.getByRole("radio", { name: "4 of 5, Satisfied" }));
    expect(screen.getByRole("radio", { name: "4 of 5, Satisfied" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Satisfied", { selector: ".sat-scale-choice" })).toBeInTheDocument();
  });

  it("moves and chooses with the arrow keys, Home and End", () => {
    render(<Scale initial={3} />);
    const neutral = screen.getByRole("radio", { name: "3 of 5, Neutral" });
    fireEvent.keyDown(neutral, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "4 of 5, Satisfied" })).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "4 of 5, Satisfied" }));
    fireEvent.keyDown(document.activeElement as Element, { key: "Home" });
    expect(screen.getByRole("radio", { name: "1 of 5, Very dissatisfied" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowLeft" });
    // Already at the lowest level: it stays there.
    expect(screen.getByRole("radio", { name: "1 of 5, Very dissatisfied" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(document.activeElement as Element, { key: "End" });
    expect(screen.getByRole("radio", { name: "5 of 5, Very satisfied" })).toHaveAttribute("aria-checked", "true");
  });
});

describe("ServiceRatingDialog", () => {
  it("names the officer and sends the rating with its comment", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ServiceRatingDialog officerName="Officer Nakato" onSubmit={onSubmit} onSkip={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "How was your chat with Officer Nakato?" })).toBeInTheDocument();
    const send = screen.getByRole("button", { name: "Send rating" });
    expect(send).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: /2 of 5/ }));
    fireEvent.change(screen.getByRole("textbox", { name: /Anything to add/ }), { target: { value: "  Slow reply.  " } });
    fireEvent.click(send);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(2, "Slow reply."));
  });

  it("says so when the rating could not be sent, and still lets the taxpayer skip", async () => {
    const onSkip = vi.fn();
    render(<ServiceRatingDialog onSubmit={vi.fn().mockRejectedValue(new Error("500"))} onSkip={onSkip} />);
    fireEvent.click(screen.getByRole("radio", { name: /5 of 5/ }));
    fireEvent.click(screen.getByRole("button", { name: "Send rating" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be sent");
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("treats closing the dialog as a skip", () => {
    const onSkip = vi.fn();
    render(<ServiceRatingDialog onSubmit={vi.fn()} onSkip={onSkip} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onSkip).toHaveBeenCalled();
  });
});
