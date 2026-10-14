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
import { distributionReading } from "../../components/quality/SatisfactionDistribution";
import { combineAiCalls, momentReading } from "../../components/quality/AiCallsPanel";
import {
  analyticsApi,
  type AiChatPage,
  type AiChatSummary,
  type AiChatTimeline,
  type NeedsReviewItem,
  type NeedsReviewPage,
  type RatingsAudit,
  type ServiceRating,
  type TicketDetail,
} from "../../services/analyticsApi";
import { callsApi, type CallRecord } from "../../services/callsApi";

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

const RATINGS: RatingsAudit = {
  source: "chat_officer_csat",
  period_days: 30,
  truncated: false,
  summary: {
    asked: 6,
    answered: 4,
    skipped: 1,
    no_answer: 0,
    pending: 1,
    response_rate: 66.7,
    average: 3.25,
    csat_pct: 50,
    low: 1,
    distribution: { "1": 0, "2": 1, "3": 1, "4": 1, "5": 1 },
    sentiment: { positive: 2, neutral: 1, negative: 1, unclear: 0 },
    positive_pct: 50,
    negative_pct: 25,
    by_officer: [
      { officer_id: "off-b", officer_name: "Officer Okello", handled: 3, rated: 2, average: 2.5, csat_pct: 0, low: 1 },
      { officer_id: "off-a", officer_name: "Officer Nakato", handled: 3, rated: 2, average: 4.5, csat_pct: 100, low: 0 },
    ],
    by_week: [],
    by_language: [],
  },
  previous: { asked: 4, answered: 4, response_rate: 100, average: 4, csat_pct: 75, positive_pct: 75, negative_pct: 0 },
  total: 2,
  limit: 25,
  offset: 0,
  ratings: [
    {
      id: "r-low", source: "chat_officer_csat", channel: "chat", subject: "officer", status: "answered", score: 2,
      sentiment: "negative", comment: "Took too long to answer.", ticket_id: "abc12345-0000", call_id: "",
      conversation_id: "conv-x", officer_id: "off-b", officer_name: "Officer Okello", team: "domestic_taxes",
      topic: "vat", locale: "en", created_at: NOW - 600, answered_at: NOW - 500,
    },
    {
      id: "r-high", source: "chat_officer_csat", channel: "chat", subject: "officer", status: "answered", score: 5,
      sentiment: "positive", comment: "", ticket_id: "def67890-0000", call_id: "", conversation_id: "conv-y",
      officer_id: "off-a", officer_name: "Officer Nakato", team: "customs", topic: "", locale: "lg",
      created_at: NOW - 400, answered_at: NOW - 300,
    },
  ],
};

const CASE: TicketDetail = {
  id: "abc12345-0000",
  status: "resolved",
  priority: "normal",
  reason: "VAT assessed twice",
  team: "domestic_taxes",
  created_at: NOW - 3600,
  locale: "en",
  modality: "text",
  transcript: [],
  messages: [
    { seq: 1, sender: "officer", text: "Which period?", officer_name: "Officer Okello", created_at: NOW - 900 },
    { seq: 2, sender: "taxpayer", text: "March 2026.", created_at: NOW - 800 },
    { seq: 3, sender: "system", text: "chat_ended", created_at: NOW - 700 },
  ],
  chat: {
    state: "ended",
    officer_name: "Officer Okello",
    ended_at: NOW - 700,
    ended_by_name: "Officer Okello",
    outcome: "resolved",
    rating: { status: "answered", score: 2, comment: "Took too long to answer." },
  },
} as unknown as TicketDetail;

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

describe("Officer chats tab", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/quality?tab=officer-chat");
    vi.spyOn(analyticsApi, "qualityAiChat").mockResolvedValue({ summary: SUMMARY, page: PAGE });
    vi.spyOn(analyticsApi, "qualityRatings").mockResolvedValue(RATINGS);
    vi.spyOn(analyticsApi, "ticket").mockResolvedValue(CASE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens on the tab the link names, as a real tab", async () => {
    renderPage();
    const tab = screen.getByRole("tab", { name: "Officer chats" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tab.id);
    fireEvent.keyDown(tab, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "AI on calls" })).toHaveAttribute("aria-selected", "true");
    expect(window.location.search).toContain("tab=ai-calls");
    fireEvent.keyDown(screen.getByRole("tab", { name: "AI on calls" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "AI answers · chat" })).toHaveAttribute("aria-selected", "true");
    expect(window.location.search).not.toContain("tab=");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "AI answers · chat",
      "AI on calls",
      "Officer chats",
      "Officer calls",
      "Needs review",
    ]);
  });

  it("leads with the average, the satisfied share, the response rate and the low ratings", async () => {
    renderPage();
    const stats = await screen.findByLabelText("Officer chat satisfaction summary");
    await waitFor(() => expect(within(stats).getByText("Average satisfaction")).toBeInTheDocument());
    expect(stats.textContent).toContain("3.25/5");
    expect(stats.textContent).toMatch(/down 0.75 from 4 in the period before/);
    expect(stats.textContent).toContain("4 of 6 asked · 1 skipped · 1 not answered yet");
    expect(stats.textContent).toContain("Low ratings (1 or 2)");
  });

  it("shows each level with its face and words, and the officers lowest first", async () => {
    renderPage();
    const levels = await screen.findByRole("list", { name: "How satisfied taxpayers were with their chat" });
    expect(within(levels).getByText(/Very satisfied/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "By officer" })).toBeInTheDocument();
    const names = screen.getAllByRole("button", { name: /^Officer (Okello|Nakato)$/ }).map((b) => b.textContent);
    expect(names).toEqual(["Officer Okello", "Officer Nakato"]);
  });

  it("filters the ratings by officer and by level through the API", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Officer Okello" }));
    await waitFor(() =>
      expect(analyticsApi.qualityRatings).toHaveBeenLastCalledWith(expect.objectContaining({ officer: "off-b" })),
    );
    fireEvent.click(screen.getByRole("button", { name: "2 of 5, Dissatisfied" }));
    await waitFor(() =>
      expect(analyticsApi.qualityRatings).toHaveBeenLastCalledWith(expect.objectContaining({ score: 2, officer: "off-b" })),
    );
  });

  it("opens a rated chat read-only, with how it ended and how it was rated", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /2\/5 · Dissatisfied/ }));
    const chat = await screen.findByRole("article", { name: "Officer chat" });
    await waitFor(() => expect(within(chat).getByText("March 2026.")).toBeInTheDocument());
    expect(within(chat).getByText(/Chat ended by Officer Okello · resolved/)).toBeInTheDocument();
    expect(within(chat).getByText(/Taxpayer rated the chat 2\/5 · Dissatisfied/)).toBeInTheDocument();
    expect(within(chat).queryByRole("textbox")).toBeNull();
    expect(window.location.search).toContain("ticket=abc12345-0000");
    expect(analyticsApi.ticket).toHaveBeenCalledTimes(1);
  });
});

function spoken(source: RatingsAudit["source"], counts: RatingsAudit["summary"]["sentiment"], ratings: ServiceRating[], extra: Partial<RatingsAudit["summary"]> = {}): RatingsAudit {
  const answered = counts.positive + counts.neutral + counts.negative + counts.unclear;
  const share = (n: number) => (answered ? Math.round((n / answered) * 1000) / 10 : null);
  return {
    ...RATINGS,
    source,
    summary: {
      ...RATINGS.summary,
      asked: answered + 1,
      answered,
      skipped: 0,
      no_answer: 1,
      pending: 0,
      response_rate: share(answered),
      average: null,
      csat_pct: null,
      low: 0,
      distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 },
      sentiment: counts,
      positive_pct: share(counts.positive),
      negative_pct: share(counts.negative),
      by_officer: [],
      by_week: [{ week: "2026-W41", rated: answered, average: null, csat_pct: null, positive_pct: share(counts.positive), negative_pct: share(counts.negative) }],
      by_language: [{ locale: "lg", asked: answered + 1, response_rate: share(answered), rated: answered, average: null, csat_pct: null, positive_pct: share(counts.positive), negative_pct: share(counts.negative) }],
      ...extra,
    },
    previous: { asked: 4, answered: 4, response_rate: 100, average: null, csat_pct: null, positive_pct: 75, negative_pct: 25 },
    total: ratings.length,
    ratings,
  };
}

function said(id: string, source: ServiceRating["source"], sentiment: string, comment: string): ServiceRating {
  return {
    ...RATINGS.ratings[0],
    id,
    source,
    channel: "call",
    subject: "ai",
    status: "answered",
    score: null,
    sentiment,
    comment,
    ticket_id: "",
    call_id: `call-${id}`,
    officer_id: "",
    officer_name: "",
    locale: "lg",
  };
}

const AI_END = spoken("call_ai_review_end", { positive: 6, neutral: 1, negative: 1, unclear: 0 }, [
  said("e1", "call_ai_review_end", "positive", "Yes, very helpful"),
  said("e2", "call_ai_review_end", "negative", "It didn't help me"),
]);
const AI_TRANSFER = spoken("call_ai_review_transfer", { positive: 1, neutral: 1, negative: 2, unclear: 0 }, [
  said("t1", "call_ai_review_transfer", "neutral", "A bit"),
  said("t2", "call_ai_review_transfer", "negative", "No, I wanted a person"),
]);

describe("AI on calls tab", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/quality?tab=ai-calls");
    vi.spyOn(analyticsApi, "qualityRatings").mockImplementation(async (q) =>
      q?.source === "call_ai_review_transfer" ? AI_TRANSFER : AI_END,
    );
    vi.spyOn(callsApi, "getCall");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads both moments, and their general opinion, not call by call", async () => {
    renderPage();
    expect(screen.getByRole("tab", { name: "AI on calls" })).toHaveAttribute("aria-selected", "true");
    const stats = await screen.findByLabelText("Callers' review of the AI summary");
    await waitFor(() => expect(within(stats).getByText("Said the AI helped")).toBeInTheDocument());
    // 7 of the 12 who answered said it helped.
    expect(stats.textContent).toContain("58.3%");
    expect(stats.textContent).toContain("Callers asked");
    expect(analyticsApi.qualityRatings).toHaveBeenCalledWith(expect.objectContaining({ source: "call_ai_review_end", status: "answered" }));
    expect(analyticsApi.qualityRatings).toHaveBeenCalledWith(expect.objectContaining({ source: "call_ai_review_transfer" }));
    const moments = screen.getByRole("list", { name: "What callers said, by when they were asked" });
    expect(within(moments).getByText(/At the end of the call/)).toBeInTheDocument();
    expect(within(moments).getByText(/While being transferred to an officer/)).toBeInTheDocument();
  });

  it("lists what callers said, the unhappy answers first, with no call to open", async () => {
    renderPage();
    const list = await screen.findByRole("heading", { name: "What callers said" });
    const panel = list.closest("section") as HTMLElement;
    await waitFor(() => expect(within(panel).getAllByRole("listitem").length).toBe(3));
    const items = within(panel).getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toMatch(/Said it didn't help/);
    expect(items.join(" ")).not.toContain("Yes, very helpful");
    expect(within(panel).queryAllByRole("button", { name: /didn't help me/ })).toHaveLength(0);
    fireEvent.click(within(panel).getByRole("button", { name: "Said it helped" }));
    expect(within(panel).getAllByRole("listitem").map((li) => li.textContent).join(" ")).toContain("Yes, very helpful");
    expect(callsApi.getCall).not.toHaveBeenCalled();
  });
});

const CALL_RATINGS: RatingsAudit = {
  ...RATINGS,
  source: "call_officer_csat",
  summary: { ...RATINGS.summary, skipped: 0, pending: 0, no_answer: 2 },
  ratings: [
    { ...RATINGS.ratings[0], id: "c-low", source: "call_officer_csat", channel: "call", ticket_id: "", call_id: "call_abc12345", comment: "" },
  ],
  total: 1,
};

describe("Officer calls tab", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/quality?tab=officer-calls");
    vi.spyOn(analyticsApi, "qualityRatings").mockResolvedValue(CALL_RATINGS);
    vi.spyOn(callsApi, "getCall").mockResolvedValue({
      call_id: "call_abc12345",
      conversation_id: "conv-c",
      locale: "lg",
      status: "ended",
      started_at: NOW - 900,
      outcome: "resolved",
      wrapup_note: "Explained the refund timeline.",
      transferred: true,
      turns: [
        { id: "t1", call_id: "call_abc12345", seq: 1, speaker: "caller", kind: "utterance", text: "Where is my refund?", low_conf_words: [], mean_word_prob: 0.9, faithfulness: null, latencies: {}, created_at: NOW - 880 },
        { id: "t2", call_id: "call_abc12345", seq: 2, speaker: "officer", kind: "utterance", text: "It is being processed.", low_conf_words: [], mean_word_prob: null, faithfulness: null, latencies: {}, created_at: NOW - 860 },
      ],
    } as unknown as CallRecord);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads the satisfaction callers gave the officer, in the call's own words", async () => {
    renderPage();
    const stats = await screen.findByLabelText("Officer call satisfaction summary");
    await waitFor(() => expect(within(stats).getByText("Caller gave an answer")).toBeInTheDocument());
    expect(stats.textContent).toContain("2 didn't say");
    expect(analyticsApi.qualityRatings).toHaveBeenCalledWith(expect.objectContaining({ source: "call_officer_csat" }));
    expect(screen.getByRole("heading", { name: "Rated calls" })).toBeInTheDocument();
  });

  it("opens the call read-only with the caller's satisfaction and the wrap-up", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /2\/5 · Dissatisfied/ }));
    const call = await screen.findByRole("article", { name: "Officer call" });
    await waitFor(() => expect(within(call).getByText("Where is my refund?")).toBeInTheDocument());
    expect(within(call).getByText(/Caller satisfaction: 2\/5 · Dissatisfied/)).toBeInTheDocument();
    expect(within(call).getByText(/Explained the refund timeline/)).toBeInTheDocument();
    expect(within(call).queryByRole("textbox")).toBeNull();
    expect(window.location.search).toContain("call=call_abc12345");
    expect(callsApi.getCall).toHaveBeenCalledTimes(1);
  });
});

const QUEUE_ITEMS: NeedsReviewItem[] = [
  {
    item_type: "ai_chat", item_id: "conv-went-wrong", stream: "ai_chat", title: "AI chat went wrong at answer 3",
    quote: "What if I am a sole proprietor?", comment: "That is wrong.", topic: "vat_registration", locale: "lg",
    officer_name: "", occurred_at: NOW - 60, link: { tab: "ai-chat", param: "conversation", value: "conv-went-wrong" },
    review: null,
  },
  {
    item_type: "rating", item_id: "r-low", stream: "officer_chat", title: "Officer chat rated 2/5 — Officer Okello",
    quote: "", comment: "Took too long to answer.", topic: "vat", locale: "en", officer_name: "Officer Okello",
    occurred_at: NOW - 600, link: { tab: "officer-chat", param: "ticket", value: "abc12345-0000" },
    review: { status: "action_needed", note: "Coach on refunds", reviewer_name: "Auditor A", updated_at: NOW - 30 },
  },
];

function queuePage(items: NeedsReviewItem[]): NeedsReviewPage {
  return {
    period_days: 30,
    truncated: false,
    counts: { open: 1, action_needed: 1, reviewed: 0, all: 2 },
    total: items.length,
    limit: 25,
    offset: 0,
    items,
  };
}

describe("Needs review tab", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/admin/quality?tab=needs-review");
    vi.spyOn(analyticsApi, "qualityNeedsReview").mockResolvedValue(queuePage(QUEUE_ITEMS));
    vi.spyOn(analyticsApi, "setQualityReview").mockResolvedValue({
      item_type: "ai_chat",
      item_id: "conv-went-wrong",
      review: { status: "reviewed", note: "", reviewer_name: "admin@ura.go.ug", updated_at: NOW },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists what is waiting with where to read it, and the counts per status", async () => {
    renderPage();
    expect(screen.getByRole("tab", { name: "Needs review" })).toHaveAttribute("aria-selected", "true");
    const list = await screen.findByRole("list", { name: "Needs review" });
    const items = within(list).getAllByRole("listitem");
    expect(items[0].textContent).toContain("Asked: What if I am a sole proprietor?");
    expect(items[0].textContent).toContain("Said: That is wrong.");
    expect(within(items[0]).getByRole("link", { name: /Open/ })).toHaveAttribute("href", "/admin/quality?conversation=conv-went-wrong");
    expect(within(items[1]).getByRole("link", { name: /Open/ })).toHaveAttribute(
      "href",
      "/admin/quality?tab=officer-chat&ticket=abc12345-0000",
    );
    // Who decided, and what.
    expect(items[1].textContent).toMatch(/Action needed by Auditor A/);
    expect(items[1].textContent).toContain("Coach on refunds");
    const filters = screen.getByRole("group", { name: "Show items" });
    expect(within(filters).getByRole("button", { name: /Open/ }).textContent).toContain("1");
    expect(analyticsApi.qualityNeedsReview).toHaveBeenCalledWith(expect.objectContaining({ status: "open" }));
  });

  it("filters by status and by stream through the API", async () => {
    renderPage();
    await screen.findByRole("list", { name: "Needs review" });
    fireEvent.click(within(screen.getByRole("group", { name: "Show items" })).getByRole("button", { name: /Action needed/ }));
    await waitFor(() =>
      expect(analyticsApi.qualityNeedsReview).toHaveBeenLastCalledWith(expect.objectContaining({ status: "action_needed" })),
    );
    fireEvent.click(within(screen.getByRole("group", { name: "From" })).getByRole("button", { name: "Officer call" }));
    await waitFor(() =>
      expect(analyticsApi.qualityNeedsReview).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "action_needed", stream: "officer_call" }),
      ),
    );
  });

  it("records a decision with its note, and reopens one", async () => {
    renderPage();
    const decision = await screen.findByRole("group", { name: "Decision on: AI chat went wrong at answer 3" });
    fireEvent.click(within(decision).getByRole("button", { name: "Add a note" }));
    fireEvent.change(within(decision).getByLabelText("Note"), { target: { value: "  Sole proprietors can register  " } });
    fireEvent.click(within(decision).getByRole("button", { name: "Action needed" }));
    await waitFor(() =>
      expect(analyticsApi.setQualityReview).toHaveBeenCalledWith(
        "ai_chat",
        "conv-went-wrong",
        "action_needed",
        "Sole proprietors can register",
      ),
    );
    const decided = screen.getByRole("group", { name: "Decision on: Officer chat rated 2/5 — Officer Okello" });
    expect(within(decided).getByRole("button", { name: "Action needed" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(decided).getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(analyticsApi.setQualityReview).toHaveBeenLastCalledWith("rating", "r-low", "open", ""));
  });

  it("says when a decision did not save", async () => {
    vi.spyOn(analyticsApi, "setQualityReview").mockRejectedValue(new Error("403 Forbidden"));
    renderPage();
    const decision = await screen.findByRole("group", { name: "Decision on: AI chat went wrong at answer 3" });
    fireEvent.click(within(decision).getByRole("button", { name: "Mark reviewed" }));
    expect(await within(decision).findByRole("alert")).toHaveTextContent(/Could not save the decision/);
  });

  it("exports the list as it stands", async () => {
    const create = vi.fn(() => "blob:queue");
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderPage();
    await screen.findByRole("list", { name: "Needs review" });
    fireEvent.click(screen.getByRole("button", { name: "Export 2 as CSV" }));
    expect(click).toHaveBeenCalledTimes(1);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("says so when nothing is waiting", async () => {
    vi.spyOn(analyticsApi, "qualityNeedsReview").mockResolvedValue(queuePage([]));
    renderPage();
    expect(await screen.findByText("Nothing waiting for a review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export 0 as CSV" })).toBeDisabled();
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

  it("reads the satisfaction levels out in words", () => {
    expect(distributionReading(RATINGS.summary)).toBe("2 of 4 rated were satisfied or very satisfied; 1 (25%) were dissatisfied — enough to read each one.");
    expect(distributionReading({ ...RATINGS.summary, distribution: { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0 } })).toMatch(/Nothing has been rated/);
  });

  it("compares the two moments callers are asked at, in words", () => {
    const total = combineAiCalls([AI_END, AI_TRANSFER]);
    expect([total.asked, total.answered, total.positivePct, total.negativePct]).toEqual([14, 12, 58.3, 25]);
    expect(
      momentReading([
        { label: "end", answered: 8, positivePct: 75 },
        { label: "transfer", answered: 4, positivePct: 25 },
      ]),
    ).toMatch(/75% of the time at the end of a call \(8 answered\) and 25% while being transferred \(4\): 50 points higher/);
    expect(momentReading([])).toMatch(/No caller has answered/);
  });

  it("reads a rating strip out in order", () => {
    expect(ratingStripLabel([{ answer: 1, rating: "up" }, { answer: 2, rating: "down" }])).toBe(
      "Answer 1 helpful, answer 2 not helpful",
    );
  });
});
