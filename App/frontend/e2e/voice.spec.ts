/**
 * Voice E2E — Tier A (mocked backend).
 *
 * Exercises the browser side of the STT/TTS surface against a fully stubbed
 * `/api/*` (see helpers.ts): the real getUserMedia → MediaRecorder → AudioContext
 * capture pipeline runs against Chromium's fake media device, and the captured
 * audio is POSTed to the stubbed `/v1/voice/chat` and `/v1/tts`. We assert the UI
 * state machine and the *request contract* (raw-audio body, consent header,
 * sample-rate query) rather than transcription accuracy (the fake mic is silent).
 *
 * Requires the config-level `--use-fake-{ui,device}-for-media-stream` flags,
 * `permissions: ['microphone']`, and `--autoplay-policy=no-user-gesture-required`.
 *
 * Runs on both `chromium` and `mobile-chrome`. Three tests drive the desktop
 * header voice controls (the "Voice" toggle + health pill), which are CSS-hidden
 * below 720px — those skip on mobile-chrome, where the equivalent flow is the
 * voice-first dialog (see voice.mobile.spec.ts).
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

// Desktop-only: header voice toggle + health pill are hidden <720px.
const HEADER_ONLY = "header voice controls are hidden on the mobile layout (<720px)";

test.describe("Voice STT/TTS (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("speech-health pill reports the pipeline ready", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile-chrome", HEADER_ONLY);
    await mockBackend(page);
    await page.goto("/");
    // Validates the /v1/speech/health stub shape (status:'ready') the UI keys on.
    await expect(page.getByText("Voice ready")).toBeVisible({ timeout: 15_000 });
  });

  test("voice-mode toggle is enabled when speech is ready", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile-chrome", HEADER_ONLY);
    await mockBackend(page);
    await page.goto("/");
    const voiceToggle = page.getByRole("button", { name: "Enter voice mode" });
    await expect(voiceToggle).toBeEnabled();
    await voiceToggle.click();
    // The one primary slot now reads "Exit voice mode" — same button, engaged.
    await expect(page.getByRole("button", { name: "Exit voice mode" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // The mic affordance is present and enabled in the composer.
    await expect(page.getByRole("button", { name: "Start speaking" })).toBeEnabled();
  });

  test("recording a turn POSTs raw audio + consent to /v1/voice/chat and renders transcript + reply", async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name === "mobile-chrome", HEADER_ONLY);
    test.slow(); // real MediaRecorder capture needs a recording window
    await mockBackend(page);
    await page.goto("/");

    await page.getByRole("button", { name: "Enter voice mode" }).click();

    // Start recording → composer switches to the "Listening…" state.
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByText("Listening...")).toBeVisible({ timeout: 10_000 });

    // Let the fake device feed the MediaRecorder so the decoded PCM is non-empty.
    await page.waitForTimeout(1500);

    const voiceReq = page.waitForRequest("**/api/v1/voice/chat**");
    await page.getByRole("button", { name: "Send recording" }).click();

    const req = await voiceReq;
    expect(req.method()).toBe("POST");
    expect((req.headers()["content-type"] || "")).toContain("application/octet-stream");
    expect(req.headers()["x-voice-consent"]).toBe("true");
    expect(req.url()).toContain("sample_rate=");

    // The stubbed compound reply is rendered as user transcript + assistant turn.
    await expect(page.locator(".message-row-user").last()).toContainText("VAT", {
      timeout: 15_000,
    });
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
  });

  test("listening to a reply calls /v1/tts with the reply text", async ({ page }) => {
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
    const body = req.postDataJSON();
    expect(body.language).toBe("en");
    expect(typeof body.text).toBe("string");
    expect(body.text.length).toBeGreaterThan(0);
  });

  test("voice-first dialog opens from the header control", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Open voice chat" }).click();
    await expect(page.getByRole("dialog", { name: "Voice chat" })).toBeVisible();
  });
});
