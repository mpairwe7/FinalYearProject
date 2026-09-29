/**
 * The guided-journeys panel answers two questions for the customer experience
 * team: where do taxpayers give up, and which reply do they find least
 * helpful. These pin that both are named in plain words, and that an empty
 * period says so instead of drawing a table of zeros.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import JourneyFunnelTable from "../../../components/charts/JourneyFunnelTable";
import type { JourneyFunnel } from "../../../services/analyticsApi";

const step = (step_id: string, title: string, stopped = 0, helpful = 0, not_helpful = 0) => ({
  step_id,
  title,
  stopped,
  helpful,
  not_helpful,
});

const data: JourneyFunnel = {
  period_days: 30,
  abandon_after_hours: 24,
  journeys: [
    {
      workflow_id: "tax_clearance",
      name: "Tax Clearance Certificate",
      started: 10,
      completed: 6,
      cancelled: 1,
      abandoned: 2,
      in_progress: 1,
      completion_pct: 60,
      steps: [
        step("collect_taxpayer_type", "Taxpayer type", 0, 4, 0),
        step("collect_returns_filed", "Returns filed", 3, 1, 2),
      ],
    },
    {
      workflow_id: "motor_vehicle_registration",
      name: "Motor Vehicle Registration",
      started: 0,
      completed: 0,
      cancelled: 0,
      abandoned: 0,
      in_progress: 0,
      completion_pct: 0,
      steps: [step("collect_registration_kind", "Registration type")],
    },
  ],
};

describe("JourneyFunnelTable", () => {
  it("names the step where most journeys stop and the least helpful step", () => {
    render(<JourneyFunnelTable data={data} />);
    const row = screen.getByRole("row", { name: /Tax Clearance Certificate/ });
    expect(within(row).getByText("6 (60%)")).toBeInTheDocument();
    expect(within(row).getByText("Returns filed (3)")).toBeInTheDocument();
    expect(within(row).getByText("Returns filed (2 not helpful)")).toBeInTheDocument();
    // cancelled + abandoned, with the split on hover
    expect(within(row).getByTitle("1 cancelled, 2 abandoned")).toHaveTextContent("3");
  });

  it("lists a journey nobody started, without a made-up rate", () => {
    render(<JourneyFunnelTable data={data} />);
    const row = screen.getByRole("row", { name: /Motor Vehicle Registration/ });
    expect(within(row).getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("says so when no journey was started in the period", () => {
    render(<JourneyFunnelTable data={{ ...data, journeys: [data.journeys[1]] }} />);
    expect(screen.getByText("No guided journeys in this period")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("states how abandonment is counted", () => {
    render(<JourneyFunnelTable data={data} />);
    expect(screen.getByText(/untouched for 24 hours counts as abandoned/)).toBeInTheDocument();
  });
});
