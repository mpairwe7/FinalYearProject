/**
 * Per-conversation actions in the sidebar, and the overflow route behind them.
 *
 * Covers what the row's 3-dot menu replaced the bare delete icon with — Pin,
 * Rename, Delete — plus the "Chats" category fold and the "View all
 * conversations" link that appears once the rail stops showing everything.
 *
 * Pin and Rename are asserted through localStorage as well as the DOM, because
 * both are only useful if they survive the next turn: `saveCurrentSession`
 * re-derives a title from the first message on every save, and a rename that
 * is not marked as the user's own is silently reverted by it.
 */
import { expect, test, type Page } from "@playwright/test";

import { mockBackend, seedConsent } from "./helpers";

const TITLES = [
  "VAT rate for standard supplies",
  "Registering for a TIN",
  "Filing annual returns",
  "Import duty on a used vehicle",
  "PAYE bands",
  "Withholding tax on rent",
  "Objecting to an assessment",
  "EFRIS invoice errors",
  "Stamp duty on land",
  "Excise duty on airtime",
];

/** Seed a history big enough to cross the "View all conversations" threshold. */
async function seedConversations(page: Page, titles: string[] = TITLES) {
  await page.addInitScript((list: string[]) => {
    const now = Date.now();
    const conversations = list.map((title, i) => ({
      id: `c${i}`,
      title,
      preview: `preview ${i}`,
      turns: [
        { id: `u${i}`, role: "user", content: title, timestamp: now - i * 3600_000 },
        { id: `a${i}`, role: "assistant", content: `answer ${i}`, timestamp: now - i * 3600_000 },
      ],
      createdAt: now - i * 3600_000,
      updatedAt: now - i * 3600_000,
    }));
    window.localStorage.setItem(
      "ura-chat-store",
      JSON.stringify({ state: { locale: "en", conversations, activeConversationId: "c0" }, version: 2 }),
    );
  }, titles);
}

const stored = (page: Page) =>
  page.evaluate(
    () => JSON.parse(window.localStorage.getItem("ura-chat-store") || "{}")?.state?.conversations ?? [],
  );

test.describe("Conversation row actions", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await seedConversations(page);
    await mockBackend(page);
    await page.goto("/");
    await expect(page.locator(".rail-item").first()).toBeVisible();
  });

  test("pin moves a thread into its own group and persists", async ({ page }) => {
    const target = page.locator(".rail-item").filter({ hasText: "PAYE bands" });
    await target.hover();
    await target.getByLabel(/^Options for PAYE bands/).click();
    await page.getByRole("menuitem", { name: "Pin" }).click();

    await expect(page.locator(".rail-group-label").first()).toHaveText("Pinned");
    await expect(page.locator(".rail-item-title").first()).toHaveText("PAYE bands");
    expect((await stored(page)).find((c: { title: string }) => c.title === "PAYE bands")?.pinned).toBe(true);
  });

  test("rename survives the next turn", async ({ page }) => {
    const target = page.locator(".rail-item").filter({ hasText: "Registering for a TIN" });
    await target.hover();
    await target.getByLabel(/^Options for Registering for a TIN/).click();
    await page.getByRole("menuitem", { name: "Rename" }).click();

    const editor = page.locator(".rail-item-rename");
    await editor.fill("TIN registration steps");
    await editor.press("Enter");
    await expect(page.locator(".rail-item").filter({ hasText: "TIN registration steps" })).toBeVisible();

    // Send a turn on the active thread; the renamed one must not be re-derived.
    await page.getByLabel("Type your message").fill("What is the VAT rate?");
    await page.getByLabel("Send message").click();
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", { timeout: 15_000 });

    await expect(page.locator(".rail-item").filter({ hasText: "TIN registration steps" })).toBeVisible();
    const renamed = (await stored(page)).find((c: { id: string }) => c.id === "c1");
    expect(renamed?.title).toBe("TIN registration steps");
    expect(renamed?.titleCustom).toBe(true);
  });

  test("delete is confirm-gated and then removes the thread", async ({ page }) => {
    const target = page.locator(".rail-item").filter({ hasText: "Stamp duty on land" });
    await target.hover();
    await target.getByLabel(/^Options for Stamp duty on land/).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();

    await expect(page.getByRole("alertdialog", { name: /Delete conversation/i })).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();

    await expect(page.locator(".rail-item").filter({ hasText: "Stamp duty on land" })).toHaveCount(0);
    expect((await stored(page)).some((c: { title: string }) => c.title === "Stamp duty on land")).toBe(false);
  });

  test("the Chats category folds the list away and back", async ({ page }) => {
    const category = page.getByRole("button", { name: "Chats" });
    await expect(category).toHaveAttribute("aria-expanded", "true");
    const before = await page.locator(".rail-item").count();

    await category.click();
    await expect(category).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#rail-chats-list")).toHaveCount(0);

    await category.click();
    await expect(category).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator(".rail-item")).toHaveCount(before);
  });

  test("view all conversations leads to /chats, which opens a thread", async ({ page }) => {
    await page.getByRole("link", { name: "View all conversations" }).click();
    await expect(page).toHaveURL(/\/chats$/);
    await expect(page.getByRole("heading", { name: "Chats", level: 1 })).toBeVisible();

    // The page searches the same history.
    await page.getByLabel("Search your conversations").fill("EFRIS");
    await expect(page.locator(".chatspg-title")).toHaveCount(1);
    await page.getByLabel("Search your conversations").fill("");

    await page.locator(".chatspg-open").filter({ hasText: "Withholding tax on rent" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator(".hdrv2-convtitle-text")).toHaveText("Withholding tax on rent");
  });

  test("the search overlay finds and opens a thread", async ({ page }) => {
    await page.getByLabel("Search conversations").click();
    const dialog = page.getByRole("dialog", { name: "Search conversations" });
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("Search your conversations").fill("airtime");
    await expect(dialog.locator(".csrch-opt")).toHaveCount(1);
    await dialog.getByLabel("Search your conversations").press("Enter");

    await expect(dialog).toBeHidden();
    await expect(page.locator(".hdrv2-convtitle-text")).toHaveText("Excise duty on airtime");
  });
});
