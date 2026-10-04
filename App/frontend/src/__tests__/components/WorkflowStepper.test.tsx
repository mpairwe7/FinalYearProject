import { afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WorkflowStepper from "../../components/WorkflowStepper";
import { useChatStore, type WorkflowState } from "../../store/useChatStore";

describe("WorkflowStepper", () => {
  afterEach(() => useChatStore.setState({ locale: "en" }));

  const activeWorkflow: WorkflowState = {
    id: "tin_registration",
    name: "TIN Registration",
    status: "active",
    step_index: 1,
    total_steps: 4,
    step_id: "collect_type",
    step_title: "Taxpayer Entity Type",
    ui_widget: "options",
    options: ["individual", "company", "ngo"],
    all_steps: [
      { id: "collect_type", title: "Entity Type", status: "current" },
      { id: "collect_name", title: "Legal Name", status: "pending" },
      { id: "collect_nin", title: "NIN Verification", status: "pending" },
      { id: "summary", title: "Portal Submission", status: "pending" },
    ],
    portal_action: {
      label: "Open URA e-Services ↗",
      url: "https://portal.ura.go.ug",
    },
  };

  it("renders workflow name and step progress pill", () => {
    render(<WorkflowStepper workflow={activeWorkflow} />);
    expect(screen.getByText("TIN Registration")).toBeInTheDocument();
    expect(screen.getByText("Step 1 of 4")).toBeInTheDocument();
    expect(screen.getByText(/Taxpayer Entity Type/)).toBeInTheDocument();
  });

  it("renders step nodes in track", () => {
    render(<WorkflowStepper workflow={activeWorkflow} />);
    expect(screen.getByText("Entity Type")).toBeInTheDocument();
    expect(screen.getByText("Legal Name")).toBeInTheDocument();
    expect(screen.getByText("NIN Verification")).toBeInTheDocument();
  });

  it("renders interactive options and triggers onSelectOption on click", () => {
    const onSelect = vi.fn();
    render(<WorkflowStepper workflow={activeWorkflow} onSelectOption={onSelect} />);

    const indBtn = screen.getByText("individual");
    expect(indBtn).toBeInTheDocument();
    fireEvent.click(indBtn);
    expect(onSelect).toHaveBeenCalledWith("individual");

    const compBtn = screen.getByText("company");
    fireEvent.click(compBtn);
    expect(onSelect).toHaveBeenCalledWith("company");
  });

  it("shows translated option labels while submitting the validator value", () => {
    const onSelect = vi.fn();
    const localizedWorkflow: WorkflowState = {
      ...activeWorkflow,
      options: ["efris invoice", "standard tax invoice"],
      option_labels: ["Ankara ya EFRIS", "Ankara ya kawaida ya kodi"],
    };
    render(<WorkflowStepper workflow={localizedWorkflow} onSelectOption={onSelect} />);

    fireEvent.click(screen.getByText("Ankara ya EFRIS"));
    expect(onSelect).toHaveBeenCalledWith("efris invoice");
  });

  it("localizes step controls with the selected assistant language", () => {
    useChatStore.setState({ locale: "sw" });
    render(<WorkflowStepper workflow={activeWorkflow} onSelectOption={vi.fn()} />);

    expect(screen.getByText("Hatua 1 kati ya 4")).toBeInTheDocument();
    expect(screen.getByText("Chagua chaguo ili kuendelea:")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ghairi mwongozo" })).toBeInTheDocument();
  });

  it("renders portal link and cancel button", () => {
    const onSelect = vi.fn();
    render(<WorkflowStepper workflow={activeWorkflow} onSelectOption={onSelect} />);

    const portalLink = screen.getByText("Open URA e-Services ↗");
    expect(portalLink).toHaveAttribute("href", "https://portal.ura.go.ug");

    const cancelBtn = screen.getByText("Cancel workflow");
    fireEvent.click(cancelBtn);
    expect(onSelect).toHaveBeenCalledWith("cancel");
  });

  it("renders completed state with completed pill", () => {
    const completedWorkflow: WorkflowState = {
      id: "tin_registration",
      name: "TIN Registration",
      status: "completed",
      step_index: 4,
      total_steps: 4,
    };
    render(<WorkflowStepper workflow={completedWorkflow} />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(screen.queryByText("Cancel workflow")).not.toBeInTheDocument();
  });
});
