/**
 * Live dictation — the composer's real-time speech-to-text contract.
 *
 * Separate from voice.spec.ts on purpose. These drive a *scripted*
 * SpeechRecognition rather than a microphone, so they need neither Chromium's
 * fake capture device nor a mic permission, and can therefore run on every
 * browser project instead of the Chromium-only voice ones. The real engines
 * cannot be driven from a test — they transcribe silence — so what is asserted
 * here is our handling of the API's contract, not any engine's accuracy.
 *
 * The behaviour under test is what makes dictation feel live: interim results
 * render as they arrive and are *replaced* when the engine commits them.
 * Before this, interimResults was off, so the composer stayed empty for the
 * length of an utterance and then filled in one jump.
 */
import { expect, test } from "@playwright/test";

import { clearChatStore, mockBackend, seedConsent } from "./helpers";

test.describe("Live dictation", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  test("dictation transcribes live, while the person is still speaking", async ({ page }) => {
    // A scripted SpeechRecognition. The real engines cannot be driven from a
    // test — Chromium's fake device feeds silence — so the contract is stubbed
    // and what is asserted is our handling of it: interim results render
    // immediately and are replaced, not appended to, when the engine commits.
    await page.addInitScript(() => {
      class FakeRecognition extends EventTarget {
        lang = "";
        continuous = false;
        interimResults = false;
        onstart: (() => void) | null = null;
        onend: (() => void) | null = null;
        onerror: ((e: unknown) => void) | null = null;
        onresult: ((e: unknown) => void) | null = null;
        private timers: number[] = [];

        start() {
          (window as unknown as Record<string, unknown>).__recogConfig = {
            continuous: this.continuous,
            interimResults: this.interimResults,
          };
          this.onstart?.();
          const emit = (text: string, isFinal: boolean, resultIndex = 0) =>
            this.onresult?.({
              resultIndex,
              results: { 0: { 0: { transcript: text }, isFinal, length: 1 }, length: 1 },
            });
          // Honours interimResults the way a real engine does — emitting
          // partials only when they were asked for. That is what makes the
          // assertion below load-bearing: with the flag off, as it used to be,
          // nothing arrives until the final result and the first expect fails.
          if (this.interimResults) {
            this.timers.push(
              window.setTimeout(() => emit("how do", false), 100),
              window.setTimeout(() => emit("how do I regis", false), 300),
            );
          }
          this.timers.push(
            window.setTimeout(() => emit("how do I register for a TIN", true), 500),
          );
        }
        stop() {
          this.timers.forEach(clearTimeout);
          this.onend?.();
        }
        abort() {
          this.timers.forEach(clearTimeout);
        }
      }
      const w = window as unknown as Record<string, unknown>;
      w.SpeechRecognition = FakeRecognition;
      w.webkitSpeechRecognition = FakeRecognition;
    });
    await mockBackend(page);
    await page.goto("/");

    const box = page.getByLabel("Type your message");
    await page.getByRole("button", { name: "Start speaking" }).click();

    // Streaming has to be asked for: interimResults for live partials, and
    // continuous so the session survives the pause between two sentences.
    await expect
      .poll(() => page.evaluate(() => (window as unknown as Record<string, unknown>).__recogConfig))
      .toEqual({ continuous: true, interimResults: true });

    // The partial appears before anything is final — this is the whole feature.
    await expect(box).toHaveValue(/how do/i, { timeout: 3_000 });
    // ...and the committed result replaces the guess rather than stacking on it.
    await expect(box).toHaveValue("how do I register for a TIN", { timeout: 3_000 });
    await expect(box).not.toHaveValue(/how do how do/i);
  });

  test("live dictation appends to typed text instead of erasing it", async ({ page }) => {
    await page.addInitScript(() => {
      class FakeRecognition extends EventTarget {
        lang = "";
        continuous = false;
        interimResults = false;
        onstart: (() => void) | null = null;
        onend: (() => void) | null = null;
        onerror: ((e: unknown) => void) | null = null;
        onresult: ((e: unknown) => void) | null = null;
        start() {
          this.onstart?.();
          window.setTimeout(
            () =>
              this.onresult?.({
                resultIndex: 0,
                results: {
                  0: { 0: { transcript: "and what about VAT" }, isFinal: true, length: 1 },
                  length: 1,
                },
              }),
            100,
          );
        }
        stop() {
          this.onend?.();
        }
        abort() {}
      }
      const w = window as unknown as Record<string, unknown>;
      w.SpeechRecognition = FakeRecognition;
      w.webkitSpeechRecognition = FakeRecognition;
    });
    await mockBackend(page);
    await page.goto("/");

    const box = page.getByLabel("Type your message");
    await box.fill("I registered last year");
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(box).toHaveValue("I registered last year and what about VAT", { timeout: 3_000 });
  });
});
