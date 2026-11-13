# Runbook: Offline & Low-Bandwidth Connectivity

**Scope:** Frontend PWA & service worker, offline fallback page, low-bandwidth network detection, and backend response compression.  
**Audience:** Frontend and backend engineers, SREs, QA engineers.  
**Updated:** 13 November 2026 (G618, ADR-0047: no tax figure offline)

---

## 1. Context & Objectives

In Uganda, a substantial segment of taxpayers—particularly in the informal sector, regional border posts (e.g., Malaba, Busia, Mutukula), and rural agricultural districts—access digital revenue services over constrained mobile networks (2G, EDGE, 3G) or experience intermittent disconnections.

OmusoloSmart addresses this reality through a **dual-tiered resilience architecture**:
1. **Low-Bandwidth Optimization (Online / Slow Networks):** Minimizes network overhead, payload size, and bandwidth consumption using automatic compression and data-saver mode.
2. **Offline-First Fallback (Disconnected):** Delivers zero-network availability for emergency tax directories, official toll-free contact routes, and pure client-side statutory tax calculations.

---

## 2. Low-Bandwidth Optimizations

### 2.1 Network Information API Integration (`useNetworkStatus.ts`)
The frontend inspects `navigator.connection` (`NetworkInformation` API) to detect connection quality:
* **Effective Connection Type:** Detects `'slow-2g'`, `'2g'`, `'3g'`, and `'4g'`.
* **Data Saver Preference:** Detects `navigator.connection.saveData`.
* **Adaptive Reactive Hook:** `useNetworkStatus()` automatically triggers state changes when network conditions degrade or recover.

### 2.2 Data Saver & High-Latency Adaptations (`page.tsx` & `OfflineBanner.tsx`)
When `isLowBandwidth` is active (`saveData === true` or `effectiveType` is `2g` or `slow-2g`):
* **Extended Gateway Timeout:** The SSE and REST request timeout expands from 120s to 180s to prevent premature client-side aborts on high-latency roundtrips.
* **Spoken replies stay light:** Narration (`autoNarrate`, off by default) streams each reply as Ogg/Opus pieces (`/v1/tts/stream`), about 4.5 KB per second of speech, a tenth of the WAV it replaced, so voice mode keeps speaking under Data Saver. Before 2026-09-30, voice mode with narration on received the whole reply as inline WAV (1.1–1.6 MB for a typical answer), whatever the connection.
* **Data Saver UI Banner:** A subtle amber banner (`Low Bandwidth: Connection bandwidth is limited. Data Saver mode active.`) informs the user of data preservation.

### 2.3 Backend Compression (`GZipMiddleware` in `main.py`)
FastAPI serves responses using `starlette.middleware.gzip.GZipMiddleware(minimum_size=500)`:
* Automatically compresses JSON and text responses larger than 500 bytes by **70%–85%** when clients send `Accept-Encoding: gzip`.
* Added directly before `AnalyticsMiddleware` to ensure accurate size threshold evaluation.

---

## 3. Offline Capabilities & Emergency Portal

### 3.1 Hand-Rolled PWA Service Worker (`sw.js` v8)
* Caches core app shell (`/`, `/manifest.json`, `/favicon.svg`, icons) and `offline.html` on install.
* A same-origin GET outside `/api/*` that fails on the network gets its cached copy if there is one; otherwise a page navigation (`request.mode === "navigate"`) gets `offline.html` and any other request fails as a network error, so a `fetch()` for data never receives HTML (`serviceWorkerOffline.test.ts`). It never touches `/api/*` or cross-origin requests, so it cannot serve a stale AI answer or rate.
* Implements navigation preload (`registration.navigationPreload.enable()`) to prevent cold-start latency penalties.
* The cache is named after the offline page's content: `CACHE_NAME = ura-v8-${OFFLINE_PAGE_REV}`, where `OFFLINE_PAGE_REV` is the first 12 hex digits of `sha256(public/offline.html)`. Activation deletes every other cache, so a changed page replaces the copy a phone installed earlier. **Whenever you edit `offline.html`, update `OFFLINE_PAGE_REV`**:
  ```bash
  sha256sum App/frontend/public/offline.html | cut -c1-12
  ```
  `src/__tests__/public/offlinePortal.test.ts` fails until the two match.

### 3.2 Offline Page (`offline.html`)
When completely disconnected, the browser serves `offline.html`. It gives **no tax rate, amount, threshold or calculator**: a figure offline cannot be checked against the law in force, and the cached page can outlive a Finance Act. Until 13 November 2026 it carried a "Statutory" PAYE and VAT calculator whose FY2026/27 bands were wrong (10% on 335,001–410,000 instead of 20%; a flat 20% above instead of 25% then 30%; a VAT threshold of UGX 150M instead of 300M), and this runbook described a third set (G618). The page now holds:
1. **Five languages** (English, Luganda, Swahili, Runyankole, Acholi). It opens in the language the taxpayer chose in the app (`ura-chat-store` in local storage), sets `<html lang>`, and the language buttons expose `aria-pressed`. Vernacular strings other than the subtitle (the app's own `handoff.offline`) are not yet reviewed by first-language speakers (G275).
2. **Why there are no figures:** "Tax rates and amounts are not shown offline. They are only given online, where they are checked against the law in force."
3. **URA's lines**, the same values as `lib/uraContacts.ts`: toll-free `0800 117 000` / `0800 217 000` (`tel:` links), WhatsApp `+256 772 140 000`, email `services@ura.go.ug`.
4. **Try again and auto-reconnect:** the button checks `navigator.onLine` and says in the page's language if still offline (no `alert()`); the `online` event shows a status toast and returns to `/`.

The in-app offline banner (`OfflineBanner.tsx`) says the same in the taxpayer's language (`common.offline_notice`) and links to the page as "URA contacts" (`common.offline_contacts`).

### 3.3 Removed: offline calculator library
`lib/offlineCalculators.ts` was imported only by its own test and carried the same wrong bands; it was removed with G618. Tax figures are computed only by the backend's calculators, from `App/backend/app/tax/data/<FY>.json`.

### 3.4 State Persistence Across Backgrounding & Mobile App Switching
* **Debounced Form Draft Persistence:** Taxpayer inquiry drafts are debounced (400 ms) and saved to local storage under `ura-chat-store.draftMessage`.
* **Visibility State Flush (`page.tsx`):** On `visibilitychange` (`hidden`) and `window.pagehide`, in-progress chat sessions and message drafts are immediately committed to persistent storage without awaiting debounce timeouts.
* **Multi-Step Stepper & Journey Recovery:** `sanitizeTurn()` explicitly retains `workflow` (interactive wizards, current step, option widgets), `nextActions`, and `resources` across sessions and reloads, preventing taxpayer data loss during mobile multitasking.
* **Offline Action Outbox & Low-Bandwidth Voice Codec (`FLAG_OFFLINE_ACTION_OUTBOX`, `FLAG_LOW_BANDWIDTH_VOICE_CODEC`, `FLAG_CALL_DROP_RESUME`):** Backend support for syncing offline actions into `verified_slot_bank` and `ResolutionCase` is defined by `ResolutionLedger.sync_offline_actions()` (and automatically carried into live voice calls via `receptionist/carryover.py:chat_carryover()`), while 2G/3G voice sessions dynamically negotiate 8 kHz G.711 μ-law wire transcoding in `BrowserCallSerializer` (`receptionist/serializer.py`, 75% bandwidth reduction: `32 KB/s -> 8 KB/s`), `caption_only_degraded` audio suppression on `slow-2g`, and 45-second HMAC-signed call drop-resume tickets with cross-replica state snapshots (`receptionist/low_bandwidth.py`).

---

## 4. Verification & Testing Commands

### Frontend Unit & Integration Tests
```bash
cd App/frontend
bun run test src/__tests__/public/offlinePortal.test.ts
bun run test src/__tests__/lib/serviceWorker.test.ts
bun run test src/__tests__/hooks/useNetworkStatus.test.ts
bun run test src/__tests__/components/OfflineBanner.test.tsx
bun run test
bun run lint
```

### Backend Compression Tests
```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_low_bandwidth_compression.py -q
```

### Offline RAG & Bundle Verification
```bash
PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_api_endpoints.py -k "offline" -q
```
