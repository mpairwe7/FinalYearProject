/**
 * Voice E2E — Tier A, mobile (mocked backend).
 *
 * On the mobile layout (≤720px) the header voice toggles + health pill are
 * hidden by CSS; the voice surface is the full-screen voice-first DIALOG
 * (VoiceChat.tsx), which drives ASR/TTS over the /v1/voice/chat/stream
 * WebSocket. These tests open that dialog, exercise the real on-device capture
 * (getUserMedia → MediaRecorder against Chromium's fake device), and assert the
 * orb state machine + the transcript/reply rendered from a stubbed WS sequence.
 *
 * Run on the mobile-chrome project (Pixel 7). See voice-e2e.yml.
 */
import { expect, test } from "@playwright/test";

import {
  clearChatStore,
  mockBackend,
  mockVoiceWebSocket,
  seedConsent,
  sendMessage,
} from "./helpers";

test.describe("Voice STT/TTS — mobile dialog (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("voice-first dialog opens with the idle orb", async ({ page }) => {
    await mockBackend(page);
    await mockVoiceWebSocket(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open voice chat" }).click();
    const dialog = page.getByRole("dialog", { name: "Voice chat" });
    await expect(dialog).toBeVisible();
    await expect(page.getByRole("button", { name: "Tap to speak" })).toBeVisible();
  });

  test("tapping the orb starts on-device capture (idle → listening)", async ({ page }) => {
    await mockBackend(page);
    await mockVoiceWebSocket(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open voice chat" }).click();

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

  test("narrating a typed reply calls /v1/tts on mobile", async ({ page }) => {
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });

    const ttsReq = page.waitForRequest("**/api/v1/tts");
    await page.getByRole("button", { name: /Listen in English/ }).last().click();
    const req = await ttsReq;
    expect(req.method()).toBe("POST");
    expect(typeof req.postDataJSON().text).toBe("string");
  });
});
