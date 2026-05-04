import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "../../components/Markdown";

describe("Markdown", () => {
  it("renders assistant sections and ordered lists as structured content", () => {
    render(
      <Markdown
        content={"# Registration steps\n1) Open the URA portal\n2) Enter your NIN\n3) Submit the form"}
      />,
    );

    expect(screen.getByRole("heading", { name: "Registration steps" })).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  });

  it("renders pipe tables for comparison-style answers", () => {
    render(
      <Markdown
        content={
          "| Item | Requirement |\n| --- | --- |\n| TIN | Required |\n| NIN | Required for individuals |"
        }
      />,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Item" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Required for individuals" })).toBeInTheDocument();
  });

  it("renders note-style content as a callout", () => {
    const { container } = render(<Markdown content={"Note: Keep your original documents nearby."} />);

    expect(container.querySelector(".md-callout-note")).toBeInTheDocument();
    expect(screen.getByText("Keep your original documents nearby.")).toBeInTheDocument();
  });
});
