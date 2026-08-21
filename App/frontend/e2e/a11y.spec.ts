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
  await page.route("**/api/v1/admin/flags", (route) => route.fulfill({ json: { flags: [] } }));
}

test.describe("WCAG 2.2 AA automated route audit", () => {
  test("has no serious or critical axe violations on every required surface in both themes", async ({ page }) => {
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
    await prepareStaffSession(page);

    const routes = [
      ["/admin", "Operations overview"],
      ["/agent", "My queue"],
      ["/admin/tickets", "Escalation queue"],
      ["/admin/flags", "Feature flags"],
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

    // Language moved into this menu when the navbar was removed, so it is the
    // first item now. What is under test is the pattern, not the ordering:
    // opening focuses the first item, Arrow keys move, Enter activates.
    const trigger = page.getByRole("button", { name: "More options" });
    await trigger.click();
    await expect(page.getByRole("menuitem", { name: /^Response language:/ })).toBeFocused();
    await page.keyboard.press("ArrowDown");
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
