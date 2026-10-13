/**
 * Service quality overview — where the administrator's audit starts.
 *
 * Each figure pins one wire between a rendered tile and the payload it claims
 * to come from: the four streams the audit reads, each against the period
 * before and each opening its tab. The Needs review panel lists what is
 * waiting and opens each item where it is read in full.
 *
 * The rest pin the role split: an administrator audits the service officers
 * give, so nothing on this page links into the live queue or takes a case.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AdminOverviewPage, { aiCallsHeadline, pointsHint } from "../../app/admin/page";
import {
  analyticsApi,
  type AiChatSummary,
  type NeedsReviewItem,
  type NeedsReviewPage,
  type QualitySummary,
  type RatingsHeadline,
} from "../../services/analyticsApi";
import { callsApi } from "../../services/callsApi";

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

const NOW = Date.now() / 1000;

function headline(over: Partial<RatingsHeadline> = {}): RatingsHeadline {
  return {
    truncated: false,
    asked: 0,
    answered: 0,
    response_rate: null,
    average: null,
    csat_pct: null,
    low: 0,
    distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 },
    sentiment: { positive: 0, neutral: 0, negative: 0, unclear: 0 },
    positive_pct: null,
    negative_pct: null,
    previous: { asked: 0, answered: 0, response_rate: null, average: null, csat_pct: null, positive_pct: null, negative_pct: null },
    ...over,
  };
}

const AI_CHAT = {
  period_days: 30,
  truncated: false,
  rated_answers: 12,
  helpful: 8,
  unhelpful: 4,
  helpful_pct: 66.7,
  comments: 2,
  conversations: 5,
  unlinked_answers: 0,
  patterns: { went_wrong: 2, never_helpful: 1, recovered: 0, all_helpful: 2 },
  median_break_answer: 3,
  by_position: [],
  by_topic: [],
  by_route: [],
  by_language: [],
  by_day: [],
  previous: { rated_answers: 10, helpful_pct: 70, truncated: false },
} satisfies AiChatSummary;

const SUMMARY: QualitySummary = {
  period_days: 30,
  ai_chat: AI_CHAT,
  // 6 of 8 answered at goodbye were positive, 1 of 2 while transferring: 7 of 10.
  ai_calls: {
    end: headline({
      asked: 10, answered: 8, positive_pct: 75, sentiment: { positive: 6, neutral: 1, negative: 1, unclear: 0 },
      previous: { asked: 5, answered: 4, response_rate: 80, average: null, csat_pct: null, positive_pct: 50, negative_pct: 50 },
    }),
    transfer: headline({
      asked: 4, answered: 2, positive_pct: 50, sentiment: { positive: 1, neutral: 0, negative: 1, unclear: 0 },
      previous: { asked: 1, answered: 1, response_rate: 100, average: null, csat_pct: null, positive_pct: 100, negative_pct: 0 },
    }),
  },
  officer_chat: headline({
    asked: 6, answered: 4, average: 3.25, csat_pct: 50, low: 1,
    previous: { asked: 4, answered: 4, response_rate: 100, average: 4, csat_pct: 75, positive_pct: 75, negative_pct: 0 },
  }),
  officer_calls: headline({ asked: 3, answered: 0 }),
  needs_review: { open: 9, action_needed: 1 },
};

function item(over: Partial<NeedsReviewItem> = {}): NeedsReviewItem {
  return {
    item_type: "rating",
    item_id: `r-${Math.random().toString(36).slice(2, 8)}`,
    stream: "officer_chat",
    title: "Officer chat rated 2/5 — Officer Okello",
    quote: "",
    comment: "Took too long to answer.",
    topic: "vat",
    locale: "en",
    officer_name: "Officer Okello",
    occurred_at: NOW - 600,
    link: { tab: "officer-chat", param: "ticket", value: "tkt-1" },
    review: null,
    ...over,
  };
}

const QUEUE: NeedsReviewPage = {
  period_days: 30,
  truncated: false,
  counts: { open: 9, action_needed: 1, reviewed: 3, all: 13 },
  total: 9,
  limit: 6,
  offset: 0,
  items: [
    item({
      item_type: "ai_chat", item_id: "conv-went-wrong", stream: "ai_chat", title: "AI chat went wrong at answer 3",
      quote: "What if I am a sole proprietor?", comment: "", officer_name: "",
      link: { tab: "ai-chat", param: "conversation", value: "conv-went-wrong" },
    }),
    item({ item_id: "r-low" }),
    item({
      item_id: "r-call", stream: "ai_call", title: "Caller said the AI didn't help (while being transferred)",
      comment: "Not really, no.", officer_name: "", link: { tab: "ai-calls", param: "", value: "" },
    }),
  ],
};

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

describe("Service quality overview", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin");
    vi.restoreAllMocks();
    mockAuthority({ ok: true, payload: AUTHORITY_FRESH });

    vi.spyOn(analyticsApi, "qualitySummary").mockResolvedValue(SUMMARY);
    vi.spyOn(analyticsApi, "qualityNeedsReview").mockResolvedValue(QUEUE);
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
    const now = Date.now() / 1000;
    vi.spyOn(callsApi, "getPresence").mockResolvedValue({
      teams: ["general", "domestic_taxes"],
      officers: [
        {
          user_id: "u-nakato", display_name: "Officer Nakato", status: "on_call", languages: ["en", "lg"],
          teams: ["domestic_taxes"], current_call_id: "call_abc123", last_seen: now - 30, updated_at: now - 30,
        },
        {
          user_id: "u-okello", display_name: "Officer Okello", status: "available", languages: ["en"],
          teams: ["general"], current_call_id: "", last_seen: now - 60, updated_at: now - 60,
        },
        {
          user_id: "u-auma", display_name: "Officer Auma", status: "offline", languages: ["sw"],
          teams: [], current_call_id: "", last_seen: now - 7200, updated_at: now - 7200,
        },
      ],
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** The stat tile with this label. */
  async function statCard(label: string): Promise<HTMLElement> {
    const grid = await screen.findByLabelText("Service quality summary");
    const hit = await waitFor(() => {
      const found = within(grid)
        .getAllByText(label)
        .find((node) => node.classList.contains("ops-stat-label"));
      if (!found) throw new Error(`no stat tile labelled ${label}`);
      return found;
    });
    return hit.closest(".ops-stat") as HTMLElement;
  }

  /** The tile's figure as read: the unit is a span of its own. */
  function value(card: HTMLElement): string {
    return card.querySelector(".ops-stat-value")?.textContent ?? "";
  }

  it("is the quality overview, not an escalation board", async () => {
    renderOverview();
    expect(await screen.findByRole("heading", { level: 1, name: "Service quality overview" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open quality audit/ })).toHaveAttribute("href", "/admin/quality");
    expect(screen.queryByLabelText("Escalation summary")).toBeNull();
    expect(screen.queryByLabelText("Escalation volume")).toBeNull();
    expect(screen.queryByText("Workload")).toBeNull();
  });

  it("leads with each stream's rating against the period before, each opening its tab", async () => {
    renderOverview();
    const ai = await statCard("AI answers found helpful");
    expect(ai).toHaveAttribute("href", "/admin/quality");
    expect(value(ai)).toBe("66.7%");
    expect(ai.textContent).toContain("8 of 12 rated in chat · down 3.3 points from 70% in the period before");

    // Both moments together: 7 of 10 answered reviews said the AI helped; 3 of 5 before.
    const calls = await statCard("Callers said the AI helped");
    expect(calls).toHaveAttribute("href", "/admin/quality?tab=ai-calls");
    expect(value(calls)).toBe("70%");
    expect(calls.textContent).toContain("10 of 14 callers answered the review · up 10 points from 60% in the period before");

    const chat = await statCard("Officer chat satisfaction");
    expect(chat).toHaveAttribute("href", "/admin/quality?tab=officer-chat");
    expect(value(chat)).toBe("3.25/5");
    expect(chat.textContent).toContain("50% satisfied (4 or 5) of 4 rated · down 25 points from 75% in the period before");

    // Asked, but nobody rated: no average invented from nothing.
    const call = await statCard("Officer call satisfaction");
    expect(call).toHaveAttribute("href", "/admin/quality?tab=officer-calls");
    expect(value(call)).toBe("—");
    expect(call.textContent).toContain("3 asked, none rated yet");
  });

  it("lists what is waiting for a review, each opening where it is read in full", async () => {
    renderOverview();
    const list = await screen.findByRole("list", { name: "Needs review, newest first" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByRole("link", { name: "AI chat went wrong at answer 3" })).toHaveAttribute(
      "href",
      "/admin/quality?conversation=conv-went-wrong",
    );
    expect(items[0].textContent).toContain("Asked: What if I am a sole proprietor?");
    expect(within(items[1]).getByRole("link")).toHaveAttribute("href", "/admin/quality?tab=officer-chat&ticket=tkt-1");
    expect(items[1].textContent).toContain("Said: Took too long to answer.");
    expect(within(items[2]).getByRole("link")).toHaveAttribute("href", "/admin/quality?tab=ai-calls");
    expect(screen.getByText("9 open · 1 action needed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /See all 9 open items/ })).toHaveAttribute(
      "href",
      "/admin/quality?tab=needs-review",
    );
    // The newest open items only, for the period chosen.
    expect(analyticsApi.qualityNeedsReview).toHaveBeenCalledWith({ days: 30, status: "open", stream: "", limit: 6 });
  });

  it("says so when nothing is waiting", async () => {
    vi.spyOn(analyticsApi, "qualityNeedsReview").mockResolvedValue({
      ...QUEUE,
      counts: { open: 0, action_needed: 0, reviewed: 3, all: 3 },
      total: 0,
      items: [],
    });
    renderOverview();
    expect(await screen.findByText("Nothing waiting for a review")).toBeInTheDocument();
    expect(screen.getByText("0 open")).toBeInTheDocument();
  });

  it("shows the service levels as figures, not as links into the officers' queue", async () => {
    renderOverview();
    const panel = (await screen.findByRole("heading", { name: "Service levels" })).closest("section") as HTMLElement;
    await waitFor(() => expect(within(panel).getByText("2567")).toBeInTheDocument());
    expect(within(panel).getByText("2630")).toBeInTheDocument();
    expect(within(panel).queryAllByRole("link")).toEqual([]);
    // Nothing on the board opens the queue, a case or a call.
    await screen.findByRole("list", { name: "Needs review, newest first" });
    const links = screen.queryAllByRole("link").map((a) => a.getAttribute("href") ?? "");
    expect(links.filter((href) => /^\/(agent|admin\/tickets|calls)/.test(href))).toEqual([]);
    expect(screen.queryByText("Waiting longest")).toBeNull();
    expect(screen.queryByText(/Open the full queue/)).toBeNull();
  });

  it("lists the officers on duty, available first and read-only", async () => {
    renderOverview();
    const roster = await screen.findByRole("table");
    const rows = within(roster).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual([
      "Officer Okello",
      "Officer Nakato",
      "Officer Auma",
    ]);
    expect(within(rows[1]).getByText("On a call")).toBeInTheDocument();
    // An officer's current call is theirs: the board names no call to open.
    expect(screen.queryByText(/call_abc/)).toBeNull();
    expect(within(roster).queryAllByRole("button")).toEqual([]);
    expect(screen.getByText(/available ·/).textContent).toMatch(/1 available · 1 on a call · 2 online of 3/);
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

  it("says the quality audit did not answer rather than showing empty figures", async () => {
    vi.spyOn(analyticsApi, "qualitySummary").mockRejectedValue(new Error("503 Service Unavailable"));
    renderOverview();
    // The hook retries once before it gives up.
    expect(await screen.findByText("The quality audit did not answer", {}, { timeout: 5000 })).toBeInTheDocument();
  });
});

describe("overview readings", () => {
  it("words a change in points, or says there was nothing to compare", () => {
    expect(pointsHint(72, 68)).toBe("up 4 points from 68% in the period before");
    expect(pointsHint(60, 60)).toBe("unchanged from 60% in the period before");
    expect(pointsHint(50, null)).toBe("nothing to compare in the period before");
  });

  it("adds both moments' counts before dividing", () => {
    const both = aiCallsHeadline(SUMMARY.ai_calls);
    expect(both).toEqual({ asked: 14, answered: 10, positivePct: 70, previousPositivePct: 60 });
    expect(aiCallsHeadline(undefined)).toEqual({ asked: 0, answered: 0, positivePct: null, previousPositivePct: null });
  });
});
