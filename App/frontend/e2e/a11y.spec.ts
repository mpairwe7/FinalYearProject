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

import { clearChatStore, mockBackend, openSettings, seedConsent } from "./helpers";

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

async function expectNoSeriousOrCritical(page: Page, surface: string) {
  const results = await new AxeBuilder({ page }).withTags(WCAG_22_AA_TAGS).analyze();
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

/** A minimal but valid operations API for the routes below. */
async function prepareStaffSession(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem("ura_auth_token", "e2e-a11y-admin-token");
    window.localStorage.setItem("ura_analytics_consent", "false");
  });
  // StaffGuard opens the live-arrivals WebSocket on every staff route. Keep
  // this test hermetic: a mocked route represents an idle, healthy stream
  // without repeatedly attempting to reach a developer's local API process.
  await page.routeWebSocket("**/api/v1/admin/tickets/stream**", () => {});

  // Register the catch-all first: Playwright gives later routes precedence.
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role: "ura_admin",
        email: "accessibility.audit@ura.go.ug",
        external_id: "e2e-a11y-admin",
        tenant_id: "default",
      },
    }),
  );
  await page.route("**/api/v1/authority/status", (route) =>
    route.fulfill({
      json: {
        fresh: true,
        version: "2026-08",
        age_days: 1,
        max_age_days: 120,
        sources: ["rates.pdf"],
      },
    }),
  );
  await page.route("**/api/v1/admin/tickets/sla**", (route) =>
    route.fulfill({
      json: {
        tickets: 0,
        responded: 0,
        resolved: 0,
        awaiting_first_response: 0,
        median_response_seconds: 0,
        median_resolution_seconds: 0,
        awaiting_next_response: 0,
        breaching: 0,
      },
    }),
  );
  await page.route("**/api/v1/admin/tickets/stats**", (route) =>
    route.fulfill({ json: { total: 0, open: 0, assigned: 0, resolved: 0, by_priority: {}, by_team: {} } }),
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
    }
  });

  test("has no serious or critical axe violations on every required staff surface in both themes", async ({ page }) => {
    // Sixteen navigations and sixteen axe passes: eight console routes in two
    // themes. That is inherently past the 30s default, and was already close to
    // it at four routes.
    test.slow();
    await prepareStaffSession(page);

    // Every console route, including the two that used to sit outside it.
    // /analytics in particular was never audited: it was not behind StaffGuard,
    // so this suite's staff session did not reach it.
    const routes = [
      ["/admin", "Operations overview"],
      ["/agent", "My queue"],
      ["/admin/tickets", "Escalation queue"],
      ["/admin/flags", "Feature flags"],
      ["/admin/overrides", "Answer overrides"],
      ["/admin/outbox", "Notification outbox"],
      ["/analytics", "Analytics Dashboard"],
      ["/analytics/evaluation", "Answer evaluation"],
    ] as const;

    for (const theme of ["light", "dark"] as const) {
      for (const [path, heading] of routes) {
        await visitInTheme(page, path, theme);
        await expect(page.getByRole("heading", { name: heading })).toBeVisible();
        await expectNoSeriousOrCritical(page, `${path} (${theme})`);
      }
    }
  });
});

test.describe("keyboard and focus regression checks", () => {
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
    await prepareStaffSession(page);
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
