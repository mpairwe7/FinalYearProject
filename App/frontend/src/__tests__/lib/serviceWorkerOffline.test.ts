/**
 * The offline page answers a failed page navigation, and nothing else
 * (CodeRabbit on PR 727).
 *
 * The network-first branch answered every failed same-origin GET outside
 * /api/ with /offline.html when nothing was cached, so a fetch() for a data
 * file could receive an HTML page where it expected JSON. A navigation is the
 * only request a page can stand in for; anything else fails as a network error.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { createContext, runInContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';

type Handler = (event: {
  request: { url: string; method: string; mode?: string };
  preloadResponse?: Promise<unknown>;
  respondWith: (r: Promise<unknown>) => void;
}) => void;

function offlineWorker(): Handler {
  const listeners: Record<string, unknown> = {};
  const context = createContext({
    addEventListener: (type: string, fn: unknown) => {
      listeners[type] = fn;
    },
    location: { origin: 'https://landwind22-ura-chatbot.hf.space' },
    skipWaiting: vi.fn(),
    clients: { claim: vi.fn() },
    caches: {
      open: vi.fn(async () => ({ addAll: vi.fn(), put: vi.fn() })),
      keys: vi.fn(async () => []),
      match: vi.fn(async (req: unknown) => (req === '/offline.html' ? 'OFFLINE_PAGE' : undefined)),
      delete: vi.fn(),
    },
    fetch: vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }),
    Response: { error: () => 'NETWORK_ERROR' },
    URL,
    Promise,
    console,
  });
  context.self = context;
  runInContext(readFileSync(join(process.cwd(), 'public/sw.js'), 'utf8'), context);
  return listeners.fetch as Handler;
}

async function answer(handler: Handler, url: string, mode: string): Promise<unknown> {
  let response: Promise<unknown> | undefined;
  handler({ request: { url, method: 'GET', mode }, respondWith: (r) => (response = r) });
  return response;
}

describe('service worker offline fallback', () => {
  const origin = 'https://landwind22-ura-chatbot.hf.space';

  it('answers a failed navigation with the offline page', async () => {
    expect(await answer(offlineWorker(), `${origin}/`, 'navigate')).toBe('OFFLINE_PAGE');
  });

  it('lets a failed data request fail instead of handing it HTML', async () => {
    expect(await answer(offlineWorker(), `${origin}/data/rates.json`, 'cors')).toBe('NETWORK_ERROR');
    expect(await answer(offlineWorker(), `${origin}/robots.txt`, 'no-cors')).toBe('NETWORK_ERROR');
  });
});
