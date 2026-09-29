/**
 * The guided-journeys panel on /analytics loads on its own request. When that
 * request fails the panel must say so: hiding it read as "no journeys in this
 * period" (CodeRabbit, PR #517).
 */
import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const refetchJourneys = vi.fn();
const hooks = vi.hoisted(() => ({ journeys: {} as Record<string, unknown> }));

vi.mock("../../components/StaffGuard", () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role: "ura_admin", email: "admin@ura.go.ug" }),
}));

const ok = (data: unknown) => ({ data, isError: false, isLoading: false, refetch: vi.fn(), dataUpdatedAt: 0 });

vi.mock("../../hooks/useAnalyticsDashboard", () => ({
  useDashboard: () =>
    ok({
      conversations: { total_conversations: 0, by_locale: {}, by_topic: {} },
      sessions: { total_sessions: 0 },
      requests: { total: 0, latency: {}, by_route: {}, by_retrieval_mode: {} },
    }),
  useFeedbackSummary: () => ok({ total: 0, thumbs_up: 0, thumbs_down: 0 }),
  useJourneyFunnel: () => hooks.journeys,
  useTicketStats: () => ok({ total: 0 }),
  useTicketQueue: () => ok({ tickets: [] }),
}));

import AnalyticsPage from "../../app/analytics/page";

describe("analytics: guided journeys", () => {
  beforeEach(() => refetchJourneys.mockReset());

  it("says the journey figures failed and offers a retry", () => {
    hooks.journeys = { data: undefined, isError: true, isLoading: false, refetch: refetchJourneys };
    render(<AnalyticsPage />);
    expect(screen.getByText("Guided journey figures did not load")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /try again|retry/i }).at(-1)!);
    expect(refetchJourneys).toHaveBeenCalled();
  });

  it("shows the panel when the figures load", () => {
    hooks.journeys = ok({ period_days: 30, abandon_after_hours: 24, journeys: [] });
    render(<AnalyticsPage />);
    expect(screen.getByText("Guided journeys")).toBeInTheDocument();
    expect(screen.queryByText("Guided journey figures did not load")).toBeNull();
  });
});
