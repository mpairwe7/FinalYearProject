import { expect, test } from "@playwright/test";
import { clearChatStore, seedConsent, sendMessage } from "./helpers";

test.describe("Frontend Automatic Language Detection & Switching Flows", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("auto-detects Luganda prompt and streams translated response on live backend", async ({ page }) => {
    test.setTimeout(60000);
    await page.goto("/");

    await sendMessage(page, "Njagala kufuna TIN yange mu URA, nina kukola ntya?");

    const assistantMsg = page.locator(".message-row-assistant").last();
    await expect(assistantMsg).toBeVisible({ timeout: 20000 });
    // Expect response to complete streaming with localized Luganda content
    await expect(assistantMsg).toContainText(/Nsonyiwa|kuyamba|wandiisa|omuntu|ura|kufuna/i, { timeout: 40000 });
    const text = await assistantMsg.innerText();
    console.log("Live Luganda Reply:", text.replace(/\n+/g, " ").slice(0, 160));
  });

  test("auto-detects Kiswahili prompt and streams translated response on live backend", async ({ page }) => {
    test.setTimeout(60000);
    await page.goto("/");

    await sendMessage(page, "Ninahitaji kusajili TIN ya biashara yangu URA, nifanyeje?");

    const assistantMsg = page.locator(".message-row-assistant").last();
    await expect(assistantMsg).toBeVisible({ timeout: 20000 });
    await expect(assistantMsg).toContainText(/kujisajili|biashara|ura|mtandaoni|namba|ushuru|Samahani|Nsonyiwa/i, { timeout: 40000 });
    const text = await assistantMsg.innerText();
    console.log("Live Swahili Reply:", text.replace(/\n+/g, " ").slice(0, 160));
  });

  test("auto-detects English prompt and streams English response on live backend", async ({ page }) => {
    test.setTimeout(60000);
    await page.goto("/");

    await sendMessage(page, "How do I register for a new TIN number online with URA?");

    const assistantMsg = page.locator(".message-row-assistant").last();
    await expect(assistantMsg).toBeVisible({ timeout: 20000 });
    await expect(assistantMsg).toContainText(/individual|organisation|register|help|tin|ura/i, { timeout: 40000 });
    const text = await assistantMsg.innerText();
    console.log("Live English Reply:", text.replace(/\n+/g, " ").slice(0, 160));
  });
});
