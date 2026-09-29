/**
 * A rating carries the journey step that produced the reply, so the analytics
 * journey panel can show which step taxpayers found least helpful. The API
 * accepts identifiers only and refuses the whole rating otherwise, so a
 * malformed value must be dropped here rather than cost the taxpayer's rating.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/analyticsConsent", () => ({ hasAnalyticsConsent: () => true }));

import { submitFeedback } from "../../store/useAnalyticsStore";

let bodies: Record<string, unknown>[];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init: RequestInit = {}) => {
      bodies.push(JSON.parse(String(init.body)));
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
});
