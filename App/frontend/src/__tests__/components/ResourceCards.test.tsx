import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ResourceCards from "../../components/ResourceCards";
import type { ContextResource } from "../../store/useChatStore";

describe("ResourceCards", () => {
  const mockResources: ContextResource[] = [
    {
      id: "form_vat_offline_template",
      title: "VAT Return Offline Excel Template (FY2026-27)",
      type: "downloadable_form",
      format: "xlsx",
      size: "245 KB",
      url: "https://portal.ura.go.ug/downloads/forms/vat_return_template.xlsx",
      description: "Official URA macro-enabled Excel return template.",
      effective_year: "FY2026-27",
      source_domain: "portal.ura.go.ug",
      checklist: ["Input tax credit EFRIS receipts", "Export customs declarations"],
    },
    {
      id: "form_vat_online_file",
      title: "Submit VAT Return Online — URA e-Services",
      type: "online_form",
      format: "web",
      url: "https://portal.ura.go.ug/eservices/returns?tax_type=vat",
      description: "Direct portal declaration module.",
      effective_year: "FY2026-27",
      source_domain: "portal.ura.go.ug",
    },
    {
      id: "statute_vat_act",
      title: "Value Added Tax Act (Cap. 349)",
      type: "statutory_source",
      format: "pdf",
      size: "1.8 MB",
      url: "https://ura.go.ug/en/download/value-added-tax-amendment-act-2023/",
      citation: "Cap. 349, Section 31",
      description: "Statutory basis for standard 18% VAT.",
      source_domain: "ura.go.ug",
    },
  ];

  it("renders container header, verified banner, and count badge", () => {
    render(<ResourceCards resources={mockResources} />);
    expect(screen.getByText("Official URA Forms, Templates & Sources")).toBeInTheDocument();
    expect(screen.getByText("✓ Official Verified Sources")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders downloadable form with xlsx badge and download action", () => {
    render(<ResourceCards resources={mockResources} />);
    expect(screen.getByText("VAT Return Offline Excel Template (FY2026-27)")).toBeInTheDocument();
    expect(screen.getByText(".xlsx")).toBeInTheDocument();
    expect(screen.getByText("245 KB")).toBeInTheDocument();
    const downloadBtn = screen.getByText("Download Template").closest("a");
    expect(downloadBtn).toHaveAttribute("href", "https://portal.ura.go.ug/downloads/forms/vat_return_template.xlsx");
  });

  it("renders online form with open portal link", () => {
    render(<ResourceCards resources={mockResources} />);
    expect(screen.getByText("Submit VAT Return Online — URA e-Services")).toBeInTheDocument();
    const openBtn = screen.getByText("Open Online Form").closest("a");
    expect(openBtn).toHaveAttribute("href", "https://portal.ura.go.ug/eservices/returns?tax_type=vat");
  });

  it("renders statutory citation and checklist items", () => {
    render(<ResourceCards resources={mockResources} />);
    expect(screen.getByText("Cap. 349, Section 31")).toBeInTheDocument();
    expect(screen.getByText("Checklist before submitting:")).toBeInTheDocument();
    expect(screen.getByText("Input tax credit EFRIS receipts")).toBeInTheDocument();
  });

  it("renders null when resources array is empty", () => {
    const { container } = render(<ResourceCards resources={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
