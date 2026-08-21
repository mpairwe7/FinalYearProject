/**
 * Voice E2E — every configured language, end to end through the UI.
 *
 * The other voice specs are English-only, and the live probe checks the API
 * directly. Neither covers the join between them: that choosing Luganda in
 * the header actually makes the *browser* ask for Luganda speech, narrate in
 * a Luganda voice, and label its controls in Luganda terms. That join is
 * where a language quietly falls back to English — the request still succeeds,
 * audio still plays, and nothing looks broken.
 *
 * So these assert the outgoing contract per locale rather than any transcript:
 *   - narration POSTs /v1/tts with that locale and the voice chosen for it
 *   - dictation POSTs /v1/asr with that locale
 *   - the voice round-trip carries the locale and voice as query params
 *   - the recognizer is configured with that locale's BCP-47 speechLang
 *   - the UI names the language, so the control says what it will do
 *
 * A scripted SpeechRecognition stands in for the engine: no browser recognises
 * lg/sw, so a real one would be untestable for two of the three languages
 * and the failure mode we care about — sending the wrong `lang` — would be
 * invisible.
 *
 * Named languages.spec.ts, not voice.*: the firefox project excludes
 * /voice.*\.spec\.ts$/ because those need Chromium's fake capture device, and
 * all but one of these do not. Gecko is the only engine available in some
 * environments, and a language regression should not be invisible there.
 * The one test that does capture audio is chromium-gated on browserName.
 */
import { expect, test, type Page } from "@playwright/test";

import { clearChatStore, mockBackend, openSettings, seedConsent, sendMessage } from "./helpers";

/** Mirrors src/lib/locales.ts — kept here so a silent edit there fails a test. */
const LOCALES = [
  { value: "en", label: "English", native: "English", speechLang: "en-US" },
  { value: "lg", label: "Luganda", native: "Oluganda", speechLang: "lg-UG" },
  { value: "sw", label: "Swahili", native: "Kiswahili", speechLang: "sw-KE" },
] as const;

/** The catalogue shape /v1/speech/voices returns, trimmed to two per locale. */
const VOICE_CATALOGUE = {
  voices: {
    en: [
      { id: "en-US-AriaNeural", provider: "edge_tts", native: false, default: true, available: true },
      { id: "en-GB-SoniaNeural", provider: "edge_tts", native: false, default: false, available: true },
    ],
    lg: [
      { id: "salt_lug_0001", provider: "sunbird", native: true, default: true, available: true },
      { id: "waxal_lug_0002", provider: "sunbird", native: true, default: false, available: true },
    ],
    sw: [
      { id: "waxal_swa_0006", provider: "sunbird", native: true, default: true, available: true },
      { id: "waxal_swa_0007", provider: "sunbird", native: true, default: false, available: true },
    ],
  },
  sunbird_configured: true,
};

/**
 * A recognizer that records the `lang` it was configured with.
 *
 * No engine recognises Luganda or Swahili, so the real API would
 * make four of five languages untestable — and the bug worth catching is that
 * we send the wrong tag, which is observable before any audio exists.
 */
async function scriptedRecognizer(page: Page, transcript: string) {
  await page.addInitScript((text) => {
    class FakeRecognition extends EventTarget {
      lang = "";
      continuous = false;
      interimResults = false;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: ((e: unknown) => void) | null = null;
      onresult: ((e: unknown) => void) | null = null;
      start() {
        (window as unknown as Record<string, unknown>).__recogLang = this.lang;
        this.onstart?.();
        window.setTimeout(
          () =>
            this.onresult?.({
              resultIndex: 0,
              results: { 0: { 0: { transcript: text }, isFinal: true, length: 1 }, length: 1 },
            }),
          80,
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
  }, transcript);
}

/** Serve the real catalogue shape so the settings voice picker has options. */
async function mockVoiceCatalogue(page: Page) {
  await page.route("**/api/v1/speech/voices", (route) => route.fulfill({ json: VOICE_CATALOGUE }));
}

/** Pick a language from the header picker, the way a person does. */
async function chooseLanguage(page: Page, label: string) {
  await page.locator(".langsel-btn").click();
  await page.getByRole("radio", { name: new RegExp(label) }).click();
  await expect(page.getByRole("dialog", { name: "Response language" })).toHaveCount(0);
}

test.describe("Every configured language, through the UI", () => {
  test.beforeEach(async ({ page }) => {
    await seedConsent(page);
    await clearChatStore(page);
  });

  for (const locale of LOCALES) {
    test(`${locale.label}: narration asks /v1/tts for ${locale.value}`, async ({ page }) => {
      await mockBackend(page);
      await mockVoiceCatalogue(page);

      const ttsBodies: Array<Record<string, unknown>> = [];
      await page.route("**/api/v1/tts", async (route) => {
        ttsBodies.push(route.request().postDataJSON());
        await route.fulfill({
          json: {
            sample_rate: 22050,
            num_samples: 1102,
            duration_s: 0.05,
            latency_s: 0.1,
            backend: "stub",
            voice: "stub",
            audio_base64: "",
            error: null,
          },
        });
      });

      await page.goto("/");
      await chooseLanguage(page, locale.label);
      await sendMessage(page, "What is the VAT rate?");
      await expect(page.locator(".message-row-assistant").last()).toBeVisible();

      // The Listen control names the language, so it says what it will do.
      const listen = page.getByRole("button", { name: `Listen in ${locale.label}` }).last();
      await expect(listen).toBeVisible();
      await listen.click();

      await expect.poll(() => ttsBodies.length, { timeout: 10_000 }).toBeGreaterThan(0);
      expect(ttsBodies[0].language).toBe(locale.value);
      // No explicit pick yet, so the backend default stands rather than the
      // client inventing one.
      expect(ttsBodies[0].voice ?? null).toBeNull();
    });

    // Named for what it asserts: this is the browser-Speech-API path, which
    // transcribes in-process and never calls /v1/asr. The server path is
    // covered once below, and per-language by scripts/probe_deploy.py.
    test(`${locale.label}: the recognizer is asked for ${locale.speechLang}`, async ({ page }) => {
      await mockBackend(page);
      await mockVoiceCatalogue(page);
      await scriptedRecognizer(page, "sample utterance");

      await page.goto("/");
      await chooseLanguage(page, locale.label);
      await page.getByRole("button", { name: "Start speaking" }).click();

      // The recognizer gets this language's BCP-47 tag, not the default en-US.
      await expect
        .poll(() => page.evaluate(() => (window as unknown as Record<string, unknown>).__recogLang))
        .toBe(locale.speechLang);
      await expect(page.getByLabel("Type your message")).toHaveValue(/sample utterance/);
    });
  }

  test("choosing a voice for one language does not move the others", async ({ page }) => {
    await mockBackend(page);
    await mockVoiceCatalogue(page);

    const ttsBodies: Array<Record<string, unknown>> = [];
    await page.route("**/api/v1/tts", async (route) => {
      ttsBodies.push(route.request().postDataJSON());
      await route.fulfill({
        json: { sample_rate: 22050, num_samples: 1, duration_s: 0, latency_s: 0,
                backend: "stub", voice: "stub", audio_base64: "", error: null },
      });
    });

    await page.goto("/");
    await openSettings(page);
    await page.getByRole("tab", { name: "Voice" }).click();

    // Pick the non-default Luganda speaker. Voices are per language, so this
    // must not become the voice for Swahili.
    const lgGroup = page.getByRole("radiogroup", { name: "Narration voice for Luganda" });
    await lgGroup.getByRole("radio").nth(1).click();
    await expect(lgGroup.getByRole("radio").nth(1)).toHaveAttribute("aria-checked", "true");

    const swGroup = page.getByRole("radiogroup", { name: "Narration voice for Swahili" });
    await expect(swGroup.getByRole("radio").first()).toHaveAttribute("aria-checked", "true");

    await page.getByRole("button", { name: "Close settings" }).click();

    // Narrating in Luganda sends the chosen speaker...
    await chooseLanguage(page, "Luganda");
    await sendMessage(page, "VAT?");
    await page.getByRole("button", { name: "Listen in Luganda" }).last().click();
    await expect.poll(() => ttsBodies.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(ttsBodies[0].voice).toBe("waxal_lug_0002");
    expect(ttsBodies[0].language).toBe("lg");

    // ...and switching to Swahili does not carry it over.
    await chooseLanguage(page, "Swahili");
    await page.getByRole("button", { name: "Listen in Swahili" }).last().click();
    await expect.poll(() => ttsBodies.length, { timeout: 10_000 }).toBeGreaterThan(1);
    const sw = ttsBodies[ttsBodies.length - 1];
    expect(sw.language).toBe("sw");
    expect(sw.voice ?? null).toBeNull();
  });

  test("the settings picker offers every language its own voices", async ({ page }) => {
    await mockBackend(page);
    await mockVoiceCatalogue(page);
    await page.goto("/");
    await openSettings(page);
    await page.getByRole("tab", { name: "Voice" }).click();

    for (const locale of LOCALES) {
      const group = page.getByRole("radiogroup", {
        name: `Narration voice for ${locale.label}`,
      });
      await expect(group).toBeVisible();
      // Exactly one default per language, and a sample control per voice —
      // a voice you cannot hear before choosing is a guess, not a choice.
      await expect(group.getByRole("radio")).toHaveCount(2);
      await expect(group.locator('[aria-checked="true"]')).toHaveCount(1);
      await expect(
        group.getByRole("button", { name: new RegExp(`Play a ${locale.label} sample`) }),
      ).toHaveCount(2);
    }
  });
});

test.describe("Voice round-trip carries the language", () => {
  // Needs Chromium's fake capture device.
  test.skip(
    ({ browserName }) => browserName !== "chromium",
    "the voice round-trip needs the Chromium fake capture device",
  );

  test("server-side dictation POSTs /v1/asr with the chosen language", async ({ page }) => {
    test.slow();
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page, { transcript: "emisolo gy'eggwanga" });
    await mockVoiceCatalogue(page);
    // No Speech API → the recording + /v1/asr fallback. defineProperty, not
    // delete: Chromium defines webkitSpeechRecognition on the Window prototype.
    await page.addInitScript(() => {
      for (const n of ["SpeechRecognition", "webkitSpeechRecognition"]) {
        Object.defineProperty(window, n, { value: undefined, configurable: true });
      }
    });

    const urls: string[] = [];
    await page.route("**/api/v1/asr**", async (route) => {
      urls.push(route.request().url());
      await route.fulfill({
        json: { text: "emisolo gy'eggwanga", language: "lg", duration_s: 0.05,
                latency_s: 0.1, rtf: 2.0, backend: "stub", error: null },
      });
    });

    await page.goto("/");
    await chooseLanguage(page, "Luganda");
    await page.getByRole("button", { name: "Start speaking" }).click();
    const stop = page.getByRole("button", { name: "Stop and insert text" });
    await expect(stop).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1200);
    await stop.click();

    await expect.poll(() => urls.length, { timeout: 20_000 }).toBeGreaterThan(0);
    expect(urls[0]).toContain("language=lg");
    await expect(page.getByLabel("Type your message")).toHaveValue(/emisolo/);
  });

  test("voice mode POSTs /v1/voice/chat with the chosen language", async ({ page }) => {
    test.slow();
    await seedConsent(page);
    await clearChatStore(page);
    await mockBackend(page);
    await mockVoiceCatalogue(page);

    const urls: string[] = [];
    await page.route("**/api/v1/voice/chat**", async (route) => {
      urls.push(route.request().url());
      await route.fulfill({
        json: {
          transcript: "emisolo", transcript_language: "lg", conversation_id: "c-e2e",
          reply: "18%", reply_audio_base64: "", sample_rate: 22050, duration_s: 0.05,
          sources: [], citations: [], faithfulness_score: 0.9, retrieval_mode: "hybrid",
          asr_backend: "stub", tts_backend: "stub", mt_backend: "stub", error: null,
        },
      });
    });

    await page.goto("/");
    await chooseLanguage(page, "Luganda");
    await page.getByRole("button", { name: "Enter voice mode" }).click();
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByRole("button", { name: "Send recording" })).toBeVisible({
      timeout: 10_000,
    });
    await page.waitForTimeout(1200);
    await page.getByRole("button", { name: "Send recording" }).click();

    await expect.poll(() => urls.length, { timeout: 20_000 }).toBeGreaterThan(0);
    expect(urls[0]).toContain("language=lg");
  });
});
