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
    expect(screen.getByText("Entity Type").closest('[role="listitem"]')).toHaveAttribute('aria-current', 'step');
  });

  it('does not expose an unsafe model-supplied portal action', () => {
    render(<WorkflowStepper workflow={{ ...activeWorkflow, portal_action: { label: 'Unsafe portal', url: 'https://ura.go.ug.evil.example' } }} />);
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  it.each(['completed', 'cancelled'] as const)('does not remediate or register from a %s workflow', (status) => {
    render(<WorkflowStepper onSelectOption={vi.fn()} workflow={{ ...activeWorkflow, status,
      prerequisites: [{ id: 'id', label: 'Identity', status: 'missing', remediation_action: 'Start identity workflow' }],
      step_up_auth: { required: true, mode: 'login', reason: 'Sign in', register_action: 'Register' },
    }} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
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

  it("renders October 2026 Journey Copilot surfaces (prerequisites, form draft, GPS, scaffolding, step-up auth, resume code)", () => {
    const onSelect = vi.fn();
    const enrichedWorkflow: WorkflowState = {
      ...activeWorkflow,
      resume_code: "URA-TIN-S1-9F2A",
      journey_gps: {
        workflow_id: "tin_registration",
        active_goal: "TIN Registration",
        step_index: 1,
        total_steps: 4,
        completion_pct: 25,
        is_paused_for_tangent: true,
      },
      prerequisites: [
        { id: "applicant_category", label: "Applicant classification", status: "satisfied" },
        {
          id: "identity_or_ursb_docs",
          label: "Original NIRA ID or URSB Certificate",
          status: "missing",
          remediation_action: "Guide me step by step through Cross-MDA & NTR Verification Guide",
        },
      ],
      scaffolding: {
        failed_attempts: 1,
        hint: "Choose individual, company, or ngo.",
      },
      form_draft: {
        form_code: "URA-REG-DT-101",
        title: "TIN Checklist",
        completion_pct: 50,
        fields: [
          { box: "Sec 1", label: "Applicant Classification", slot: "taxpayer_type", status: "filled", display_value: "Recorded" },
        ],
      },
      step_up_auth: {
        required: true,
        mode: "login_or_register",
        reason: "Sign in to submit on the URA portal.",
        preserved_slot_count: 2,
        register_action: "Guide me step by step through TIN Registration Guide",
      },
    };

    render(<WorkflowStepper workflow={enrichedWorkflow} onSelectOption={onSelect} />);
    expect(screen.getByTestId("workflow-resume-code")).toHaveTextContent("URA-TIN-S1-9F2A");
    expect(screen.getByTestId("workflow-journey-gps")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-prerequisites")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-scaffolding")).toHaveTextContent("Choose individual, company, or ngo.");
    expect(screen.getByTestId("workflow-form-draft")).toHaveTextContent("URA-REG-DT-101 · 50%");
    expect(screen.getByTestId("workflow-step-up-auth")).toHaveTextContent("2 answers saved");
    expect(screen.getByTestId("workflow-step-up-action-btn")).toHaveTextContent(
      "Guide me step by step through TIN Registration Guide",
    );

    fireEvent.click(screen.getByTestId("workflow-step-up-action-btn"));
    expect(onSelect).toHaveBeenCalledWith("Guide me step by step through TIN Registration Guide");

    fireEvent.click(screen.getByText("Guide me step by step through Cross-MDA & NTR Verification Guide"));
    expect(onSelect).toHaveBeenCalledWith("Guide me step by step through Cross-MDA & NTR Verification Guide");
  });
});
