/**
 * Shared E2E helpers. Not a spec (no `.spec.ts`), so Playwright won't collect it.
 *
 * The app calls the backend same-origin at `/api/*` (rewritten to FastAPI by
 * next.config.mjs), so E2E is decoupled from the real backend by intercepting
 * `**​/api/**` with page.route. The analytics consent banner (role=alertdialog)
 * overlays on first load and can intercept clicks, so tests seed a decision.
 */
import type { Page } from "@playwright/test";

/** Seed an analytics-consent decision so the banner never renders. */
export async function seedConsent(page: Page, value: "granted" | "denied" = "denied") {
  await page.addInitScript((v) => {
    try {
      window.localStorage.setItem("ura_analytics_consent", v as string);
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
  opts: { reply?: string; escalate?: boolean } = {},
) {
  const reply = opts.reply ?? "The standard VAT rate in Uganda is 18%.";

  // Catch-all fallback for any /api call (registered first → lowest priority).
  await page.route("**/api/**", (route) => route.fulfill({ json: {} }));

  await page.route("**/api/v1/speech/health", (route) =>
    route.fulfill({ json: { status: "available", backend: "stub" } }),
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
