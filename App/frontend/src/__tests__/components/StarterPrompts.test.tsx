/**
 * StarterPrompts — component unit tests.
 *
 * Covers: prompt rendering, click handler, a11y structure.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StarterPrompts from "../../components/StarterPrompts";

describe("StarterPrompts", () => {
  it("renders all four starter prompts", () => {
    render(<StarterPrompts onSelect={vi.fn()} />);
    expect(screen.getByText(/What services does URA provide/)).toBeInTheDocument();
    expect(screen.getByText(/How do I register for a TIN/)).toBeInTheDocument();
    expect(screen.getByText(/What is the current VAT rate/)).toBeInTheDocument();
    expect(screen.getByText(/How do I file my annual tax returns/)).toBeInTheDocument();
  });

  it("fires onSelect with the prompt text on click", async () => {
    const onSelect = vi.fn();
    render(<StarterPrompts onSelect={onSelect} />);

    await userEvent.click(screen.getByText(/How do I register for a TIN/));
    expect(onSelect).toHaveBeenCalledWith("How do I register for a TIN?");
  });

  it("renders voice capabilities info panel", () => {
    render(<StarterPrompts onSelect={vi.fn()} />);
    expect(screen.getByText("Voice capabilities")).toBeInTheDocument();
    expect(screen.getByText(/Speech recognition in English and Luganda/)).toBeInTheDocument();
  });

  it("renders grounding explanation panel", () => {
    render(<StarterPrompts onSelect={vi.fn()} />);
    expect(screen.getByText("How grounding works")).toBeInTheDocument();
    expect(screen.getByText(/Faithfulness score/)).toBeInTheDocument();
  });

  it("uses aside element for semantic structure", () => {
    const { container } = render(<StarterPrompts onSelect={vi.fn()} />);
    expect(container.querySelector("aside")).toBeInTheDocument();
  });
});
