/**
 * The service worker must not touch cross-origin requests.
 *
 * Its "network-first for pages" branch had no origin check, so a cross-origin
 * GET — in production, the OIDC discovery document — fell into it and broke
 * sign-in three ways at once: re-issued from the worker's context (where a
 * service worker keeps the CSP it was *installed* with, so an older
 * `connect-src 'self'` blocked it while the page's current CSP allowed it),
 * written into the app cache, and on failure answered with /offline.html —
 * handing an OIDC client HTML where it expected JSON.
 *
 * Reported from a real browser console:
 *   sw.js:58 Connecting to 'https://…auth0.com/.well-known/openid-configuration'
 *   violates the following Content Security Policy directive: "connect-src 'self'"
 *
 * public/sw.js is plain script, not a module, so it is read and evaluated here
 * against a fake ServiceWorkerGlobalScope. That is deliberate: the file ships
 * verbatim to browsers, so the test has to exercise the shipped text rather
 * than an importable copy that could drift from it.
 */
import { describe, it, expect, beforeAll, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createContext, runInContext } from "node:vm";

type FetchHandler = (event: {
  request: { url: string; method: string };
  respondWith: (r: unknown) => void;
}) => void;

let fetchHandler: FetchHandler;
let installHandler: unknown;

beforeAll(() => {
  const source = readFileSync(join(process.cwd(), "public/sw.js"), "utf8");
  const listeners: Record<string, unknown> = {};
  const scope = {
    addEventListener: (type: string, fn: unknown) => {
      listeners[type] = fn;
    },
    location: { origin: "https://landwind22-ura-chatbot.hf.space" },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
    caches: {
      open: vi.fn(async () => ({ addAll: vi.fn(), put: vi.fn() })),
      keys: vi.fn(async () => []),
      match: vi.fn(async () => undefined),
      delete: vi.fn(),
    },
    // Must resolve to something Response-shaped: both branches call
    // .then(resp => resp.clone()) on it.
    fetch: vi.fn(async () => ({ clone: () => ({}) })),
    URL,
    Promise,
  };
  // node:vm rather than new Function(): the repo's semgrep rules block the
  // Function constructor outright (OWASP LLM05), and a real sandbox is the
  // better tool for evaluating a script against a synthetic global anyway.
  const context = createContext({ ...scope, self: undefined, console });
  context.self = context;
  runInContext(source, context);
  fetchHandler = listeners.fetch as FetchHandler;
  installHandler = listeners.install;
});

/** Returns true when the worker claimed the request via respondWith(). */
function handled(url: string, method = "GET"): boolean {
  let claimed = false;
  fetchHandler({
    request: { url, method },
    respondWith: () => {
      claimed = true;
    },
  });
  return claimed;
}

describe("service worker request routing", () => {
  it("registers install and fetch handlers", () => {
    expect(installHandler).toBeTypeOf("function");
    expect(fetchHandler).toBeTypeOf("function");
  });

  it("does not intercept the OIDC discovery document", () => {
    // The exact request from the production console report.
    expect(
      handled("https://dev-s16d7m00eyrksjy2.us.auth0.com/.well-known/openid-configuration"),
    ).toBe(false);
  });

  it("does not intercept any cross-origin GET", () => {
    for (const url of [
      "https://dev-s16d7m00eyrksjy2.us.auth0.com/authorize",
      "https://dev-s16d7m00eyrksjy2.us.auth0.com/oauth/token",
      "https://example.com/anything.json",
    ]) {
      expect(handled(url), url).toBe(false);
    }
  });

  it("still leaves same-origin API calls to the network", () => {
    expect(handled("https://landwind22-ura-chatbot.hf.space/api/v1/chat", "POST")).toBe(false);
    expect(handled("https://landwind22-ura-chatbot.hf.space/api/v1/speech/voices")).toBe(false);
  });

  it("still handles same-origin pages and static assets", () => {
    expect(handled("https://landwind22-ura-chatbot.hf.space/")).toBe(true);
    expect(handled("https://landwind22-ura-chatbot.hf.space/signin")).toBe(true);
    expect(
      handled("https://landwind22-ura-chatbot.hf.space/_next/static/chunks/main.js"),
    ).toBe(true);
  });
});
