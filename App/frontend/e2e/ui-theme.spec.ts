/**
 * E2E for the chat-UX additions: copy-to-clipboard on assistant replies and the
 * theme system (system-adaptive light/dark + the Auto/Light/Dark toggle).
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

test.describe("Copy assistant reply", () => {
  test.use({ permissions: ["clipboard-read", "clipboard-write"] });
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("copies the reply and shows a confirmation", async ({ page }) => {
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    const assistant = page.locator(".message-row-assistant").last();
    await expect(assistant).toContainText("18%", { timeout: 15_000 });

    await assistant.hover();
    await assistant.getByRole("button", { name: "Copy reply" }).click();
    await expect(assistant.getByRole("button", { name: "Reply copied" })).toBeVisible();

    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain("18%");
  });
});

test.describe("Theme — system-adaptive", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("auto follows a light OS", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await mockBackend(page);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const [r, g, b] = (bg.match(/\d+/g) || ["0", "0", "0"]).map(Number);
    expect((r + g + b) / 3).toBeGreaterThan(180); // light canvas
  });

  test("auto follows a dark OS", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await mockBackend(page);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });
});

test.describe("Theme toggle", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
    await page.emulateMedia({ colorScheme: "light" });
  });

  test("cycles Auto → Light → Dark → Auto and persists the choice", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    // The header's own ThemeToggle is `display: none` below 720px
    // (settings.css), moved into the kebab so it doesn't crowd the 44px
    // cluster next to language/overflow. The kebab's "Theme: …" menuitem is
    // reachable at every width — same trade-off as openSettings() in
    // helpers.ts — and clicking it doesn't close the menu, so one locator
    // covers all three clicks below.
    await page.getByRole("button", { name: "More options" }).click();
    const toggle = page.getByRole("menuitem", { name: /^Theme:/ });

    await expect(toggle).toHaveAttribute("aria-label", /Auto/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light"); // auto → light OS

    await toggle.click(); // Light
    await expect(toggle).toHaveAttribute("aria-label", /Light/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await toggle.click(); // Dark (overrides the light OS)
    await expect(toggle).toHaveAttribute("aria-label", /Dark/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await page.evaluate(() => localStorage.getItem("ura-theme"))).toBe("dark");

    await toggle.click(); // back to Auto
    await expect(toggle).toHaveAttribute("aria-label", /Auto/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(await page.evaluate(() => localStorage.getItem("ura-theme"))).toBeNull();
  });
});
