/**
 * Voice E2E — Tier A, mobile composer (mocked backend).
 *
 * This file used to drive the full-screen VoiceChat dialog and its
 * /v1/voice/chat/stream WebSocket, opened from a mic in the header. That mic is
 * gone — it was a second entry into speech sitting beside the composer's own —
 * so the dialog has no way in and those tests could only have been kept alive
 * by re-adding the control they were written to reach.
 *
 * The capability did not go anywhere: the composer's voice mode records,
 * POSTs /v1/voice/chat, and renders transcript + reply, which is what the
 * dialog did. So the coverage moves here rather than being deleted. The
 * round-trip is REST now, not the WS stream, so the mockVoiceWebSocket helper
 * these tests used went with them — it had no other caller. It is in git history
 * if the dialog is ever given an entry point again.
 *
 * Run on the mobile-chrome project (Pixel 7) — it needs the Chromium fake
 * capture device. See voice-e2e.yml.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

test.describe("Voice STT/TTS — mobile composer (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("entering voice mode arms the mic on a phone", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Enter voice mode" }).click();
    // Voice mode is a toggle, not a mode switch that hides the composer: the
    // textarea stays, and says so.
    await expect(page.getByPlaceholder(/Voice mode on/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Exit voice mode" })).toBeVisible();

    // Tap the mic → getUserMedia + MediaRecorder.start succeeded. Recording
    // replaces the composer with the waveform panel, so the mic is gone and the
    // checkmark is what stops it — labelled "Send recording" here because voice
    // mode does send the utterance as a turn.
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByText("Listening...")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Send recording" })).toBeVisible();
  });

  test("full mobile round-trip: capture → /v1/voice/chat → transcript + reply", async ({
    page,
  }) => {
    test.slow(); // real capture window
    await mockBackend(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Enter voice mode" }).click();
    await page.getByRole("button", { name: "Start speaking" }).click();
    const send = page.getByRole("button", { name: "Send recording" });
    await expect(send).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1500); // let the fake device feed the recorder

    // The checkmark stops + sends the utterance → the stub answers with both.
    await send.click();

    await expect(page.locator(".message-row-user")).toContainText(/VAT/i, { timeout: 15_000 });
    await expect(page.locator(".message-row-assistant")).toContainText("18%", {
      timeout: 15_000,
    });
  });
});
