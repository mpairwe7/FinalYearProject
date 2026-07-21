import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Markdown from "../../components/Markdown";

describe("Markdown autolinking", () => {
  it("renders a markdown link with safe external attributes", () => {
    render(<Markdown content={"See [the portal](https://ura.go.ug) today."} />);
    const a = screen.getByRole("link", { name: "the portal" });
    expect(a).toHaveAttribute("href", "https://ura.go.ug");
    expect(a).toHaveAttribute("target", "_blank");
    expect(a.getAttribute("rel")).toContain("noopener");
  });

  it("autolinks a bare URL and strips trailing punctuation", () => {
    render(<Markdown content={"Visit https://ura.go.ug. Thanks"} />);
    const a = screen.getByRole("link", { name: "https://ura.go.ug" });
    expect(a).toHaveAttribute("href", "https://ura.go.ug");
  });

  it("prepends https:// for www. links", () => {
    render(<Markdown content={"Go to www.ura.go.ug for help"} />);
    const a = screen.getByRole("link", { name: "www.ura.go.ug" });
    expect(a).toHaveAttribute("href", "https://www.ura.go.ug");
  });

  it("autolinks the scheme-less ura.go.ug domain", () => {
    render(<Markdown content={"Register at ura.go.ug to start."} />);
    const a = screen.getByRole("link", { name: "ura.go.ug" });
    expect(a).toHaveAttribute("href", "https://ura.go.ug");
  });

  it("autolinks email addresses as mailto (no new tab)", () => {
    render(<Markdown content={"Email services@ura.go.ug for support"} />);
    const a = screen.getByRole("link", { name: "services@ura.go.ug" });
    expect(a).toHaveAttribute("href", "mailto:services@ura.go.ug");
    expect(a).not.toHaveAttribute("target");
  });

  it("autolinks URA phone numbers as tel with separators stripped", () => {
    render(<Markdown content={"Call 0800 117 000 anytime"} />);
    const a = screen.getByRole("link", { name: "0800 117 000" });
    expect(a).toHaveAttribute("href", "tel:0800117000");
  });

  it("blocks javascript: links (rendered as text, not a link)", () => {
    render(<Markdown content={"[click me](javascript:alert(1))"} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("click me")).toBeInTheDocument();
  });

  it("allows tel: scheme inside a markdown link", () => {
    render(<Markdown content={"[call us](tel:0800117000)"} />);
    const a = screen.getByRole("link", { name: "call us" });
    expect(a).toHaveAttribute("href", "tel:0800117000");
    expect(a).not.toHaveAttribute("target");
  });

  it("keeps [n] as a citation pill, not a link", () => {
    const { container } = render(<Markdown content={"Per the Act [1] you must file."} />);
    expect(container.querySelector(".md-cite-ref")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("treats numeric link text [1](url) as a link, not a citation", () => {
    const { container } = render(<Markdown content={"See [1](https://ura.go.ug)."} />);
    const a = screen.getByRole("link", { name: "1" });
    expect(a).toHaveAttribute("href", "https://ura.go.ug");
    expect(container.querySelector(".md-cite-ref")).toBeNull();
  });

  it("does not mistake tax figures or TINs for phone numbers", () => {
    render(<Markdown content={"You owe UGX 1,000,000 by 2025 (TIN 1014567890)."} />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
