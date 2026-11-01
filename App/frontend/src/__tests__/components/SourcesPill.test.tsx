import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import SourcesPill from "../../components/SourcesPill";
import { useChatStore, type Citation, type ContextResource } from "../../store/useChatStore";

const onlineForm: ContextResource = {
  id: "tcc_online",
  title: "Tax clearance certificate",
  type: "online_form",
  format: "web",
  url: "https://ura.go.ug/en/tax-clearance/",
  description: "Apply for a tax clearance certificate (TCC).",
  effective_year: "FY2026-27",
  source_domain: "ura.go.ug",
};

const statute: ContextResource = {
  id: "statute_tpca",
  title: "Tax Procedures Code Act",
  type: "statutory_source",
  format: "pdf",
  url: "https://ura.go.ug/download-category/laws-and-acts/",
  citation: "Cap. 343, s. 43",
  source_domain: "ura.go.ug",
};

const passage: Citation = {
  ref: "[1]",
  source: "ura_tax_clearance_faqs.csv",
  page: "2",
  section: "TCC",
  passage: "A tax clearance certificate confirms that a taxpayer has filed all returns and paid all taxes due.",
};

describe("SourcesPill", () => {
  afterEach(() => {
    vi.useRealTimers();
    useChatStore.setState({ locale: "en" });
  });

  it("renders nothing when the answer has no sources", () => {
    const { container } = render(<SourcesPill citations={[]} resources={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("counts forms and passages together and keeps the list closed", () => {
    render(<SourcesPill citations={[passage]} resources={[onlineForm, statute]} />);
    const pill = screen.getByRole("button", { name: "Sources (3)" });
    expect(pill).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Tax clearance certificate")).not.toBeInTheDocument();
  });

  it("does not count the same document twice", () => {
    const sameAsForm: Citation = { ref: "[2]", source: "tcc.html", url: "https://ura.go.ug/en/tax-clearance" };
    render(<SourcesPill citations={[sameAsForm]} resources={[onlineForm, onlineForm]} />);
    expect(screen.getByRole("button", { name: "Sources (1)" })).toBeInTheDocument();
  });

  it("keeps distinct passages of one document that has no URL of its own", () => {
    const page5: Citation = { ref: "[1]", source: "ura_vat_faqs.csv", page: "5" };
    const page7: Citation = { ref: "[2]", source: "ura_vat_faqs.csv", page: "7" };
    render(<SourcesPill citations={[page5, page7]} />);
    expect(screen.getByRole("button", { name: "Sources (2)" })).toBeInTheDocument();
  });

  it("opens on click with grouped, safe outbound links, and closes on a second click", () => {
    render(<SourcesPill citations={[passage]} resources={[onlineForm, statute]} />);
    const pill = screen.getByRole("button", { name: "Sources (3)" });
    fireEvent.click(pill);

    expect(pill).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Forms & services")).toBeInTheDocument();
    expect(screen.getByText("References")).toBeInTheDocument();
    const form = screen.getByText("Tax clearance certificate").closest("a");
    expect(form).toHaveAttribute("href", onlineForm.url);
    expect(form).toHaveAttribute("target", "_blank");
    expect(form?.getAttribute("rel")).toContain("noopener");
    expect(screen.getByText(/Cap\. 343, s\. 43/)).toBeInTheDocument();
    // A corpus filename reads as a document name.
    expect(screen.getByText("Tax Clearance")).toBeInTheDocument();

    fireEvent.click(pill);
    expect(pill).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("sources-popover")).not.toBeInTheDocument();
  });

  it("opens while hovered and closes after the pointer leaves", () => {
    vi.useFakeTimers();
    render(<SourcesPill resources={[onlineForm]} />);
    const pill = screen.getByRole("button", { name: "Sources (1)" });

    fireEvent.pointerEnter(pill, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(200));
    expect(screen.getByTestId("sources-popover")).toBeInTheDocument();

    fireEvent.pointerLeave(pill, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(250));
    expect(screen.queryByTestId("sources-popover")).not.toBeInTheDocument();
  });

  it("ignores touch hover so a tap is the only way in on phones", () => {
    vi.useFakeTimers();
    render(<SourcesPill resources={[onlineForm]} />);
    const pill = screen.getByRole("button", { name: "Sources (1)" });
    fireEvent.pointerEnter(pill, { pointerType: "touch" });
    act(() => vi.advanceTimersByTime(500));
    expect(screen.queryByTestId("sources-popover")).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the pill", () => {
    render(<SourcesPill resources={[onlineForm]} />);
    const pill = screen.getByRole("button", { name: "Sources (1)" });
    fireEvent.click(pill);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("sources-popover")).not.toBeInTheDocument();
    expect(pill).toHaveFocus();
  });

  it("closes when the reader clicks elsewhere", () => {
    render(
      <div>
        <SourcesPill resources={[onlineForm]} />
        <p>Elsewhere</p>
      </div>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Sources (1)" }));
    fireEvent.pointerDown(screen.getByText("Elsewhere"));
    expect(screen.queryByTestId("sources-popover")).not.toBeInTheDocument();
  });

  it("never renders a non-http resource URL as a link", () => {
    const unsafe: ContextResource = { title: "Odd link", type: "guide", url: "javascript:alert(1)" };
    render(<SourcesPill resources={[unsafe]} />);
    fireEvent.click(screen.getByRole("button", { name: "Sources (1)" }));
    expect(screen.getByText("Odd link").closest("a")).toBeNull();
  });

  it("dates a web page by its publisher, never by when it was crawled", () => {
    const page: Citation = {
      ref: "[1]",
      source: "6ec19a6e4bbeaec9.json",
      url: "https://ura.go.ug/en/domestic-taxes/paye-rates/",
      title: "PAYE rates",
      published_at: "2023-08-23",
    };
    render(<SourcesPill citations={[page]} />);
    fireEvent.click(screen.getByRole("button", { name: /sources/i }));
    expect(screen.getByText(/Published 2023-08-23/)).toBeInTheDocument();
  });

  it("shows no date at all for a page with none", () => {
    const page: Citation = { ref: "[1]", source: "abc.json", url: "https://ura.go.ug/en/x/", title: "A page" };
    render(<SourcesPill citations={[page]} />);
    fireEvent.click(screen.getByRole("button", { name: /sources/i }));
    expect(screen.queryByText(/Published/)).not.toBeInTheDocument();
  });

  it("speaks the reader's language", () => {
    useChatStore.setState({ locale: "sw" });
    render(<SourcesPill resources={[onlineForm, statute]} />);
    expect(screen.getByRole("button", { name: "Vyanzo (2)" })).toHaveTextContent("Vyanzo");
  });
});
