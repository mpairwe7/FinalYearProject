/**
 * Voice E2E — Tier A, voice mode on a phone (mocked backend).
 *
 * This file used to drive the full-screen VoiceChat dialog and its
 * /v1/voice/chat/stream WebSocket, opened from a mic in the header. That mic is
 * gone — it was a second entry into speech sitting beside the composer's own —
 * so the dialog has no way in and those tests could only have been kept alive
 * by re-adding the control they were written to reach.
 *
 * The capability did not go anywhere: the composer's voice mode opens its dock
 * in the composer's place, records, transcribes on /v1/asr, and answers the
 * words it heard on /v1/chat/stream, which is what the dialog did. So the
 * coverage lives here, at phone width, where the dock must fit a 412 px screen
 * and its controls must stay thumb-sized.
 *
 * Run on the mobile-chrome project (Pixel 7) — it needs the Chromium fake
 * capture device. See voice-e2e.yml.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

test.describe("Voice STT/TTS — voice mode on a phone (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("entering voice mode opens the mic on a phone, in the composer's place", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Enter voice mode" }).click();
    const dock = page.getByTestId("voice-dock");
    await expect(dock).toBeVisible();
    // The dock replaces the composer rather than stacking a second bar on a
    // small screen; the mic is already open, so the one control is "Send now".
    await expect(page.getByLabel("Type your message")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Send now" })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "End voice mode" })).toBeVisible();

    // It fits the phone, and its controls are thumb-sized (WCAG 2.5.8 asks for 24 px).
    const viewport = page.viewportSize();
    const box = await dock.boundingBox();
    expect(box && viewport && box.x >= 0 && box.x + box.width <= viewport.width).toBe(true);
    for (const name of ["Send now", "End voice mode", /Turn the microphone off/]) {
      const control = await page.getByRole("button", { name }).boundingBox();
      expect(control && control.width >= 44 && control.height >= 44).toBe(true);
    }
  });

  test("full mobile round-trip: capture → /v1/asr → what was heard → streamed reply", async ({ page }) => {
    test.slow(); // real capture window
    await mockBackend(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Enter voice mode" }).click();
    const send = page.getByRole("button", { name: "Send now" });
    await expect(send).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1500); // let the fake device feed the recorder

    await send.click();

    // .last(): the transcript is a new turn appended after the seeded greeting,
    // so the assistant rows are the greeting and the reply. Matching the class
    // alone is a strict-mode violation.
    await expect(page.locator(".message-row-user").last()).toContainText(/VAT/i, {
      timeout: 15_000,
    });
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
  });
});
