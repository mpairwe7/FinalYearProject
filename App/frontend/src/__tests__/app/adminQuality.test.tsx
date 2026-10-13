/**
 * Service-quality audit — the AI answers a taxpayer rated, read in order.
 *
 * The case the page exists for: a conversation whose first two answers were
 * helpful and whose third was not. It must be listed first, say where it went
 * wrong, and open at that answer with the helpful one before it marked.
 */
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import QualityAuditPage from "../../app/admin/quality/page";
import { positionReading } from "../../components/quality/PositionChart";
import { ratingStripLabel } from "../../components/quality/RatingStrip";
import {
  analyticsApi,
  type AiChatPage,
  type AiChatSummary,
  type AiChatTimeline,
} from "../../services/analyticsApi";

vi.mock("../../components/StaffGuard", () => ({
  __esModule: true,
  default: ({ children }: { children: (who: unknown) => React.ReactNode }) =>
    children({ authenticated: true, role: "ura_admin", email: "admin@ura.go.ug" }),
}));

const NOW = Date.now() / 1000;

const SUMMARY: AiChatSummary = {
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
  by_position: [
    { position: "1", rated: 5, helpful: 5, helpful_pct: 100 },
    { position: "2", rated: 4, helpful: 2, helpful_pct: 50 },
    { position: "3", rated: 3, helpful: 1, helpful_pct: 33.3 },
    { position: "4", rated: 0, helpful: 0, helpful_pct: null },
    { position: "5+", rated: 0, helpful: 0, helpful_pct: null },
  ],
  by_topic: [{ key: "vat_registration", rated: 6, helpful: 3, unhelpful: 3, helpful_pct: 50 }],
  by_route: [{ key: "hybrid", rated: 10, helpful: 7, unhelpful: 3, helpful_pct: 70 }],
  by_language: [{ key: "en", rated: 12, helpful: 8, unhelpful: 4, helpful_pct: 66.7 }],
  by_day: [],
  previous: { rated_answers: 10, helpful_pct: 70, truncated: false },
};

const PAGE: AiChatPage = {
  period_days: 30,
  truncated: false,
  total: 1,
  limit: 25,
  offset: 0,
  conversations: [
    {
      conversation_id: "conv-went-wrong",
      key: "conv-went-wrong",
      linked: true,
      pattern: "went_wrong",
      ratings: [
        { answer: 1, rating: "up" },
        { answer: 2, rating: "up" },
        { answer: 3, rating: "down" },
      ],
      rated_answers: 3,
      unhelpful: 1,
      break_answer: 3,
      break_question: "What if I am a sole proprietor?",
      helpful_before_break: true,
      comments: 1,
      topic: "vat_registration",
      locale: "lg",
      first_rated_at: NOW - 600,
      last_rated_at: NOW - 60,
    },
  ],
};

const TIMELINE: AiChatTimeline = {
  conversation_id: "conv-went-wrong",
  pattern: "went_wrong",
  break_answer: 3,
  transcript_complete: true,
  transcript_kept_days: 7,
  answers: [
    { answer: 1, turn_id: "t1", question: "How do I register for VAT?", reply: "Apply on the eTax portal.", created_at: NOW - 900, sources: ["vat-act.pdf"], confidence: 0.8, topic: "vat_registration", rating: "up", comment: "", retrieval_mode: "hybrid", is_break: false },
    { answer: 2, turn_id: "t2", question: "What documents do I need?", reply: "Your TIN.", created_at: NOW - 800, sources: [], confidence: 0.7, topic: "vat_registration", rating: "up", comment: "", retrieval_mode: "hybrid", is_break: false },
    { answer: 3, turn_id: "t3", question: "What if I am a sole proprietor?", reply: "Sole proprietors cannot register.", created_at: NOW - 700, sources: [], confidence: 0.4, topic: "vat_registration", rating: "down", comment: "That is wrong.", retrieval_mode: "hybrid", is_break: true },
  ],
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QualityAuditPage />
    </QueryClientProvider>,
  );
}

describe("Service quality audit", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/quality");
    vi.spyOn(analyticsApi, "qualityAiChat").mockResolvedValue({ summary: SUMMARY, page: PAGE });
    vi.spyOn(analyticsApi, "qualityAiChatTimeline").mockResolvedValue(TIMELINE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("leads with how helpful the answers were and how that moved", async () => {
    renderPage();
    const stats = await screen.findByLabelText("AI answer ratings summary");
    // The tile sets its unit apart ("66.7" + "%"), so read the tile as a whole.
    const tile = await waitFor(() => {
      const label = within(stats).getByText("Answers found helpful");
      return label.closest(".ops-stat") as HTMLElement;
    });
    expect(tile.textContent).toContain("66.7%");
    expect(tile.textContent).toMatch(/down 3.3 points from 70% in the period before/);
    expect(stats.textContent).toContain("answer 3");
  });

  it("lists the conversation that went wrong, and where", async () => {
    renderPage();
    const row = await screen.findByRole("button", { name: /Went wrong at answer 3, after a helpful one/ });
    expect(within(row).getByRole("img", { name: "Answer 1 helpful, answer 2 helpful, answer 3 not helpful" })).toBeInTheDocument();
    expect(within(row).getByText("Luganda")).toBeInTheDocument();
  });

  it("opens the conversation at the answer that went wrong, beside the last good one", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Went wrong at answer 3/ }));
    const timeline = await screen.findByRole("article", { name: "Conversation timeline" });
    const answers = within(timeline).getAllByRole("listitem");
    expect(within(answers[2]).getByText("Things went wrong here")).toBeInTheDocument();
    expect(within(answers[2]).getByText(/That is wrong/)).toBeInTheDocument();
    expect(within(answers[1]).getByText("Last helpful answer before it")).toBeInTheDocument();
    expect(window.location.search).toContain("conversation=conv-went-wrong");
  });

  it("filters the list by pattern through the API", async () => {
    renderPage();
    await screen.findByRole("button", { name: /Went wrong at answer 3/ });
    const filters = screen.getByRole("group", { name: "Show conversations" });
    fireEvent.click(within(filters).getByRole("button", { name: "Never found helpful" }));
    await waitFor(() =>
      expect(analyticsApi.qualityAiChat).toHaveBeenLastCalledWith(expect.objectContaining({ pattern: "never_helpful" })),
    );
  });

  it("says so when a period has more ratings than one view reads", async () => {
    vi.spyOn(analyticsApi, "qualityAiChat").mockResolvedValue({ summary: { ...SUMMARY, truncated: true }, page: PAGE });
    renderPage();
    expect(await screen.findByText(/cover the most recent ones only/)).toBeInTheDocument();
  });

  it("links into no live queue: the audit reads, it does not work cases", async () => {
    renderPage();
    await screen.findByRole("button", { name: /Went wrong at answer 3/ });
    const hrefs = screen.queryAllByRole("link").map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.filter((h) => /^\/(agent|admin\/tickets|calls)/.test(h))).toEqual([]);
  });
});

describe("audit readings", () => {
  it("compares first answers with follow-ups in words", () => {
    expect(positionReading(SUMMARY.by_position)).toMatch(
      /First answers were found helpful 100% of the time \(5 rated\); follow-ups 42.9% \(7 rated\): 57.1 points lower/,
    );
  });

  it("says when there are no follow-ups to compare", () => {
    expect(positionReading([{ position: "1", rated: 3, helpful: 2, helpful_pct: 66.7 }])).toMatch(/Not enough follow-up/);
  });

  it("reads a rating strip out in order", () => {
    expect(ratingStripLabel([{ answer: 1, rating: "up" }, { answer: 2, rating: "down" }])).toBe(
      "Answer 1 helpful, answer 2 not helpful",
    );
  });
});
