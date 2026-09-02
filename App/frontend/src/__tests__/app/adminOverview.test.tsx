/**
 * Operations overview — the three counts that used to be wrong.
 *
 * Each case here pins one wire between a rendered figure and the payload it
 * claims to come from. All three shipped green because nothing rendered the
 * component against the shape the API actually returns: two read fields the
 * backend has never emitted, and one counted an array the page had truncated
 * for a different purpose.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminOverviewPage from "../../app/admin/page";
import { analyticsApi, type TicketQueueItem } from "../../services/analyticsApi";

/** What GET /v1/authority/status actually returns. No `sources` key. */
const AUTHORITY_FRESH = {
  ok: true,
  configured: true,
  fresh: true,
  version: "2026-07",
  generated_at: "2026-07-01T00:00:00Z",
  age_days: 12,
  max_age_days: 120,
  sources_checked: 7,
  invalid_sources: [],
  errors: [],
};

function queueRow(over: Partial<TicketQueueItem> = {}): TicketQueueItem {
  const now = Date.now() / 1000;
  return {
    id: `tkt-${Math.random().toString(36).slice(2, 8)}`,
    status: "open",
    priority: "normal",
    reason: "Needs a human",
    user_query: "How do I object to an assessment?",
    bot_reply: "I am not sure.",
    created_at: now - 3600,
    updated_at: now,
    assignee: "",
    ...over,
  };
}

/** The page loads 20 open rows for its list; every one of them unclaimed. */
const PAGE_OF_TWENTY = Array.from({ length: 20 }, () => queueRow());

/** A stand-in for the one bespoke fetch on this page. `Partial<Response>`
 *  cannot describe it: `body` there is a ReadableStream, not the payload. */
interface StubResponse {
  ok: boolean;
  status?: number;
  payload: unknown;
}

function mockAuthority({ ok, status, payload }: StubResponse) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/v1/authority/status")) {
        return {
          ok,
          status: status ?? (ok ? 200 : 401),
          statusText: ok ? "OK" : "Unauthorized",
          json: async () => payload,
        } as Response;
      }
      throw new Error(`unexpected fetch: ${String(input)}`);
    }),
  );
}

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminOverviewPage />
    </QueryClientProvider>,
  );
}

/** StaffGuard gates on a token + an authenticated /v1/me, so stand in for both. */
vi.mock("../../components/StaffGuard", () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role: "ura_admin", email: "admin@ura.go.ug" }),
}));

describe("Operations overview", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin");
    vi.restoreAllMocks();
    mockAuthority({ ok: true, payload: AUTHORITY_FRESH });

    vi.spyOn(analyticsApi, "ticketStats").mockResolvedValue({
      total: 2662,
      open: 2567,
      assigned: 94,
      resolved: 1,
      wontfix: 0,
      by_priority: { normal: 2553, high: 106, urgent: 3 },
    });
    vi.spyOn(analyticsApi, "ticketSla").mockResolvedValue({
      period_days: 30,
      tickets: 2662,
      responded: 91,
      resolved: 1,
      awaiting_first_response: 2586,
      awaiting_next_response: 91,
      unassigned: 2567,
      median_response_seconds: 900,
      median_resolution_seconds: 7200,
      median_next_reply_seconds: 3600,
      breaching: 2630,
    });
    vi.spyOn(analyticsApi, "tickets").mockResolvedValue({
      count: PAGE_OF_TWENTY.length,
      status_filter: "open",
      limit: 20,
      offset: 0,
      tickets: PAGE_OF_TWENTY,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** The stat tile with this label. "Unassigned" is also a queue-row meta. */
  async function statCard(label: string): Promise<HTMLElement> {
    const grid = await screen.findByLabelText("Escalation summary");
    const hit = await waitFor(() =>
      within(grid)
        .getAllByText(label)
        .find((node) => node.classList.contains("ops-stat-label")),
    );
    if (!hit) throw new Error(`no stat tile labelled ${label}`);
    return hit.closest(".ops-stat") as HTMLElement;
  }

  it("counts unassigned cases from the SLA payload, not the page it loaded", async () => {
    renderOverview();
    const card = await statCard("Unassigned");
    // 2567 open cases are unclaimed. Derived from the loaded rows this read 20,
    // sitting beside an "Open escalations" tile that reported the real total.
    await waitFor(() => expect(within(card).getByText("2567")).toBeInTheDocument());
    expect(within(card).queryByText("20")).toBeNull();
  });

  it("sends the live tiles to a view that holds the cases they count", async () => {
    renderOverview();
    for (const label of ["Awaiting first response", "Past 24-hour SLA", "Unassigned"]) {
      // These count open AND in-progress; ?status=open showed fewer rows than
      // the number that sent the reader there.
      expect(await statCard(label)).toHaveAttribute("href", "/admin/tickets?status=any");
    }
  });

  it("does not name the loaded page size as the size of the queue", async () => {
    renderOverview();
    await statCard("Unassigned");
    // Was `All ${allOpen.length} open` — "All 20 open" on a queue of 2,567.
    expect(screen.queryByText("All 20 open")).toBeNull();
    expect(screen.getByText("All open")).toBeInTheDocument();
  });

  it("reads the source count the authority endpoint actually emits", async () => {
    renderOverview();
    const sources = await screen.findByText("Sources");
    const row = sources.closest("div") as HTMLElement;
    // sources_checked, not sources.length — the latter is a key
    // get_authority_status() has never returned, so the row was always "—".
    await waitFor(() => expect(within(row).getByText(/7 checked/)).toBeInTheDocument());
  });

  it("reports a rejected authority request as an error, not a stale manifest", async () => {
    mockAuthority({ ok: false, status: 401, payload: { detail: "authentication required" } });
    renderOverview();
    await screen.findByText("Sources");
    // A FastAPI error body parses cleanly, so an unchecked r.json() left
    // `fresh` undefined and the panel announced the alarm state for what was
    // really an expired token.
    await waitFor(() =>
      expect(screen.getByText(/Authority status unavailable/)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Stale or missing")).toBeNull();
  });
});
