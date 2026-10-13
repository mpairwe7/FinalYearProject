/**
 * A rating carries the journey step that produced the reply, so the analytics
 * journey panel can show which step taxpayers found least helpful. The API
 * accepts identifiers only and refuses the whole rating otherwise, so a
 * malformed value must be dropped here rather than cost the taxpayer's rating.
 *
 * It also names the answer it rates — conversation, turn id, position — so the
 * service-quality audit can find where a conversation went wrong; and it is
 * sent without analytics consent too, just without the analytics session id.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const consent = vi.hoisted(() => ({ granted: true }));
vi.mock("../../lib/analyticsConsent", () => ({ hasAnalyticsConsent: () => consent.granted }));

import { submitFeedback } from "../../store/useAnalyticsStore";

let bodies: Record<string, unknown>[];
let headers: Record<string, string>[];

beforeEach(() => {
  consent.granted = true;
  bodies = [];
  headers = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init: RequestInit = {}) => {
      bodies.push(JSON.parse(String(init.body)));
      headers.push(Object.fromEntries(new Headers(init.headers).entries()));
      return { ok: true, status: 200, json: async () => ({ id: "fb-1" }) } as Response;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("submitFeedback context", () => {
  it("sends the route and journey step with the rating", async () => {
    await submitFeedback("m1", "down", "", "How do I file?", "Step 1", {
      retrievalMode: "workflow",
      workflowId: "return_filing",
      stepId: "collect_taxpayer_type",
    });
    expect(bodies[0]).toMatchObject({
      rating: "down",
      retrieval_mode: "workflow",
      workflow_id: "return_filing",
      step_id: "collect_taxpayer_type",
    });
  });

  it("drops a malformed identifier instead of losing the rating", async () => {
    await submitFeedback("m2", "up", "", "", "", { workflowId: "Return Filing!", stepId: "ok_step" });
    expect(bodies[0]).toMatchObject({ rating: "up", workflow_id: "", step_id: "ok_step" });
  });

  it("sends empty context when the reply had none", async () => {
    await submitFeedback("m3", "up");
    expect(bodies[0]).toMatchObject({ retrieval_mode: "", workflow_id: "", step_id: "" });
  });

  it("names the answer it rates: conversation, turn and position", async () => {
    await submitFeedback("m4", "down", "", "Does it apply to exports?", "Exports are zero-rated.", {
      conversationId: "conv-abc_123",
      turnId: "0f3c9a",
      answerNumber: 2,
    });
    expect(bodies[0]).toMatchObject({ conversation_id: "conv-abc_123", turn_id: "0f3c9a", turn_index: 2 });
  });

  it("drops a malformed conversation id or position rather than the rating", async () => {
    await submitFeedback("m5", "up", "", "", "", { conversationId: "conv with spaces", answerNumber: 0 });
    expect(bodies[0]).toMatchObject({ rating: "up", conversation_id: "", turn_index: null });
  });

  it("rates without analytics consent, but without the analytics session id", async () => {
    consent.granted = false;
    const result = await submitFeedback("m6", "up", "", "", "", { conversationId: "conv-1" });
    expect(result).toEqual({ id: "fb-1" });
    expect(bodies[0]).toMatchObject({ rating: "up", session_id: null, conversation_id: "conv-1" });
    expect(headers[0]["x-session-id"]).toBeUndefined();
  });
});
