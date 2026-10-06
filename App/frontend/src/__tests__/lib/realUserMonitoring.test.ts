import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Metric } from "web-vitals";

import { ANALYTICS_CONSENT_KEY } from "@/lib/analyticsConsent";
import {
  ERRORS_ENDPOINT,
  installGlobalErrorHandlers,
  reportClientError,
  resetClientErrorsForTests,
} from "@/lib/client-errors";
import { flushVitals, recordVital, resetVitalsForTests, toReport, VITALS_ENDPOINT } from "@/lib/web-vitals";

function metric(name: Metric["name"], value: number, rating: Metric["rating"] = "good"): Metric {
  return {
    name,
    value,
    rating,
    delta: value,
    id: `v-${name}`,
    navigationId: "nav-1",
    entries: [],
    navigationType: "navigate",
  } as unknown as Metric;
}

describe("web vitals reporting", () => {
  beforeEach(() => {
    resetVitalsForTests();
    localStorage.clear();
  });

  it("rounds milliseconds and keeps CLS as a score", () => {
    expect(toReport(metric("LCP", 1234.56), "/")).toMatchObject({ name: "LCP", value: 1235, route: "/" });
    expect(toReport(metric("CLS", 0.123456), "/").value).toBe(0.1235);
  });

  it("sends nothing without analytics consent", () => {
    const transport = vi.fn();
    recordVital(metric("INP", 180), "/");
    flushVitals(transport);
    expect(transport).not.toHaveBeenCalled();
  });

  it("batches what was measured once consent is given", () => {
    localStorage.setItem(ANALYTICS_CONSENT_KEY, "true");
    const transport = vi.fn();
    recordVital(metric("INP", 180), "/staff");
    recordVital(metric("CLS", 0.02), "/staff");
    flushVitals(transport);
    expect(transport).toHaveBeenCalledTimes(1);
    const body = JSON.parse(transport.mock.calls[0][0]);
    expect(body.metrics.map((m: { name: string }) => m.name)).toEqual(["INP", "CLS"]);
    expect(Object.keys(body.metrics[0]).sort()).toEqual(["name", "navigation_type", "rating", "route", "value"]);
    flushVitals(transport);
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it("posts to the API's vitals endpoint through the /api rewrite", () => {
    expect(VITALS_ENDPOINT).toBe("/api/v1/telemetry/vitals");
  });
});

describe("client error reporting", () => {
  const beacon = vi.fn((_url: string, _data?: BodyInit | null) => true);

  beforeEach(() => {
    resetClientErrorsForTests();
    beacon.mockClear();
    // setup.ts stubs sendBeacon as writable (not configurable): assign, don't redefine.
    (navigator as unknown as { sendBeacon: typeof beacon }).sendBeacon = beacon;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  async function sentBody(): Promise<Record<string, string>> {
    const blob = beacon.mock.calls[0][1] as Blob;
    return JSON.parse(await blob.text());
  }

  it("reports the class, digest and route but never the message", async () => {
    reportClientError("boundary", new TypeError("cannot read user-typed-detail"), "dg-1");
    expect(beacon).toHaveBeenCalledWith(ERRORS_ENDPOINT, expect.any(Blob));
    const body = await sentBody();
    expect(body).toEqual({ kind: "boundary", name: "TypeError", digest: "dg-1", source: "", route: "/" });
    expect(JSON.stringify(body)).not.toContain("user-typed-detail");
  });

  it("sends each distinct error once and caps a page's reports", () => {
    const err = new Error("x");
    reportClientError("error", err);
    reportClientError("error", err);
    expect(beacon).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 20; i += 1) reportClientError("error", new Error(`e${i}`), `d${i}`);
    expect(beacon).toHaveBeenCalledTimes(10);
  });

  it("listens for uncaught errors and rejections until uninstalled", () => {
    const uninstall = installGlobalErrorHandlers();
    window.dispatchEvent(new ErrorEvent("error", { error: new RangeError("r"), filename: "https://x/app.js?v=1", lineno: 3, colno: 9 }));
    expect(beacon).toHaveBeenCalledTimes(1);
    uninstall();
    // No `error` object: with no listener left, Vitest rethrows an ErrorEvent
    // that carries one as an uncaught exception and fails the run.
    window.dispatchEvent(new ErrorEvent("error", { message: "s", filename: "https://x/other.js", lineno: 1, colno: 1 }));
    expect(beacon).toHaveBeenCalledTimes(1);
  });
});
