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

  it("renders inline numbered procedures with a lead-in as structured ordered lists", () => {
    const raw =
      "The URA provides services, including: 1. Tax Administration: collects taxes. 2. Customs Services: clears goods. 3. Digital Solutions: EFRIS.";
    render(<Markdown content={raw} />);

    expect(screen.getByText("The URA provides services, including:")).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(list.tagName.toLowerCase()).toBe("ol");
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Tax Administration: collects taxes");
    expect(items[1]).toHaveTextContent("Customs Services: clears goods");
    expect(items[2]).toHaveTextContent("Digital Solutions: EFRIS");
  });

  it("normalizes Customary Services to Customs Services", () => {
    render(<Markdown content={"The URA provides Customary Services and tax administration."} />);
    expect(screen.getByText(/Customs Services/)).toBeInTheDocument();
    expect(screen.queryByText(/Customary Services/)).not.toBeInTheDocument();
  });

  it("renders loose ordered lists with blank lines into a single continuous ordered list", () => {
    const raw =
      "Services include:\n\n1. Tax Administration: collects taxes.\n\n2. Customs Services: clears goods.\n\n3. Digital Solutions: EFRIS.";
    const { container } = render(<Markdown content={raw} />);

    const olElements = container.querySelectorAll("ol");
    expect(olElements).toHaveLength(1);
    const items = within(olElements[0]).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Tax Administration: collects taxes");
    expect(items[1]).toHaveTextContent("Customs Services: clears goods");
    expect(items[2]).toHaveTextContent("Digital Solutions: EFRIS");
  });

  it("renders underscore italics from calculator outputs without touching snake_case identifiers", () => {
    const raw = "_Assumptions: gross monthly salary._ Field name is tax_head.";
    const { container } = render(<Markdown content={raw} />);
    const em = container.querySelector("em");
    expect(em).not.toBeNull();
    expect(em?.textContent).toBe("Assumptions: gross monthly salary.");
    expect(container.textContent).toContain("tax_head");
  });

  it("renders bolded callout labels and nested inline code inside bold text", () => {
    const raw = "**Important:** Submit form **`DT-2001`** before the 15th.";
    const { container } = render(<Markdown content={raw} />);
    expect(container.querySelector(".md-callout-important")).toBeInTheDocument();
    const code = container.querySelector("strong code.md-inline-code");
    expect(code).not.toBeNull();
    expect(code?.textContent).toBe("DT-2001");
  });

  it("does not split long paragraphs across statutory abbreviations like Sec. or Cap.", () => {
    const raw =
      "Every registered taxpayer in Uganda must maintain accurate accounting records for at least five years from the end of the tax period to which they relate, pursuant to Sec. 15 of the Tax Procedures Code Act, Cap. 343, and produce them upon inspection.";
    const { container } = render(<Markdown content={raw} />);
    const paras = Array.from(container.querySelectorAll("p")).map((p) => p.textContent ?? "");
    expect(paras.some((p) => p.endsWith("Sec."))).toBe(false);
    expect(paras.some((p) => p.endsWith("Cap."))).toBe(false);
    expect(container.textContent).toContain("Sec. 15 of the Tax Procedures Code Act, Cap. 343");
  });
});
