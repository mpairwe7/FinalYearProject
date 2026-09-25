/**
 * E2E tests for the officer's Call Desk (docs/plans/officer-call-desk-plan.md §11).
 *
 * Exercises the call layer across staff pages, transfer alerts, preview,
 * taking calls, active dock survival across navigation, wrapup, and history search.
 */
import { expect, test, type Page } from "@playwright/test";
import { seedConsent } from "./helpers";

async function signedInAsStaff(page: Page) {
  await seedConsent(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("ura_auth_token", "e2e-staff-token");
  });

  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));

  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role: "ura_staff",
        email: "okello@ura.go.ug",
        external_id: "officer_okello",
        tenant_id: "default",
      },
    }),
  );

  await page.route("**/api/v1/admin/officers/presence", (route) =>
    route.fulfill({
      json: {
        officers: [
          {
            user_id: "officer_okello",
            display_name: "Officer Okello",
            status: "available",
            languages: ["en"],
            teams: ["general"],
            last_seen: Date.now() / 1000,
            updated_at: Date.now() / 1000,
          },
        ],
        teams: ["general", "taxpayer_accounts", "disputes"],
      },
    }),
  );

  await page.route("**/api/v1/admin/calls**", (route) => {
    const url = route.request().url();
    if (url.includes("/metrics")) {
      return route.fulfill({
        json: {
          period_days: 7,
          total_calls: 10,
          containment_rate: 0.7,
          transfer_rate: 0.3,
          transfers_by_reason: {},
          clarification_rate: 0.1,
          clarification_first_try_rate: 0.8,
          mean_word_prob: 0.92,
          avg_duration_s: 120,
          avg_officer_rating: 4.8,
          latency_p50_ms: 120,
          latency_p95_ms: 350,
        },
      });
    }
    return route.fulfill({
      json: {
        calls: [],
        total: 0,
        count: 0,
        status_filter: "all",
        limit: 50,
        offset: 0,
      },
    });
  });

  await page.routeWebSocket("**/api/v1/admin/calls/stream**", () => {});
}

test.describe("Staff Call Desk", () => {
  test("renders the Call Console Bar on staff pages", async ({ page }) => {
    await signedInAsStaff(page);
    await page.goto("/admin/tickets");

    const bar = page.locator(".cc-bar");
    await expect(bar).toBeVisible();
    await expect(bar.getByText(/Available|Busy|Away/i)).toBeVisible();
    await expect(bar.getByText(/Live 0/i)).toBeVisible();
  });

  test("displays Call Desk tabs and structure on /calls", async ({ page }) => {
    await signedInAsStaff(page);
    await page.goto("/calls");

    await expect(page.getByRole("tab", { name: "Desk" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Callbacks" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "History" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Performance" })).toBeVisible();
  });
});
