/**
 * WCAG 2.2 AA accessibility evidence.
 *
 * axe is necessary but not sufficient: it catches machine-testable issues on
 * every public and staff surface, while the keyboard tests below cover the
 * dynamic controls axe cannot operate. The manual checklist in
 * docs/ACCESSIBILITY_CONFORMANCE.md covers the remaining human checks.
 */
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { clearChatStore, mockBackend, openSettings, seedConsent, sendMessage } from "./helpers";

const WCAG_22_AA_TAGS = [
  "wcag2a",
  "wcag2aa",
  "wcag21a",
  "wcag21aa",
  "wcag22a",
  "wcag22aa",
];

type Theme = "light" | "dark";

function violationSummary(violations: Awaited<ReturnType<AxeBuilder["analyze"]>>["violations"]) {
  return violations
    .map(
      (violation) =>
        `[${violation.impact}] ${violation.id}: ${violation.description} (${violation.nodes.length} instances)`,
    )
    .join("\n");
}

async function expectNoSeriousOrCritical(page: Page, surface: string, include: string[] = []) {
  let builder = new AxeBuilder({
    page: page as unknown as ConstructorParameters<typeof AxeBuilder>[0]["page"],
  }).withTags(WCAG_22_AA_TAGS);
  for (const selector of include) builder = builder.include(selector);
  const results = await builder.analyze();
  const violations = results.violations.filter(
    (violation) => violation.impact === "critical" || violation.impact === "serious",
  );
  expect(violations, `${surface}\n${violationSummary(violations)}`).toHaveLength(0);
}

/** Set the explicit theme, then reload so every route receives the same tokens. */
async function visitInTheme(page: Page, path: string, theme: Theme) {
  await page.goto(path);
  await page.evaluate((nextTheme) => {
    window.localStorage.setItem("ura-theme", nextTheme);
  }, theme);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

/** A conversation that went wrong at its third answer, as GET /v1/admin/quality/ai-chat returns it. */
function qualityFixture() {
  const now = Math.floor(Date.now() / 1000);
  const summary = {
    period_days: 30, truncated: false, rated_answers: 12, helpful: 8, unhelpful: 4, helpful_pct: 66.7,
    comments: 2, conversations: 5, unlinked_answers: 0,
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
  const page = {
    period_days: 30, truncated: false, total: 1, limit: 25, offset: 0,
    conversations: [{
      conversation_id: "conv-went-wrong", key: "conv-went-wrong", linked: true, pattern: "went_wrong",
      ratings: [{ answer: 1, rating: "up" }, { answer: 2, rating: "up" }, { answer: 3, rating: "down" }],
      rated_answers: 3, unhelpful: 1, break_answer: 3, break_question: "What if I am a sole proprietor?",
      helpful_before_break: true, comments: 1, topic: "vat_registration", locale: "lg",
      first_rated_at: now - 600, last_rated_at: now - 60,
    }],
  };
  const answer = (n: number, rating: string, question: string, reply: string, isBreak = false) => ({
    answer: n, turn_id: `t${n}`, question, reply, created_at: now - 900 + n * 100, sources: ["vat-act.pdf"],
    confidence: 0.7, topic: "vat_registration", rating, comment: isBreak ? "That is wrong." : "",
    retrieval_mode: "hybrid", is_break: isBreak,
  });
  const timeline = {
    conversation_id: "conv-went-wrong", pattern: "went_wrong", break_answer: 3, transcript_complete: true,
    transcript_kept_days: 7,
    answers: [
      answer(1, "up", "How do I register for VAT?", "Apply on the eTax portal."),
      answer(2, "up", "What documents do I need?", "Your TIN and certificate of incorporation."),
      answer(3, "down", "What if I am a sole proprietor?", "Sole proprietors cannot register.", true),
    ],
  };
  return { summary, page, timeline };
}

/** Officer-chat ratings, as GET /v1/admin/quality/ratings returns them: one low, one high. */
function ratingsFixture() {
  const now = Math.floor(Date.now() / 1000);
  const rating = (id: string, score: number, officer: [string, string], extra: Record<string, unknown> = {}) => ({
    id, source: "chat_officer_csat", channel: "chat", subject: "officer", status: "answered", score,
    sentiment: score <= 2 ? "negative" : score === 3 ? "neutral" : "positive", comment: "", ticket_id: `tkt-${id}`,
    call_id: "", conversation_id: `conv-${id}`, officer_id: officer[0], officer_name: officer[1], team: "domestic_taxes",
    topic: "vat", locale: "en", created_at: now - 900, answered_at: now - 600, ...extra,
  });
  return {
    source: "chat_officer_csat",
    period_days: 30,
    truncated: false,
    summary: {
      asked: 3, answered: 2, skipped: 1, no_answer: 0, pending: 0, response_rate: 66.7, average: 3.5, csat_pct: 50, low: 1,
      distribution: { "1": 0, "2": 1, "3": 0, "4": 0, "5": 1 },
      sentiment: { positive: 1, neutral: 0, negative: 1, unclear: 0 },
      by_officer: [
        { officer_id: "u-okello", officer_name: "Officer Okello", handled: 2, rated: 1, average: 2, csat_pct: 0, low: 1 },
        { officer_id: "u-nakato", officer_name: "Officer Nakato", handled: 1, rated: 1, average: 5, csat_pct: 100, low: 0 },
      ],
      by_week: [],
    },
    previous: { asked: 2, average: 4, csat_pct: 50 },
    total: 2,
    limit: 25,
    offset: 0,
    ratings: [
      rating("low", 2, ["u-okello", "Officer Okello"], { comment: "Took too long to answer." }),
      rating("high", 5, ["u-nakato", "Officer Nakato"]),
    ],
  };
}

async function stubQualityAudit(page: Page) {
  const q = qualityFixture();
  await page.route("**/api/v1/admin/quality/ai-chat/*", (route) => route.fulfill({ json: q.timeline }));
  await page.route("**/api/v1/admin/quality/ai-chat?**", (route) =>
    route.fulfill({ json: { summary: q.summary, page: q.page } }),
  );
  await page.route("**/api/v1/admin/quality/ratings?**", (route) => route.fulfill({ json: ratingsFixture() }));
}

/**
 * A minimal but valid operations API for the routes below, signed in as *role*.
 *
 * Officers (`ura_staff`) open the queue and the call desk; administrators open
 * the audit and configuration pages and are refused the live ones. Called a
 * second time on the same page, the later `/v1/me` stub wins (Playwright
 * matches the most recently added route first).
 */
async function prepareStaffSession(page: Page, role = "ura_admin") {
  await page.addInitScript(() => {
    window.localStorage.setItem("ura_auth_token", "e2e-a11y-admin-token");
    window.localStorage.setItem("ura_analytics_consent", "false");
  });
  // StaffGuard opens the live-arrivals WebSocket on every staff route. Keep
  // this test hermetic: a mocked route represents an idle, healthy stream
  // without repeatedly attempting to reach a developer's local API process.
  await page.routeWebSocket("**/api/v1/admin/tickets/stream**", () => {});
  // …and the call layer's lobby socket (the call bar on every staff page).
  await page.routeWebSocket("**/api/v1/admin/calls/stream**", () => {});

  // Register the catch-all first: Playwright gives later routes precedence.
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role,
        email: "accessibility.audit@ura.go.ug",
        external_id: "e2e-a11y-admin",
        tenant_id: "default",
      },
    }),
  );
  await stubQualityAudit(page);
  await page.route("**/api/v1/authority/status", (route) =>
    route.fulfill({
      // The endpoint returns a count, not a list — `sources` was never a key
      // it emits. See the same correction in staff-ui.spec.ts.
      json: {
        ok: true,
        configured: true,
        fresh: true,
        version: "2026-08",
        generated_at: "2026-08-01T00:00:00Z",
        age_days: 1,
        max_age_days: 120,
        sources_checked: 1,
        invalid_sources: [],
        errors: [],
      },
    }),
  );
  await page.route("**/api/v1/admin/tickets/sla**", (route) =>
    route.fulfill({
      json: {
        period_days: 30,
        tickets: 0,
        responded: 0,
        resolved: 0,
        awaiting_first_response: 0,
        median_response_seconds: 0,
        median_resolution_seconds: 0,
        awaiting_next_response: 0,
        unassigned: 0,
        breaching: 0,
      },
    }),
  );
  await page.route("**/api/v1/admin/tickets/stats**", (route) =>
    route.fulfill({
      json: { period_days: 30, total: 0, open: 0, assigned: 0, resolved: 0, wontfix: 0, by_priority: {} },
    }),
  );
  await page.route("**/api/v1/admin/tickets?**", (route) =>
    route.fulfill({ json: { tickets: [], teams: [], total: 0 } }),
  );
  await page.route("**/api/v1/admin/flags", (route) =>
    route.fulfill({
      json: {
        overrides_are_ephemeral: true,
        flags: [
          {
            name: "ticket_queue",
            default: true,
            enabled: true,
            description: "Human oversight queue for escalated conversations.",
            protected: true,
          },
          {
            name: "hyde",
            default: false,
            enabled: true,
            overridden: true,
            description: "Hypothetical document embeddings before retrieval.",
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/admin/overrides", (route) =>
    route.fulfill({
      json: {
        overrides: [
          {
            id: "ovr-1",
            match_query: "What is the VAT registration threshold?",
            reply: "The annual VAT registration threshold is UGX 150 million.",
            created_by: "accessibility.audit@ura.go.ug",
          },
        ],
      },
    }),
  );
  await page.route("**/api/v1/admin/outbox", (route) =>
    route.fulfill({
      json: {
        live: false,
        items: [{ id: "out-000000000001", channel: "email", provider: "mock", status: "queued" }],
      },
    }),
  );
  await page.route("**/api/v1/feedback/summary**", (route) =>
    route.fulfill({
      json: {
        total: 24,
        thumbs_up: 19,
        thumbs_down: 5,
        satisfaction_pct: 79.2,
        recent: [
          {
            id: "fb-1",
            rating: "up",
            comment: "Clear answer about import duty.",
            user_query: "How much duty do I pay on a used car?",
            created_at: 1_760_000_000,
          },
        ],
      },
    }),
  );
  // The charts are the point of auditing /analytics: their axis labels, legends
  // and tooltips were the surfaces carrying hardcoded dark-theme colours, so the
  // stub has to be rich enough for them to render.
  await page.route("**/api/v1/analytics/dashboard**", (route) =>
    route.fulfill({
      json: {
        uptime_seconds: 93_600,
        requests: {
          counters: {
            'retrieval_mode_total{mode="hybrid"}': 812,
            'retrieval_mode_total{mode="keyword"}': 141,
            'retrieval_mode_total{mode="abstained"}': 37,
          },
          latency: {
            "POST|/v1/chat": { p50: 640, p95: 1820, p99: 2450, avg: 810, count: 990 },
            "GET|/v1/health": { p50: 4, p95: 11, p99: 18, avg: 6, count: 4200 },
          },
        },
        chat: { event_counts: {} },
        sessions: { period_days: 30, total_sessions: 412, avg_messages_per_session: 3.4, max_messages_in_session: 22 },
        conversations: {
          period_days: 30,
          total_conversations: 1_402,
          avg_response_time_ms: 812,
          avg_confidence: 0.74,
          top_topics: [
            { tag: "vat", count: 320 },
            { tag: "paye", count: 214 },
            { tag: "customs", count: 168 },
          ],
        },
        feedback: { period_days: 30, total: 24, thumbs_up: 19, thumbs_down: 5, satisfaction_pct: 79.2, recent: [] },
      },
    }),
  );
}

test.describe("WCAG 2.2 AA automated route audit", () => {
  test("has no serious or critical axe violations on every required surface in both themes", async ({ page }) => {
    // Six navigations, a settings dialog and six axe passes. Same reason as the
    // staff audit below: inherently past the 30s default under a parallel run.
    test.slow();
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);

    for (const theme of ["light", "dark"] as const) {
      await visitInTheme(page, "/", theme);
      await expect(page.getByLabel("Type your message")).toBeVisible();
      await expectNoSeriousOrCritical(page, `taxpayer chat (${theme})`);

      await openSettings(page);
      // The sheet fades in. axe resolves rendered colours, so wait for the
      // finished state rather than measuring the intentionally translucent
      // entry animation as if it were the steady UI.
      await expect(page.locator(".setv2")).toHaveCSS("opacity", "1");
      await expectNoSeriousOrCritical(page, `settings dialog (${theme})`);
      await page.keyboard.press("Escape");

      await visitInTheme(page, "/signin", theme);
      await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
      await expectNoSeriousOrCritical(page, `sign-in (${theme})`);

      await visitInTheme(page, "/signup", theme);
      await expect(page.getByRole("heading", { name: "Create an account", exact: true })).toBeVisible();
      await expectNoSeriousOrCritical(page, `sign-up (${theme})`);
    }
  });

  test("has no serious or critical axe violations on every required staff surface in both themes", async ({ page }) => {
    // Eighteen navigations and eighteen axe passes: nine console routes in two
    // themes. That is inherently past the 30s default, and was already close to
    // it at four routes.
    test.slow();

    // Every console route, including the two that used to sit outside it.
    // /analytics in particular was never audited: it was not behind StaffGuard,
    // so this suite's staff session did not reach it. Each is visited as the
    // role that opens it: administrators audit and configure, officers work
    // the queue and the call desk.
    const routesByRole = {
      ura_admin: [
        ["/admin", "Operations overview"],
        ["/admin/quality", "Service quality audit"],
        ["/admin/quality?tab=officer-chat", "Service quality audit"],
        ["/admin/flags", "Feature flags"],
        ["/admin/overrides", "Answer overrides"],
        ["/admin/outbox", "Notification outbox"],
        ["/analytics", "Analytics Dashboard"],
        ["/analytics/evaluation", "Answer evaluation"],
      ],
      ura_staff: [
        ["/agent", "My queue"],
        ["/admin/tickets", "Escalation queue"],
        ["/calls", "Phone Calls"],
      ],
    } as const;

    for (const [role, routes] of Object.entries(routesByRole)) {
      await prepareStaffSession(page, role);
      for (const theme of ["light", "dark"] as const) {
        for (const [path, heading] of routes) {
          await visitInTheme(page, path, theme);
          await expect(page.getByRole("heading", { name: heading })).toBeVisible();
          await expectNoSeriousOrCritical(page, `${path} as ${role} (${theme})`);
        }
      }
    }
  });
});

test.describe("live chat with an officer", () => {
  test("the officer's messages, the notices and the rating dialog pass WCAG 2.2 AA, and the faces are keyboard-operable", async ({ page }) => {
    const ticket = "abc12345-6789-4def-8123-456789abcdef";
    let phase: "officer" | "ended" = "officer";
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page, { escalate: true, reply: "This requires human review." });
    await page.route("**/api/v1/escalate", (route) =>
      route.fulfill({ json: { ok: true, ticket_id: ticket, status: "open", message: "A URA officer has been asked to look at this." } }),
    );
    await page.route(`**/api/v1/escalate/${ticket}/messages**`, (route) =>
      route.fulfill({
        json: {
          ticket_id: ticket,
          chat_state: phase,
          officer_name: "Officer Nakato",
          messages: [{ seq: 1, sender: "officer", text: "Which period?", officer_name: "Officer Nakato", created_at: Date.now() / 1000 }],
          last_seq: 1,
          csat: { requested: phase === "ended", submitted: false },
        },
      }),
    );
    await page.route(`**/api/v1/escalate/${ticket}/csat`, (route) => route.fulfill({ json: { ok: true, status: "answered" } }));
    await page.goto("/");
    await sendMessage(page, "I was assessed VAT twice");
    await page.getByTestId("handoff-request").click();
    await expect(page.locator(".message-row-officer")).toBeVisible({ timeout: 15_000 });
    // Scoped to what the live chat adds: the officer's message, the notice and
    // the banner. The rest of the chat page has its own route audit.
    await expectNoSeriousOrCritical(page, "chat with an officer", [".message-row-officer", ".chat-notice", ".chat-case-banner"]);

    phase = "ended";
    const dialog = page.getByRole("dialog", { name: "How was your chat with Officer Nakato?" });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    // Focus starts on the scale; the arrows choose without a pointer.
    await expect(dialog.getByRole("radio", { name: "3 of 5, Neutral" })).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await expect(dialog.getByRole("radio", { name: "4 of 5, Satisfied" })).toBeChecked();
    await expectNoSeriousOrCritical(page, "officer chat rating dialog", [".service-rating-dialog"]);
  });
});

test.describe("keyboard and focus regression checks", () => {
  test("connector picker supports keyboard selection, restores focus, and passes WCAG 2.2 AA", async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
    await page.route(
      (url) => url.pathname === "/api/v1/connectors" && url.searchParams.get("view") === "chat",
      (route) => route.fulfill({
        json: {
          ok: true,
          enabled: true,
          connectors: [
            {
              namespace: "efris",
              label: "EFRIS",
              description: "Demo data only — not connected to live URA systems. Reads a sample taxpayer profile.",
              summary: "Reads a sample taxpayer profile.",
              mode: "simulation",
              status: "available",
              operation_count: 2,
              operations: [
                { name: "efris_taxpayer_status", title: "Taxpayer status", description: "Reads a profile." },
                { name: "efris_invoice_status", title: "Invoice status", description: "Reads an invoice." },
              ],
              read_only: true,
            },
            {
              namespace: "ursb",
              label: "URSB",
              description: "Demo data only — not connected to live URA systems. Searches sample businesses.",
              summary: "Searches sample businesses.",
              mode: "simulation",
              status: "unavailable",
              operation_count: 1,
              operations: [{ name: "ursb_verify_business", title: "Verify a business", description: "" }],
              read_only: true,
            },
          ],
        },
      }),
    );
    await page.goto("/");

    const trigger = page.getByTestId("composer-add-btn");
    await trigger.click();
    await expect(page.getByRole("menuitem", { name: /Upload a file or screenshot/ })).toBeFocused();
    await expectNoSeriousOrCritical(page, "add menu");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog", { name: "URA services" });
    await expect(dialog).toBeVisible();
    // What a person needs before choosing: demo or live, answering or not.
    await expect(dialog.getByText("Demo data").first()).toBeVisible();
    await expect(dialog.getByRole("button", { name: /URSB/ })).toBeDisabled();
    await expectNoSeriousOrCritical(page, "connector picker");
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();

    await trigger.click();
    await expect(page.getByRole("menuitem", { name: /Upload a file or screenshot/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(dialog).toBeVisible();
    const connectorOption = dialog.getByRole("button", { name: /EFRIS/ });
    await connectorOption.click();
    await expect(connectorOption).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: "Remove EFRIS from this chat" })).toBeVisible();
    await page.keyboard.press("Escape");
  });

  test("the add menu stays above an existing conversation", async ({ page }) => {
    // Found in the live UI: the menu used to be an overlay inside the composer,
    // and once the transcript had messages their text and buttons painted
    // over it. It is a native <dialog> in the browser's top layer now.
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", { timeout: 15_000 });

    await page.getByTestId("composer-add-btn").click();
    const menu = page.locator("dialog.addmenu-dialog");
    await expect(menu).toBeVisible();

    const box = await menu.boundingBox();
    expect(box).not.toBeNull();
    // Every row's centre is hit-tested to the menu itself, not to the transcript.
    for (const row of await menu.getByRole("menuitem").all()) {
      const rowBox = await row.boundingBox();
      expect(rowBox).not.toBeNull();
      const owner = await page.evaluate(
        ({ x, y }) => document.elementFromPoint(x, y)?.closest("dialog.addmenu-dialog") !== null,
        { x: rowBox!.x + rowBox!.width / 2, y: rowBox!.y + rowBox!.height / 2 },
      );
      expect(owner).toBe(true);
    }
    await page.keyboard.press("Escape");
    await expect(menu).not.toBeVisible();
    await expect(page.getByTestId("composer-add-btn")).toBeFocused();
  });

  test("header menu follows the menu-button keyboard pattern and restores focus", async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
    await page.goto("/");

    const trigger = page.getByRole("button", { name: "More options" });
    await trigger.click();
    await expect(page.getByRole("menuitem", { name: /^Theme:/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("menuitem", { name: "Settings" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
  });

  test("composer controls and staff tabs are reachable without a pointer", async ({ page }) => {
    await prepareStaffSession(page, "ura_staff");
    await page.goto("/");
    await expect(page.getByLabel("Type your message")).toBeVisible();
    await expect(page.getByLabel("Start speaking")).toBeVisible();
    await expect(page.getByLabel("Enter voice mode")).toBeVisible();

    await page.goto("/agent");
    const nextUp = page.getByRole("tab", { name: "Next up" });
    await nextUp.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Mine" })).toBeFocused();
    await expect(page.getByRole("tab", { name: "Mine" })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: "Resolved" })).toBeFocused();
    await expect(page.locator("body")).not.toBeFocused();
  });
});
