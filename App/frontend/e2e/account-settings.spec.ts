/**
 * Account entry points and the settings dialog, in a browser.
 *
 * The unit tests already cover that each control writes the right store. What
 * needs a real engine is the wiring around them: that both auth routes are
 * reachable from the assistant, that a settings change is visible in the header
 * that reads the same state, that the dialog contains focus and closes, and that
 * a signed-in session replaces the call to action instead of sitting beside it.
 *
 * No backend: `/api/**` is intercepted, same as the other browser specs.
 */
import { expect, test, type Page } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

async function anonymous(page: Page) {
  await seedConsent(page);
  await clearChatStore(page);
  await mockBackend(page);
}

/** A token plus a `/v1/me` that accepts it — both are needed to read as signed in. */
async function signedIn(page: Page, role = "verified_taxpayer") {
  await anonymous(page);
  await page.addInitScript(() => {
    window.localStorage.setItem("ura_auth_token", "e2e-token");
  });
  await page.route("**/api/v1/me", (route) =>
    route.fulfill({
      json: {
        authenticated: true,
        role,
        email: `${role}@example.ug`,
        external_id: `ext-${role}`,
        tenant_id: "default",
      },
    }),
  );
}

test.describe("Auth entry points", () => {
  test("the landing page offers sign-in and sign-up in three places", async ({ page }) => {
    await anonymous(page);
    await page.goto("/");

    // Header pair.
    await expect(page.locator("a.hdrv2-signup")).toHaveAttribute("href", "/signup");
    // Sidebar account block.
    await expect(page.locator("a.rail-acct-primary")).toHaveAttribute("href", "/signin");
    await expect(page.locator("a.rail-acct-ghost")).toHaveAttribute("href", "/signup");
    // The hero note, which says what an account is for rather than gating.
    await expect(page.locator(".landing-auth")).toContainText("save conversations");
  });

  test("/signup explains itself and refuses to pretend it can register", async ({ page }) => {
    await anonymous(page);
    await page.goto("/signup");

    await expect(page.getByRole("heading", { name: "Create an account" })).toBeVisible();
    // NEXT_PUBLIC_OIDC_* is unset in CI, so the button must say so rather than
    // redirect to a URL built from empty strings.
    const cta = page.getByRole("button", { name: /Identity provider not configured/ });
    await expect(cta).toBeVisible();
    await expect(cta).toBeDisabled();
    // Neither auth route is a dead end.
    await expect(page.locator('.signin-switch a[href="/signin"]')).toBeVisible();
    await expect(page.locator('.signin-switch a[href="/"]')).toBeVisible();
  });

  test("/signin links to /signup", async ({ page }) => {
    await anonymous(page);
    await page.goto("/signin");
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    await expect(page.locator('.signin-switch a[href="/signup"]')).toBeVisible();
  });

  test("a confirmed session replaces the call to action", async ({ page }) => {
    await signedIn(page);
    await page.goto("/");

    await expect(page.locator(".rail-acct-name")).toHaveText("verified_taxpayer@example.ug");
    await expect(page.locator("a.hdrv2-signup")).toHaveCount(0);
    await expect(page.locator("a.rail-acct-primary")).toHaveCount(0);

    // Signing out from the account menu brings the entry points back.
    await page.locator(".rail-acct-user").click();
    await page.getByRole("menuitem", { name: /Sign out/ }).click();
    await expect(page.locator("a.rail-acct-primary")).toBeVisible();
  });

  test("a token the backend refuses is not shown as a session", async ({ page }) => {
    await anonymous(page);
    await page.addInitScript(() => {
      window.localStorage.setItem("ura_auth_token", "stale-token");
    });
    // The catch-all from mockBackend answers {} — i.e. not authenticated.
    await page.goto("/");
    await expect(page.locator(".rail-acct-pitch")).toContainText("no longer valid");
  });
});

test.describe("Settings", () => {
  test.beforeEach(async ({ page }) => {
    await anonymous(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open settings" }).click();
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
  });

  test("a theme change is applied to the whole app, not just the dialog", async ({ page }) => {
    await page.getByRole("radio", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("radio", { name: "Light" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  });

  test("the response language chosen here is the one the header shows", async ({ page }) => {
    await page.getByLabel("Response language", { exact: true }).selectOption("lg");
    await page.getByRole("button", { name: "Close settings" }).click();
    await expect(page.locator(".langsel-btn")).toContainText("LG");
  });

  test("every section renders", async ({ page }) => {
    for (const tab of ["Voice", "Tax profile", "Privacy & data", "Account"]) {
      await page.getByRole("tab", { name: tab }).click();
      await expect(page.getByRole("tab", { name: tab })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByRole("tabpanel")).toBeVisible();
    }
  });

  test("arrow keys move between tabs", async ({ page }) => {
    const general = page.getByRole("tab", { name: "General" });
    await general.focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByRole("tab", { name: "Voice" })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true");
  });

  test("deleting the local history asks first", async ({ page }) => {
    // Seed a conversation so the row is enabled.
    await page.getByRole("button", { name: "Close settings" }).click();
    await page.getByLabel("Type your message").fill("What is the VAT rate?");
    await page.getByLabel("Send message").click();
    await expect(page.locator(".message-row-user")).toBeVisible();

    await page.getByRole("button", { name: "More options" }).click();
    await page.getByRole("menuitem", { name: "Settings" }).click();
    await page.getByRole("tab", { name: "Privacy & data" }).click();
    await page.getByRole("button", { name: "Delete all" }).click();

    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toContainText("Delete all conversations?");
    // Escape dismisses the confirmation without touching the settings behind it.
    await page.keyboard.press("Escape");
    await expect(confirm).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
  });

  test("Escape closes the dialog and returns focus to the trigger", async ({ page }) => {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog", { name: "Settings" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open settings" })).toBeFocused();
  });

  test("signed out, the account tab offers both routes", async ({ page }) => {
    await page.getByRole("tab", { name: "Account" }).click();
    const panel = page.getByRole("tabpanel");
    await expect(panel.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/signin");
    await expect(panel.getByRole("link", { name: "Create an account" })).toHaveAttribute(
      "href",
      "/signup",
    );
  });
});
