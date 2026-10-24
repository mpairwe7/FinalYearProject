#!/usr/bin/env node
// Live check of the chat's voice mode (ADR-0016) against a running stack.
//
// Drives real Chromium through one spoken turn: a WAV file stands in for the
// microphone (make it with scripts/make_probe_mic_wav.py), the frontend proxies
// /api to a real api with speech, an LLM and TTS. Records every phase the voice
// dock shows, with a screenshot of each, plus the /v1/asr → /v1/chat/stream →
// /v1/tts/stream timeline. See docs/runbooks/chat-voice-mode-live-check.md.
//
//   node scripts/probe_voice_mode.mjs --base http://localhost:3277 \
//     --mic /path/question.wav --out /path/out [--language Luganda] [--device phone]
//
// Exits 1 when a phase is skipped, when what was heard did not reach the
// screen before the answer did, or when the answer is the stream's error text.
import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, i, all) => (i % 2 ? pairs : [...pairs, [value.replace(/^--/, ""), all[i + 1]]]), []),
);
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendDir = args.frontend ?? path.join(repo, "App/frontend");
const { base, mic, out } = args;
if (!base || !mic || !out) {
  console.error("usage: probe_voice_mode.mjs --base <url> --mic <wav> --out <dir> [--language <label>] [--device phone]");
  process.exit(2);
}
const require = createRequire(path.join(frontendDir, "package.json"));
const { chromium, devices } = require("playwright");

mkdirSync(out, { recursive: true });
const t0 = Date.now();
const timeline = [];
const note = (what, extra = {}) => timeline.push({ ms: Date.now() - t0, what, ...extra });

const browser = await chromium.launch({
  headless: true,
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${path.resolve(mic)}`,
    "--autoplay-policy=no-user-gesture-required",
    // Single-process: the only mode Chromium renders in on the dev box.
    ...(process.env.PW_SINGLE_PROCESS ? ["--no-sandbox", "--single-process", "--no-zygote"] : []),
  ],
});
const context = await browser.newContext({
  ...(args.device === "phone" ? devices["Pixel 7"] : { viewport: { width: 1280, height: 860 } }),
  permissions: ["microphone"],
});
await context.addInitScript(() => {
  try {
    window.localStorage.setItem("ura_analytics_consent", "false");
    window.localStorage.removeItem("ura-chat-store");
  } catch {}
});
const page = await context.newPage();

const watched = /\/api\/v1\/(asr|chat\/stream|tts\/stream)$/;
page.on("request", (req) => {
  const url = new URL(req.url());
  if (!watched.test(url.pathname)) return;
  const entry = { path: url.pathname };
  if (url.pathname.endsWith("/asr")) entry.query = Object.fromEntries(url.searchParams);
  if (url.pathname.endsWith("/chat/stream")) {
    const body = req.postDataJSON();
    entry.body = { message: body.message, locale: body.locale, locale_explicit: body.locale_explicit };
  }
  note(`→ ${url.pathname}`, entry);
});
page.on("requestfinished", (req) => {
  const url = new URL(req.url());
  if (watched.test(url.pathname)) note(`✓ ${url.pathname}`);
});
page.on("response", async (res) => {
  if (!new URL(res.url()).pathname.endsWith("/v1/asr")) return;
  const result = await res.json().catch(() => ({}));
  note("asr result", { text: result.text, language: result.language, backend: result.backend, latency_s: result.latency_s });
});

await page.goto(base, { waitUntil: "networkidle" });
if (args.language) {
  await page.locator(".langsel-btn").click();
  await page.getByRole("radio", { name: new RegExp(args.language) }).click();
}
note("enter voice mode");
await page.locator('[data-testid="composer-voicemode"]').click();

const seen = [];
let lastKey = "";
let spoke = false;
let heardShownAt = null;
let answerShownAt = null;
const deadline = Date.now() + 150_000;
while (Date.now() < deadline) {
  const state = await page.evaluate(() => {
    const dock = document.querySelector('[data-testid="voice-dock"]');
    const rows = [...document.querySelectorAll(".message-row")];
    const last = rows.at(-1);
    return {
      phase: dock?.getAttribute("data-phase") ?? null,
      label: dock?.querySelector('[data-testid="voice-dock-label"]')?.textContent ?? "",
      detail: dock?.querySelector('[data-testid="voice-dock-detail"]')?.textContent ?? "",
      orb: dock?.querySelector(".call-orb")?.getAttribute("data-state") ?? null,
      announced: dock?.querySelector('[role="status"]')?.textContent ?? "",
      spokenTurn: Boolean(document.querySelector(".message-row-user .msg-spoken-tag")),
      answerText: last?.classList.contains("message-row-assistant")
        ? (last.querySelector(".msg-content")?.textContent ?? "").trim()
        : "",
    };
  });
  if (!state.phase) break;
  if (state.spokenTurn && heardShownAt === null) heardShownAt = Date.now() - t0;
  if (state.answerText && state.spokenTurn && answerShownAt === null) answerShownAt = Date.now() - t0;
  const key = `${state.phase}|${state.label}|${state.detail}`;
  if (key !== lastKey) {
    lastKey = key;
    note(`phase ${state.phase}`, { label: state.label, detail: state.detail, orb: state.orb, announced: state.announced });
    if (!seen.includes(state.phase)) {
      seen.push(state.phase);
      await page.screenshot({ path: path.join(out, `${String(seen.length).padStart(2, "0")}-${state.phase}.png`) });
    }
  }
  if (state.phase === "speaking") spoke = true;
  // The floor came back after the answer was read: one whole turn.
  if (spoke && ["listening", "opening", "idle"].includes(state.phase)) break;
  await page.waitForTimeout(100);
}

const conversation = await page.evaluate(() =>
  [...document.querySelectorAll(".message-row")].map((row) => ({
    role: row.classList.contains("message-row-user") ? "user" : "assistant",
    spoken: Boolean(row.querySelector(".msg-spoken-tag")),
    text: (row.querySelector(".msg-content")?.textContent ?? "").trim().slice(0, 600),
  })),
);
await page.screenshot({ path: path.join(out, "99-after-turn.png") });
await page.getByTestId("voice-dock-end").click().catch(() => {});
await browser.close();

const expected = ["listening", "hearing", "pausing", "transcribing", "thinking", "speaking"];
const missing = expected.filter((phase) => !seen.includes(phase));
const answer = conversation.filter((turn) => turn.role === "assistant").at(-1)?.text ?? "";
const problems = [
  ...missing.map((phase) => `phase never shown: ${phase}`),
  ...(heardShownAt === null ? ["what was heard never reached the screen"] : []),
  ...(heardShownAt !== null && answerShownAt !== null && heardShownAt > answerShownAt ? ["the answer arrived before what was heard"] : []),
  ...(/an error occurred/i.test(answer) ? [`the answer is the stream's error text: ${answer}`] : []),
];
const summary = { phasesSeen: seen, heardShownAtMs: heardShownAt, answerShownAtMs: answerShownAt, conversation, problems, timeline };
writeFileSync(path.join(out, "timeline.json"), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ phasesSeen: seen, heardShownAtMs: heardShownAt, answerShownAtMs: answerShownAt, conversation, problems }, null, 2));
process.exit(problems.length ? 1 : 0);
