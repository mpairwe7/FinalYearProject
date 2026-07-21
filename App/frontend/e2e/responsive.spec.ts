/**
 * Responsive-layout E2E. The conversation rail is the primary mobile-vs-desktop
 * signal (globals.css): below 1024px it's an off-canvas overlay reachable via
 * the hamburger; at/above 1024px it's a persistent grid column and the
 * hamburger is `display:none`. At/below 720px the brand title is hidden.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

test.describe("Responsive layout", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
  });

  test.describe("desktop ≥1024px", () => {
    test.use({ viewport: { width: 1280, height: 800 } });

    test("rail is persistent and the brand title is shown", async ({ page }) => {
      await page.goto("/");
      // On desktop the rail is a persistent grid column and the brand title shows.
      await expect(page.locator("aside.conversation-rail")).toBeVisible();
      await expect(page.locator(".top-bar-title")).toBeVisible();
    });

    test("chat still works at desktop width", async ({ page }) => {
      await page.goto("/");
      await sendMessage(page, "What is the VAT rate?");
      await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
        timeout: 15_000,
      });
    });
  });

  test.describe("mobile (Pixel 7, 412px)", () => {
    test.use({
      viewport: { width: 412, height: 915 },
      isMobile: true,
      hasTouch: true,
      deviceScaleFactor: 2.625,
    });

    test("hamburger is shown, brand title hidden, rail opens as an overlay", async ({ page }) => {
      await page.goto("/");
      const hamburger = page.getByLabel("Open conversation history");
      await expect(hamburger).toBeVisible();
      await expect(page.locator(".top-bar-title")).toBeHidden();

      await hamburger.click();
      await expect(page.locator("aside.conversation-rail.conversation-rail-open")).toBeVisible();
      await expect(page.getByRole("heading", { name: "Chats" })).toBeVisible();

      // Closing via the close button collapses the overlay.
      await page.getByLabel("Close sidebar").click();
      await expect(
        page.locator("aside.conversation-rail.conversation-rail-open"),
      ).toHaveCount(0);
    });

    test("composer is reachable and sends on mobile", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByLabel("Type your message")).toBeVisible();
      await sendMessage(page, "How do I register for a TIN?");
      await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
        timeout: 15_000,
      });
    });
  });

  test.describe("tablet 768px", () => {
    test.use({ viewport: { width: 768, height: 1024 } });

    test("rail is still off-canvas below the 1024px breakpoint", async ({ page }) => {
      await page.goto("/");
      await expect(page.getByLabel("Open conversation history")).toBeVisible();
    });
  });

  test("respects reduced-motion preference", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByLabel("Type your message")).toBeVisible();
  });
});
