import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WorkflowStepper from "../../components/WorkflowStepper";
import type { WorkflowState } from "../../store/useChatStore";

describe("WorkflowStepper", () => {
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
