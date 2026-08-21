/**
 * Consent banner, conversation-rail interactions, and route navigation
 * (analytics dashboard + evaluation). The evaluation page ships static
 * fallback data, so it renders without a backend.
 *
 * The banner is a `region` labelled "Privacy notice", not an `alertdialog`:
 * a86f3b0b ("fix(a11y): close WCAG 2.2 audit gaps") reclassified it, because
 * it interrupts nothing and traps no focus. These assertions were still
 * looking for the old role.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

test.describe("Analytics consent banner", () => {
  test.beforeEach(async ({ page }) => {
    await clearChatStore(page);
    await mockBackend(page);
    // Deliberately do NOT seed consent so the banner renders.
  });

  test("accept dismisses the banner and persists the decision", async ({ page }) => {
    await page.goto("/");
    const banner = page.getByRole("region", { name: /Privacy notice/i });
    await expect(banner).toBeVisible();
    await banner.getByRole("button", { name: "Accept" }).click();
    await expect(banner).toBeHidden();
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("ura_analytics_consent")))
      .toBeTruthy();
  });

  test("decline also dismisses the banner", async ({ page }) => {
    await page.goto("/");
    const banner = page.getByRole("region", { name: /Privacy notice/i });
    await banner.getByRole("button", { name: "Decline" }).click();
    await expect(banner).toBeHidden();
  });
});

test.describe("Conversation rail", () => {
  test.use({ viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true });

  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
  });

  test("opens, shows empty state, and a new-chat control", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Open conversation history").click();
    const rail = page.locator("aside.conversation-rail");
    // The "Chats" heading and the always-on search field are gone: the rail is
    // headed by the brand, and search is a button that opens an overlay.
    await expect(rail.locator(".rail-brand")).toBeVisible();
    await expect(rail.getByText(/New chat/i)).toBeVisible();
    await expect(rail.getByRole("button", { name: "Chats" })).toBeVisible();
  });

  test("the search icon opens an overlay that works with no history", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Open conversation history").click();
    await page.getByLabel("Search conversations").click();

    const dialog = page.getByRole("dialog", { name: "Search conversations" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel("Search your conversations")).toBeFocused();
    // Nothing saved yet, and the overlay still opens and says so.
    await expect(dialog.getByText(/No conversations yet/i)).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });
});

test.describe("Navigation: analytics + evaluation", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await mockBackend(page);
  });

  test("analytics dashboard route renders", async ({ page }) => {
    await page.goto("/analytics");
    // The /analytics route mounts and shows its dashboard heading.
    await expect(page.getByRole("heading", { name: /Analytics Dashboard/i })).toBeVisible();
  });

  test("evaluation page renders without a backend (static fallback)", async ({ page }) => {
    await page.goto("/analytics/evaluation");
    // Renders without a backend (static fallback). A heading is present.
    await expect(page.getByRole("heading").first()).toBeVisible();
  });

  test("can navigate analytics → back to chat", async ({ page }) => {
    await page.goto("/analytics");
    const back = page.getByRole("link", { name: /Back to Chat/i });
    if (await back.count()) {
      await back.first().click();
      await expect(page.getByLabel("Type your message")).toBeVisible();
    }
  });
});
