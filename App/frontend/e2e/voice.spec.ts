/**
 * Voice E2E — Tier A (mocked backend).
 *
 * Exercises the browser side of the STT/TTS surface against a fully stubbed
 * `/api/*` (see helpers.ts): the real getUserMedia → MediaRecorder → AudioContext
 * capture pipeline runs against Chromium's fake media device. In voice mode the
 * captured audio is POSTed to the stubbed `/v1/asr`, what it heard goes out as
 * a turn on the stubbed `/v1/chat/stream`, and the answer is read from the
 * stubbed `/v1/tts/stream` (or `/v1/tts`, its fallback). We assert the voice
 * dock's state machine and the *request contract* (raw-audio body, consent
 * header, sample-rate query, the language heard) rather than transcription
 * accuracy (the fake mic is silent).
 *
 * Requires the config-level `--use-fake-{ui,device}-for-media-stream` flags,
 * `permissions: ['microphone']`, and `--autoplay-policy=no-user-gesture-required`.
 *
 * Runs on both `chromium` and `mobile-chrome`. A few tests drive controls that
 * are CSS-hidden below 720px and skip on mobile-chrome, where the equivalent
 * flow lives in the composer (see voice.mobile.spec.ts).
 */
import { expect, test, type Page } from "@playwright/test";

import { silentWavB64 } from "./audio";
import { TINY_WAV_B64 } from "./fixtures";
import { clearChatStore, mockBackend, seedConsent, sendMessage, sseReply } from "./helpers";

// Desktop-only: some composer controls are hidden <720px.
const HEADER_ONLY = "these controls are hidden on the mobile layout (<720px)";

/**
 * Take away the browser Speech API before the app boots.
 *
 * Not a contrivance — this is Firefox, and Chromium on platforms that do not
 * expose webkitSpeechRecognition. Dictation there falls through to recording +
 * the server's /v1/asr, and that path had no test: the button was enabled,
 * tapping it hit `if (!recognitionRef.current) return`, and nothing happened.
 * Removing the API in Chromium is how we reach that branch while keeping the
 * fake capture device, which Firefox has no equivalent of.
 *
 * defineProperty, not `delete`. Chromium exposes webkitSpeechRecognition as an
 * IDL attribute on the Window *prototype*, so deleting it off the instance is a
 * silent no-op: the prototype accessor still resolves, the app builds a real
 * recognizer, and start() then never fires onstart because headless CI has no
 * speech service. That is not hypothetical — it is exactly how these two tests
 * failed on chromium. Shadowing with an own property is what actually hides it,
 * and assertNoSpeechApi below makes a regression here loud instead of a
 * timeout on an unrelated button label.
 */
async function withoutSpeechApi(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    for (const name of ["SpeechRecognition", "webkitSpeechRecognition"]) {
      Object.defineProperty(window, name, { value: undefined, configurable: true });
    }
  });
}

/** Fail on the precondition rather than timing out on a button label. */
async function assertNoSpeechApi(page: import("@playwright/test").Page) {
  const visible = await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition);
  });
  expect(visible, "Speech API still reachable — the fallback branch was not exercised").toBe(false);
}

/**
 * A microphone that says something for 1.5 s, then goes quiet.
 *
 * A 500 Hz tone, in the band the end-of-turn meter listens to, at about
 * -23 dBFS. Chromium's own fake device only beeps, which is not speech.
 */
async function speakThenPause(page: Page) {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      const ac = new AudioContext();
      const voice = ac.createOscillator();
      voice.frequency.value = 500;
      const level = ac.createGain();
      level.gain.value = 0;
      const out = ac.createMediaStreamDestination();
      voice.connect(level).connect(out);
      voice.start();
      const at = ac.currentTime + 0.5;
      level.gain.setValueAtTime(0.1, at);
      level.gain.setValueAtTime(0, at + 1.5);
      return out.stream;
    };
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

  /**
   * Entering voice mode used to change a placeholder and nothing else: the mic
   * wanted a second tap on another button, and nothing said so. Now the dock
   * takes the composer's place with the mic already open, says "Listening",
   * and has the focus; End puts the composer back, with the focus in it.
   */
  test("entering voice mode opens the mic at once, in a dock that says so", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Enter voice mode" }).click();

    const dock = page.getByTestId("voice-dock");
    await expect(dock).toBeVisible();
    await expect(page.getByLabel("Type your message")).toHaveCount(0);
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Listening", { timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Send now" })).toBeFocused();
    await expect(dock.locator(".call-orb")).toHaveAttribute("data-state", "listening");

    await page.getByRole("button", { name: "End voice mode" }).click();
    await expect(dock).toHaveCount(0);
    await expect(page.getByLabel("Type your message")).toBeFocused();
  });

  test("Escape turns the mic off first, then leaves voice mode", async ({ page }) => {
    await mockBackend(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Enter voice mode" }).click();
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Listening", { timeout: 10_000 });

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Tap the mic to talk");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("voice-dock")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Enter voice mode" })).toBeVisible();
  });

  /**
   * The gap this change closes. The turn used to go to /v1/voice/chat, which
   * answered with the transcript and the whole reply together, 2–20 s later,
   * with nothing on screen moving in between — on the landing page, nothing
   * at all. Now what was heard is the taxpayer's turn before any answer
   * exists, the dock says it back and offers Stop while the answer is worked
   * on, and the answer streams in and is read aloud once it has all arrived.
   */
  test("a spoken turn shows what was heard at once, then streams the answer and reads it aloud", async ({
    page,
  }) => {
    test.slow(); // real MediaRecorder capture needs a recording window
    await mockBackend(page);
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/v1/chat/stream", async (route) => {
      await held;
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream; charset=utf-8" },
        body: sseReply("The standard VAT rate in Uganda is 18%."),
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Enter voice mode" }).click();
    const send = page.getByRole("button", { name: "Send now" });
    await expect(send).toBeVisible({ timeout: 10_000 });
    // Let the fake device feed the MediaRecorder so the decoded PCM is non-empty.
    await page.waitForTimeout(1500);

    const asrReq = page.waitForRequest("**/api/v1/asr**");
    const chatReq = page.waitForRequest("**/api/v1/chat/stream");
    await send.click();

    const asr = await asrReq;
    expect(asr.method()).toBe("POST");
    // WAV, not headerless PCM: a recording starting on sample -1 (FF FF) was sniffed as MP3 (G93).
    expect(asr.headers()["content-type"] || "").toContain("audio/wav");
    expect(asr.postDataBuffer()?.subarray(0, 4).toString("ascii")).toBe("RIFF");
    expect(asr.headers()["x-voice-consent"]).toBe("true");
    const asrUrl = new URL(asr.url());
    expect(asrUrl.searchParams.get("sample_rate")).toBeTruthy();
    expect(asrUrl.searchParams.get("language")).toBe("en");
    expect(asrUrl.searchParams.get("domain")).toBe("tax");

    // What was heard goes out as a turn, answered in the language it was heard in…
    expect((await chatReq).postDataJSON()).toMatchObject({
      message: "what is the standard VAT rate in Uganda",
      locale: "en",
      locale_explicit: true,
    });
    // …and is on screen, marked as spoken, while the answer is still being worked on.
    const userTurn = page.locator(".message-row-user").last();
    await expect(userTurn).toContainText("what is the standard VAT rate in Uganda");
    await expect(userTurn).toContainText("Spoken");
    await expect(page.getByTestId("voice-dock-detail")).toContainText(
      "You said: “what is the standard VAT rate in Uganda”",
    );
    await expect(page.getByTestId("voice-dock-primary")).toHaveAccessibleName("Stop");

    const ttsReq = page.waitForRequest("**/api/v1/tts/stream");
    release();
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
    expect((await ttsReq).postDataJSON().text).toContain("18%");
  });

  test("Stop in voice mode stops the answer, and does not read it out", async ({ page }) => {
    test.slow();
    await mockBackend(page);
    // An answer held back until the test is over: only Stop ends this turn.
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/v1/chat/stream", async (route) => {
      await held;
      // The page gave up on this request when Stop was pressed.
      await route.fulfill({ body: "" }).catch(() => {});
    });
    const tts: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/tts")) tts.push(r.url());
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Enter voice mode" }).click();
    const primary = page.getByTestId("voice-dock-primary");
    await expect(primary).toHaveAccessibleName("Send now", { timeout: 10_000 });
    await page.waitForTimeout(1200);
    await primary.click();

    await expect(primary).toHaveAccessibleName("Stop", { timeout: 15_000 });
    await primary.click();
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Tap the mic to talk", { timeout: 10_000 });
    await page.waitForTimeout(1000);
    expect(tts).toEqual([]);
    release();
  });

  test("listening to a reply streams its speech from /v1/tts/stream", async ({ page }) => {
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });

    const streamReq = page.waitForRequest("**/api/v1/tts/stream");
    await page.getByRole("button", { name: /Listen in English/ }).last().click();
    const req = await streamReq;
    expect(req.method()).toBe("POST");
    const body = req.postDataJSON();
    expect(body.language).toBe("en");
    expect(typeof body.text).toBe("string");
    expect(body.text.length).toBeGreaterThan(0);
    // Opus where the browser plays it, else WAV.
    expect(["opus", "wav"]).toContain(body.format);
  });

  test("listening falls back to /v1/tts when the stream is unavailable", async ({ page }) => {
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.route("**/api/v1/tts/stream", (route) => route.fulfill({ status: 503, body: "down" }));
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });

    const ttsReq = page.waitForRequest("**/api/v1/tts");
    await page.getByRole("button", { name: /Listen in English/ }).last().click();
    const body = (await ttsReq).postDataJSON();
    expect(body.language).toBe("en");
    expect(body.text.length).toBeGreaterThan(0);
  });

  test("stopping a read-aloud while it loads also stops its whole-reply fallback", async ({ page }) => {
    await page.addInitScript(() => {
      const start = AudioBufferSourceNode.prototype.start;
      AudioBufferSourceNode.prototype.start = function (...args) {
        const w = window as unknown as { __played?: number };
        w.__played = (w.__played ?? 0) + 1;
        return start.apply(this, args);
      };
    });
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    await page.route("**/api/v1/tts/stream", (route) => route.fulfill({ status: 503, body: "down" }));
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/v1/tts", async (route) => {
      await held;
      await route.fulfill({
        json: { sample_rate: 22050, num_samples: 1102, duration_s: 0.05, latency_s: 0.1,
                backend: "stub", voice: "stub", audio_base64: TINY_WAV_B64, error: null },
      });
    });
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });

    // The stream fails, so the whole reply is fetched; the reader stops it meanwhile.
    const fallback = page.waitForRequest("**/api/v1/tts");
    await page.getByRole("button", { name: /Listen in English/ }).last().click();
    await fallback;
    await page.getByRole("button", { name: "Stop listening" }).last().click();
    release();
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => (window as unknown as { __played?: number }).__played ?? 0)).toBe(0);
    await expect(page.getByRole("button", { name: /Listen in English/ }).last()).toBeEnabled();
  });

  test("voice mode sends the turn by itself when the speaker pauses, and says it will", async ({ page }) => {
    test.slow(); // a real recording window, then the pause
    await speakThenPause(page);
    await mockBackend(page);
    await page.goto("/");

    const asrReq = page.waitForRequest("**/api/v1/asr**", { timeout: 15_000 });
    await page.locator('[data-testid="composer-voicemode"]').click();
    // The tone is speech to the meter: the dock hears it, then counts the
    // pause down round the send button before the turn goes.
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Hearing you…", { timeout: 10_000 });
    await expect(page.getByTestId("voice-dock")).toHaveAttribute("data-phase", "pausing", { timeout: 5_000 });

    // No tap: 1.5 s of sound, then 1.2 s of quiet sends it.
    const req = await asrReq;
    expect(req.postDataBuffer()?.subarray(0, 4).toString("ascii")).toBe("RIFF");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
  });

  test("voice mode closes a mic that heard nothing, says why, and sends nothing", async ({ page }) => {
    test.slow(); // waits out the 8 s of silence
    await mockBackend(page);
    const sent: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/asr") || r.url().includes("/api/v1/chat")) sent.push(r.url());
    });
    await page.goto("/");

    await page.locator('[data-testid="composer-voicemode"]').click();
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Listening", { timeout: 10_000 });

    // Chromium's fake device is silence with a short beep every 500 ms: nothing
    // a person said, so after 8 s the mic closes by itself and says why.
    await expect(page.getByTestId("voice-dock-label")).toHaveText("Tap the mic to talk", { timeout: 15_000 });
    await expect(page.getByTestId("voice-dock-detail")).toContainText("I didn’t hear anything");
    expect(sent).toEqual([]);
  });

  test("voice mode listens again once the reply has been read", async ({ page }) => {
    test.slow(); // two spoken turns
    await speakThenPause(page);
    await mockBackend(page);
    const sent: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/v1/asr")) sent.push(r.url());
    });
    await page.goto("/");

    await page.locator('[data-testid="composer-voicemode"]').click();
    // The first turn sends itself; its reply is read (one short piece); then the
    // mic opens again with no tap, and the second turn sends itself too.
    await expect.poll(() => sent.length, { timeout: 30_000 }).toBeGreaterThanOrEqual(2);
  });

  test("opening the mic silences a reply being read aloud", async ({ page }) => {
    await page.addInitScript(() => {
      const stop = AudioBufferSourceNode.prototype.stop;
      AudioBufferSourceNode.prototype.stop = function (...args) {
        const w = window as unknown as { __stopped?: number };
        w.__stopped = (w.__stopped ?? 0) + 1;
        return stop.apply(this, args);
      };
    });
    await withoutSpeechApi(page);
    await mockBackend(page, { reply: "The standard VAT rate in Uganda is 18%." });
    // A reply that takes 20 s to read.
    await page.route("**/api/v1/tts/stream", (route) =>
      route.fulfill({
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ seq: 0, text: "long", format: "wav", audio_base64: silentWavB64(20), duration_s: 20 })}\n${JSON.stringify({ done: true, pieces: 1, failed: 0 })}\n`,
      }),
    );
    await page.goto("/");
    await sendMessage(page, "What is the VAT rate?");
    await expect(page.locator(".message-row-assistant").last()).toContainText("18%", {
      timeout: 15_000,
    });
    await page.getByRole("button", { name: /Listen in English/ }).last().click();
    await expect(page.getByRole("button", { name: "Stop listening" }).last()).toBeVisible();

    // Dictate over it: the reply stops at once, rather than being recorded too.
    await page.getByRole("button", { name: "Start speaking" }).click();
    await expect(page.getByRole("button", { name: "Stop and insert text" })).toBeVisible({ timeout: 10_000 });
    expect(await page.evaluate(() => (window as unknown as { __stopped?: number }).__stopped ?? 0)).toBeGreaterThan(0);
  });

  test("dictation still works with no browser Speech API", async ({ page }) => {
    test.slow(); // real capture window
    await withoutSpeechApi(page);
    await mockBackend(page, { transcript: "how do I register for a TIN" });
    await page.goto("/");
    await assertNoSpeechApi(page);

    // Not in voice mode: this is dictation, which fills the composer rather
    // than sending a turn.
    const mic = page.getByRole("button", { name: "Start speaking" });
    await expect(mic).toBeEnabled();
    await mic.click();

    // Recording swaps the whole composer for the waveform panel — there is no
    // mic button to re-press, which is what the first version of this test
    // waited for. The checkmark is the stop control, and on this path it is
    // labelled for what it actually does.
    const stop = page.getByRole("button", { name: "Stop and insert text" });
    await expect(stop).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("Listening...")).toBeVisible();
    await page.waitForTimeout(1200); // let the fake device feed the recorder
    await stop.click();

    // The transcript lands in the textarea, and no turn was sent.
    await expect(page.getByLabel("Type your message")).toHaveValue(/TIN/i, { timeout: 15_000 });
    await expect(page.locator(".message-row-user")).toHaveCount(0);
  });

  test("dictation appends to what is already typed", async ({ page }) => {
    test.slow();
    await withoutSpeechApi(page);
    await mockBackend(page, { transcript: "and what about VAT" });
    await page.goto("/");
    await assertNoSpeechApi(page);

    const box = page.getByLabel("Type your message");
    await box.fill("I registered last year");
    await page.getByRole("button", { name: "Start speaking" }).click();
    const stop = page.getByRole("button", { name: "Stop and insert text" });
    await expect(stop).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(1200);
    await stop.click();

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
