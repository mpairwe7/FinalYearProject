# Runbook: Offline & Low-Bandwidth Connectivity

**Scope:** Frontend PWA & service worker, offline fallback portal, client-side statutory tax calculators, low-bandwidth network detection, and backend response compression.  
**Audience:** Frontend and backend engineers, SREs, QA engineers.  
**Updated:** September 2026

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
* **Conserved Voice & Media Prefetching:** Automatic background audio synthesis narration (`autoNarrate`) is guarded to prevent consuming metered cellular megabytes.
* **Data Saver UI Banner:** A subtle amber banner (`Low Bandwidth: Connection bandwidth is limited. Data Saver mode active.`) informs the user of data preservation.

### 2.3 Backend Compression (`GZipMiddleware` in `main.py`)
FastAPI serves responses using `starlette.middleware.gzip.GZipMiddleware(minimum_size=500)`:
* Automatically compresses JSON and text responses larger than 500 bytes by **70%–85%** when clients send `Accept-Encoding: gzip`.
* Added directly before `AnalyticsMiddleware` to ensure accurate size threshold evaluation.

---

## 3. Offline Capabilities & Emergency Portal

### 3.1 Hand-Rolled PWA Service Worker (`sw.js` v7)
* Caches core app shell (`/`, `/manifest.json`, `/favicon.svg`, icons) on install.
* Serves `offline.html` immediately on network failure for all document navigations.
* Implements navigation preload (`registration.navigationPreload.enable()`) to prevent cold-start latency penalties.

### 3.2 Enhanced Offline Portal (`offline.html`)
When completely disconnected, the browser serves `offline.html`, which includes:
1. **Trilingual Language Switcher:** Full parity across **English**, **Luganda**, and **Swahili**.
2. **Official URA Contact Directory:**
   * Toll-free helplines: `0800 117 000` / `0800 217 000` (`tel:0800117000`)
   * Official WhatsApp: `+256 772 140 000` (`https://wa.me/256772140000`)
   * Support email: `services@ura.go.ug`
3. **Key Statutory Deadlines & Rates Reference:**
   * PAYE monthly tax-free threshold: **UGX 335,000** (FY2026/27).
   * Standard VAT rate: **18%**; mandatory threshold: **UGX 150,000,000/year**.
   * Return deadlines: **15th of each month** for PAYE/VAT/WHT; **31st December** for Income Tax.
4. **Zero-Dependency Client-Side Statutory Tax Calculators:**
   * **Monthly PAYE Calculator:** Executes progressive tax calculations across all statutory brackets including high-earner surcharge (>UGX 10M).
   * **18% VAT Calculator:** Computes exclusive (net + 18%) or inclusive (extract 18%) amounts.
5. **Auto-Reconnect Listener:** Automatically detects `window.addEventListener('online')`, displays a success toast, and redirects back into the live app.

### 3.3 Pure Offline Calculator Library (`offlineCalculators.ts`)
* `calculatePayeMonthly(grossSalary)`
* `calculateVat(amount, mode)`
* `calculatePresumptiveTax(turnover)`
* `formatUgx(amount)`

---

## 4. Verification & Testing Commands

### Frontend Unit & Integration Tests
```bash
cd App/frontend
bun run test src/__tests__/lib/offlineCalculators.test.ts
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
