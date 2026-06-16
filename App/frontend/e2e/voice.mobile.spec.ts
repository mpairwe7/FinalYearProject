/**
 * Voice E2E — Tier A, mobile-dialog-specific (mocked backend).
 *
 * On the mobile layout (≤720px) the header voice toggles + health pill are
 * hidden by CSS; the voice surface is the full-screen voice-first DIALOG
 * (VoiceChat.tsx), which drives ASR/TTS over the /v1/voice/chat/stream
 * WebSocket. These tests cover the parts unique to that dialog — the orb state
 * machine and the WS round-trip. The viewport-agnostic flows (opening the
 * dialog, message-level TTS narration) are covered by voice.spec.ts, which also
 * runs on mobile-chrome.
 *
 * Run on the mobile-chrome project (Pixel 7). See voice-e2e.yml.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, mockVoiceWebSocket, seedConsent } from "./helpers";

test.describe("Voice STT/TTS — mobile dialog (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("tapping the orb starts on-device capture (idle → listening)", async ({ page }) => {
    await mockBackend(page);
    await mockVoiceWebSocket(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open voice chat" }).click();

    // Dialog opens at the idle orb, then the tap kicks off real capture.
    await expect(page.getByRole("dialog", { name: "Voice chat" })).toBeVisible();
    await page.getByRole("button", { name: "Tap to speak" }).click();
    // getUserMedia + MediaRecorder.start succeeded → orb reflects the listening phase.
    await expect(page.getByRole("button", { name: "Listening..." })).toBeVisible({
      timeout: 10_000,
    });
  });

  test("full mobile round-trip: capture → WS → transcript + reply render", async ({ page }) => {
    test.slow(); // real capture window
    await mockBackend(page);
    await mockVoiceWebSocket(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open voice chat" }).click();

    await page.getByRole("button", { name: "Tap to speak" }).click();
    await expect(page.getByRole("button", { name: "Listening..." })).toBeVisible({
      timeout: 10_000,
    });
    await page.waitForTimeout(1500); // let the fake device feed the recorder

    // Second tap stops + sends the utterance → stubbed WS emits transcript + reply.
    await page.getByRole("button", { name: "Listening..." }).click();

    await expect(page.locator(".voice-reply-text")).toContainText("18%", { timeout: 15_000 });
    await expect(page.locator(".voice-transcript-text")).toContainText(/VAT/i, {
      timeout: 15_000,
    });
  });
});
