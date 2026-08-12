/**
 * Voice E2E — Tier B (real backend, deterministic mock inference).
 *
 * Unlike voice.spec.ts (which stubs /api/*), this drives the browser against a
 * REAL FastAPI backend reached through the Next.js /api rewrite. The backend is
 * started with deterministic mock speech backends:
 *
 *   SPEECH_ENABLED=true SPEECH_ASR_BACKEND=mock SPEECH_TTS_BACKEND=mock \
 *   SPEECH_MT_BACKEND=mock LLM_ENABLED=false
 *
 * so it serves /v1/speech/health (ready), /v1/tts (real WAV bytes), and
 * /v1/voice/chat (200; mock ASR yields no transcript → the graceful
 * "No speech detected" path) without downloading any heavy models.
 *
 * Gated behind VOICE_INTEGRATION=1 so it never runs in the default (mocked)
 * suite or a stray local run. See .github/workflows/voice-e2e.yml.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, seedConsent } from "./helpers";

test.describe("Voice STT/TTS — real backend (integration)", () => {
  test.skip(
    !process.env.VOICE_INTEGRATION,
    "Set VOICE_INTEGRATION=1 against a backend with SPEECH_ENABLED + mock speech backends",
  );

  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("real /v1/speech/health reports the pipeline ready", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Voice ready")).toBeVisible({ timeout: 20_000 });
  });

  test("real voice round-trip: capture → /v1/voice/chat (200) → graceful reply", async ({
    page,
  }) => {
    test.slow();
    await page.goto("/");
    await page.getByRole("button", { name: "Enter voice mode" }).click();

    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByText("Listening...")).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1500);

    const resp = page.waitForResponse(
      (r) => r.url().includes("/api/v1/voice/chat") && r.request().method() === "POST",
      { timeout: 30_000 },
    );
    await page.getByRole("button", { name: "Send recording" }).click();
    const r = await resp;
    expect(r.status()).toBe(200);

    // Mock ASR returns no transcript → the client renders the graceful voice-error
    // turn ("Voice error: No speech detected …"). Assert the round-trip surfaced.
    await expect(page.locator(".message-row-assistant").last()).toContainText(
      /no speech detected|voice error/i,
      { timeout: 20_000 },
    );
  });

  test("real TTS: narrating a reply hits /v1/tts and returns audio", async ({ page }) => {
    test.slow();
    await page.goto("/");
    const chatDone = page.waitForResponse(
      (r) => r.url().includes("/api/v1/chat/stream"),
      { timeout: 30_000 },
    );
    await page.getByLabel("Type your message").fill("What is the standard VAT rate?");
    await page.getByLabel("Send message").click();
    await chatDone.catch(() => {}); // real keyword-FAQ reply (no LLM needed)

    // The listen control is only present once an assistant turn has rendered.
    const listen = page.getByRole("button", { name: /Listen in English/ }).last();
    await expect(listen).toBeVisible({ timeout: 25_000 });

    const ttsResp = page.waitForResponse(
      (r) => r.url().includes("/api/v1/tts") && r.request().method() === "POST",
      { timeout: 30_000 },
    );
    await listen.click();
    const r = await ttsResp;
    expect(r.status()).toBe(200);
    const body = await r.json();
    // Mock TTS produces a real WAV payload.
    expect(typeof body.audio_base64).toBe("string");
    expect(body.audio_base64.length).toBeGreaterThan(100);
    expect(body.error).toBeFalsy();
  });
});
