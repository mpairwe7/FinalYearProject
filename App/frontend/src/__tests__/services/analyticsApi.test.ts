/**
 * The admin API client's two translations.
 *
 * Both are places where the console's vocabulary and the API's differ, and
 * both are silent when wrong: sending "any" as a status is a 400, and a flag
 * reset that never issues a DELETE looks exactly like a reset that worked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { analyticsApi } from "../../services/analyticsApi";

function captureFetch() {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(input), init });
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => ({}),
      } as Response;
    }),
  );
  return calls;
}

describe("analyticsApi", () => {
  let calls: { url: string; init: RequestInit }[];

  beforeEach(() => {
    calls = captureFetch();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the queue's 'any' status as an absent filter", async () => {
    await analyticsApi.tickets("any", 20);
    // The backend validates against the four real statuses and 400s on
    // anything else; an absent status is how it expresses "all".
    expect(calls[0].url).toContain("status=&");
    expect(calls[0].url).not.toContain("status=any");
  });

  it("passes a real status through untouched", async () => {
    await analyticsApi.tickets("assigned", 20, "urgent", "customs");
    expect(calls[0].url).toContain("status=assigned");
    expect(calls[0].url).toContain("priority=urgent");
    expect(calls[0].url).toContain("team=customs");
  });

  it("clears a flag override with DELETE, not another PATCH", async () => {
    await analyticsApi.clearFlag("hyde");
    // Setting the flag back to its default value leaves the override in place
    // and still shadowing FLAG_*; only the delete removes it.
    expect(calls[0].init.method).toBe("DELETE");
    expect(calls[0].url).toBe("/api/v1/admin/flags/hyde");
  });

  it("still toggles with PATCH and the enabled query parameter", async () => {
    await analyticsApi.setFlag("hyde", true);
    expect(calls[0].init.method).toBe("PATCH");
    expect(calls[0].url).toBe("/api/v1/admin/flags/hyde?enabled=true");
  });
});
