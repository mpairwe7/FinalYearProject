/**
 * URA Tax Assistant — Service Worker (Cache-First for assets, Network-First for API)
 *
 * 2026 production pattern: lightweight hand-rolled SW instead of Workbox/next-pwa
 * to keep the bundle minimal and avoid build-tool coupling.
 */

// v5: drops entries cached before cross-origin requests were excluded.
const CACHE_NAME = 'ura-v5';
const OFFLINE_PAGE = '/offline.html';
const STATIC_ASSETS = ['/', '/manifest.json', '/favicon.svg', '/icon-192x192.svg', '/icon-512x512.svg', OFFLINE_PAGE];

// Install — pre-cache shell assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate — clean old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch — network-first for API, cache-first for static assets
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET and API requests (always go to network)
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/')) {
    return;
  }

  // Cross-origin requests are not ours to serve. Returning without calling
  // respondWith() hands them back to the browser untouched.
  //
  // Without this they fell through to the network-first branch below, which
  // broke sign-in three separate ways. The OIDC discovery document is a
  // cross-origin GET to the identity provider, so it was:
  //   1. re-issued from the service worker's context, and a service worker
  //      keeps the CSP it was installed with — an older one, from before the
  //      provider's origin was added to connect-src, blocks it with
  //      "violates ... connect-src 'self'" while the page's own current CSP
  //      allows it. Observed in production.
  //   2. written into the app cache, which is no place for an IdP's metadata.
  //   3. on any failure, answered from cache and ultimately with
  //      /offline.html — so the OIDC client received an HTML page where it
  //      expected JSON, and reported a parse error rather than a network one.
  if (url.origin !== self.location.origin) {
    return;
  }

  // Cache-first for static assets (JS, CSS, fonts, icons)
  if (
    url.pathname.startsWith('/_next/static/') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.woff2') ||
    url.pathname === '/manifest.json'
  ) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request).then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return resp;
      }))
    );
    return;
  }

  // Network-first for pages (HTML)
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return resp;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || caches.match(OFFLINE_PAGE)))
  );
});
