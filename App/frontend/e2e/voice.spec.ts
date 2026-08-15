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
 * Runs on both `chromium` and `mobile-chrome`. A few tests drive controls that
 * are CSS-hidden below 720px and skip on mobile-chrome, where the equivalent
 * flow lives in the composer (see voice.mobile.spec.ts).
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent, sendMessage } from "./helpers";

// Desktop-only: some composer controls are hidden <720px.
const HEADER_ONLY = "these controls are hidden on the mobile layout (<720px)";

/**
 * Take away the browser Speech API before the app boots.
 *
 * Not a contrivance — this is Firefox, and Chromium on platforms that do not
 * expose webkitSpeechRecognition. Dictation there falls through to recording +
 * the server's /v1/asr, and that path had no test: the button was enabled,
 * tapping it hit `if (!recognitionRef.current) return`, and nothing happened.
 * Deleting the API in Chromium is how we reach that branch while keeping the
 * fake capture device, which Firefox has no equivalent of.
 */
async function withoutSpeechApi(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    const w = window as unknown as Record<string, unknown>;
    delete w.SpeechRecognition;
    delete w.webkitSpeechRecognition;
  });
}

test.describe("Voice STT/TTS (mocked)", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("a ready pipeline leaves the composer's speech controls usable", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name === "mobile-chrome", HEADER_ONLY);
    await mockBackend(page);
    await page.goto("/");
    // This used to assert the header's "Voice ready" pill. The pill is gone —
    // a permanent status readout for something that is almost always fine — so
    // the same /v1/speech/health stub is now checked where it actually matters:
    // the controls it governs are live rather than disabled.
    await expect(page.getByRole("button", { name: "Start speaking" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Enter voice mode" })).toBeEnabled();
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

  test("dictation still works with no browser Speech API", async ({ page }) => {
    test.slow(); // real capture window
    await withoutSpeechApi(page);
    await mockBackend(page, { transcript: "how do I register for a TIN" });
    await page.goto("/");

    // Not in voice mode: this is dictation, which fills the composer rather
    // than sending a turn.
    const mic = page.getByRole("button", { name: "Start speaking" });
    await expect(mic).toBeEnabled();
    await mic.click();
    await expect(page.getByRole("button", { name: "Stop listening" })).toBeVisible({
      timeout: 10_000,
    });
    await page.waitForTimeout(1200); // let the fake device feed the recorder
    await page.getByRole("button", { name: "Stop listening" }).click();

    // The transcript lands in the textarea, and no turn was sent.
    await expect(page.getByLabel("Type your message")).toHaveValue(/TIN/i, { timeout: 15_000 });
    await expect(page.locator(".message-row-user")).toHaveCount(0);
  });

  test("dictation appends to what is already typed", async ({ page }) => {
    test.slow();
    await withoutSpeechApi(page);
    await mockBackend(page, { transcript: "and what about VAT" });
    await page.goto("/");

    const box = page.getByLabel("Type your message");
    await box.fill("I registered last year");
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByRole("button", { name: "Stop listening" })).toBeVisible({
      timeout: 10_000,
    });
    await page.waitForTimeout(1200);
    await page.getByRole("button", { name: "Stop listening" }).click();

    // Half-typed question plus the dictated rest — replacing would lose the typing.
    await expect(box).toHaveValue(/I registered last year.*VAT/i, { timeout: 15_000 });
  });

  test("speech has exactly one entry point, in the composer", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    // The header's voice-overlay mic is gone: it put a second, differently
    // shaped way into speech a few centimetres from the composer's own. This
    // replaces the "the overlay opens" test — asserting the overlay is
    // unreachable is what the change actually means.
    await expect(page.getByRole("button", { name: "Open voice chat" })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Voice chat" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Enter voice mode" })).toBeVisible();
  });
});
