/**
 * Shared E2E helpers. Not a spec (no `.spec.ts`), so Playwright won't collect it.
 *
 * The app calls the backend same-origin at `/api/*` (rewritten to FastAPI by
 * next.config.mjs), so E2E is decoupled from the real backend by intercepting
 * `**​/api/**` with page.route. The analytics consent banner (role=alertdialog)
 * overlays on first load and can intercept clicks, so tests seed a decision.
 */
import { expect, type Page } from "@playwright/test";

import { TINY_WAV_B64 } from "./fixtures";

/**
 * Assert the auth call-to-action is coherent, whichever way the target is wired.
 *
 * CI runs without NEXT_PUBLIC_OIDC_*, so the button has to say "Identity
 * provider not configured" and stay disabled rather than redirect into a URL
 * built from empty strings. A real deployment has an IdP, so the same button
 * has to be live and labelled with the action.
 *
 * Both specs that use this asserted only the CI shape, which meant a clean run
 * against the deployed Space reported two failures for a reason that was not a
 * defect — the deployment simply had OIDC configured. Skipping there would have
 * traded a false alarm for a blind spot; branching keeps a real assertion on
 * both. Only the disabled state has a fixed label, so callers pass the live one.
 */
export async function expectAuthCta(page: Page, liveName: RegExp) {
  const unconfigured = page.getByRole("button", { name: /Identity provider not configured/ });
  const live = page.getByRole("button", { name: liveName });

  await expect(unconfigured.or(live).first()).toBeVisible();
  if ((await unconfigured.count()) > 0) {
    await expect(unconfigured).toBeDisabled();
  } else {
    await expect(live).toBeEnabled();
  }
}

/**
 * Open Settings through the header's overflow menu — the one entry point that
 * exists at every width.
 *
 * Not the sidebar's account block, which is what account-settings.spec.ts uses
 * and why that spec is pinned to chromium: below 1024px the rail is a drawer
 * parked off-screen at x=-312, and clicking it times out.
 *
 * The trap is that `isVisible()` returns **true** for it there. The drawer is
 * moved with `transform: translateX(-100%)`, not `display:none`, so the button
 * keeps a bounding box and Playwright counts it visible while it can never be
 * clicked. Branching on isVisible() therefore picks the unreachable path on a
 * phone and fails exactly as if there were no branch — which it did. Using the
 * kebab unconditionally needs no probe, and it is a real path on desktop too.
 */
export async function openSettings(page: Page) {
  await page.getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page.getByRole("dialog", { name: "Settings" })).toBeVisible();
}

/** Seed an analytics-consent decision so the banner never renders.
 * The store reads "true"/"false" (see lib/analyticsConsent); anything else is
 * treated as undecided, so the banner would still show. */
export async function seedConsent(page: Page, value: "granted" | "denied" = "denied") {
  await page.addInitScript((v) => {
    try {
      window.localStorage.setItem("ura_analytics_consent", v === "granted" ? "true" : "false");
    } catch {
      /* ignore */
    }
  }, value);
}

/** Clear persisted chat history for a deterministic landing state. */
export async function clearChatStore(page: Page) {
  await page.addInitScript(() => {
    try {
      window.localStorage.removeItem("ura-chat-store");
    } catch {
      /* ignore */
    }
  });
}

function sseReply(text: string, opts: { escalate?: boolean } = {}): string {
  return [
    "event: metadata",
    `data: ${JSON.stringify({ sources: ["vat.pdf"], citations: [], conversation_id: "c-e2e" })}`,
    "",
    "event: token",
    `data: ${text}`,
    "",
    "event: grounding",
    `data: ${JSON.stringify({
      faithfulness_score: 0.9,
      escalation_required: !!opts.escalate,
      escalation_reason: opts.escalate ? "dispute" : "",
    })}`,
    "",
    "event: done",
    "data: {}",
    "",
    "",
  ].join("\n");
}

/**
 * Mock the backend. Registers a catch-all FIRST so the specific routes added
 * afterwards take precedence (Playwright matches most-recently-added first).
 */
export async function mockBackend(
  page: Page,
  opts: { reply?: string; escalate?: boolean; transcript?: string; ttsEnabled?: boolean } = {},
) {
  const reply = opts.reply ?? "The standard VAT rate in Uganda is 18%.";

  // Catch-all fallback for any /api call (registered first → lowest priority).
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));

  await page.route("**/api/v1/speech/health", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        enabled: true,
        asr_backend: "stub",
        tts_backend: "stub",
        mt_backend: "stub",
      },
    }),
  );
  // --- Voice/speech endpoints (STT/TTS/MT) ------------------------------------
  // The real client posts raw audio to /v1/voice/chat and /v1/asr, and JSON to
  // /v1/tts and /v1/translate. TTS payloads must be a *decodable* WAV (the client
  // runs AudioContext.decodeAudioData), hence TINY_WAV_B64.
  await page.route("**/api/v1/voice/chat**", (route) =>
    route.fulfill({
      json: {
        transcript: opts.transcript ?? "what is the standard VAT rate in Uganda",
        transcript_language: "en",
        conversation_id: "c-e2e",
        reply,
        reply_audio_base64: opts.ttsEnabled === false ? "" : TINY_WAV_B64,
        sample_rate: 22050,
        duration_s: 0.05,
        sources: ["vat.pdf"],
        citations: [],
        faithfulness_score: 0.9,
        retrieval_mode: "hybrid",
        asr_latency_s: 0.1,
        mt_latency_s: 0.0,
        llm_latency_s: 0.2,
        tts_latency_s: 0.1,
        total_latency_s: 0.4,
        asr_backend: "stub",
        tts_backend: "stub",
        mt_backend: "stub",
        error: null,
      },
    }),
  );
  await page.route("**/api/v1/tts", (route) =>
    route.fulfill({
      json: {
        sample_rate: 22050,
        num_samples: 1102,
        duration_s: 0.05,
        latency_s: 0.1,
        backend: "stub",
        voice: "en_US-stub",
        audio_base64: TINY_WAV_B64,
        error: null,
      },
    }),
  );
  await page.route("**/api/v1/asr**", (route) =>
    route.fulfill({
      json: {
        text: opts.transcript ?? "what is the standard VAT rate in Uganda",
        language: "en",
        duration_s: 0.05,
        latency_s: 0.1,
        rtf: 2.0,
        backend: "stub",
        error: null,
      },
    }),
  );
  await page.route("**/api/v1/translate", (route) =>
    route.fulfill({
      json: {
        text: "VAT mu Uganda eri ku 18%.",
        source_lang: "en",
        target_lang: "lg",
        latency_s: 0.1,
        backend: "stub",
        error: null,
      },
    }),
  );
  await page.route("**/api/v1/chat/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream; charset=utf-8" },
      body: sseReply(reply, { escalate: opts.escalate }),
    }),
  );
  // Non-stream fallback the app uses if the stream is not-ok/empty.
  await page.route("**/api/v1/chat", (route) =>
    route.fulfill({
      json: {
        reply,
        sources: ["vat.pdf"],
        citations: [],
        faithfulness_score: 0.9,
        retrieval_mode: "hybrid",
        model: "stub",
        conversation_id: "c-e2e",
        locale: "en",
        escalation_required: !!opts.escalate,
        escalation_reason: opts.escalate ? "dispute" : "",
        agent_role: "rag_answerer",
        ticket_id: "",
        next_actions: [],
      },
    }),
  );
  await page.route("**/api/v1/feedback**", (route) => route.fulfill({ json: { ok: true } }));
  await page.route("**/api/v1/analytics/**", (route) =>
    route.fulfill({
      json: {
        total_conversations: 42,
        period_days: 7,
        topics: [],
        latency: { p50_ms: 120, p95_ms: 400 },
        feedback: { up: 10, down: 2 },
        slo: {},
        tickets: {},
        retrieval_modes: {},
        segments: [],
      },
    }),
  );
}

/** Type a message into the composer and send it. */
export async function sendMessage(page: Page, text: string) {
  await page.getByLabel("Type your message").fill(text);
  await page.getByLabel("Send message").click();
}
