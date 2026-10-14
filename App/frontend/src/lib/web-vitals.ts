/**
 * Core Web Vitals from real users, measured by Google's `web-vitals` library.
 *
 * This replaced a hand-rolled PerformanceObserver collector that measured the
 * wrong things — INP as the longest event in each observer batch, CLS without
 * session windows, an FCP observer on an entry type that does not exist — and
 * beaconed to an endpoint the API never served. The library implements each
 * metric's definition, including soft navigations (`reportSoftNavs`) in this
 * client-routed app.
 *
 * Reports are queued and sent in batches when the page is hidden, to
 * `/api/v1/telemetry/vitals` (the Next.js rewrite of the API's
 * `/v1/telemetry/vitals`), and only with analytics consent. Each report is the
 * metric, its value and rating, the pathname (the API folds ids out of it)
 * and the navigation type — nothing that identifies the visitor. The API
 * aggregates them into `ura_web_vitals_*` histograms; dashboards read the
 * 75th percentile, the threshold Core Web Vitals are assessed at.
 */

import { onCLS, onFCP, onINP, onLCP, onTTFB, type Metric } from 'web-vitals';
import { hasAnalyticsConsent } from '@/lib/analyticsConsent';

export const VITALS_ENDPOINT = '/api/v1/telemetry/vitals';
const MAX_BATCH = 20;

export interface VitalReport {
  name: Metric['name'];
  value: number;
  rating: Metric['rating'];
  route: string;
  navigation_type: string;
}

let queue: VitalReport[] = [];
let started = false;

export function toReport(metric: Metric, pathname: string): VitalReport {
  return {
    name: metric.name,
    // CLS is a unitless score; the others are milliseconds.
    value: metric.name === 'CLS' ? Math.round(metric.value * 10_000) / 10_000 : Math.round(metric.value),
    rating: metric.rating,
    route: pathname,
    navigation_type: metric.navigationType,
  };
}

function send(body: string): void {
  try {
    if (navigator.sendBeacon?.(VITALS_ENDPOINT, new Blob([body], { type: 'application/json' }))) return;
  } catch {
    // fall through to fetch
  }
  void fetch(VITALS_ENDPOINT, {
    method: 'POST',
    body,
    headers: { 'Content-Type': 'application/json' },
    keepalive: true,
  }).catch(() => undefined);
}

/** Send everything queued, in batches the API accepts. */
export function flushVitals(transport: (body: string) => void = send): void {
  while (queue.length > 0) {
    const batch = queue.slice(0, MAX_BATCH);
    queue = queue.slice(MAX_BATCH);
    transport(JSON.stringify({ metrics: batch }));
  }
}

/** Queue one measurement (dropped without analytics consent). */
export function recordVital(metric: Metric, pathname: string = window.location.pathname): void {
  if (process.env.NODE_ENV === 'development') {
    console.debug(`[Web Vitals] ${metric.name}: ${metric.value.toFixed(metric.name === 'CLS' ? 3 : 0)} (${metric.rating})`);
  }
  if (!hasAnalyticsConsent()) return;
  queue.push(toReport(metric, pathname));
  if (queue.length >= MAX_BATCH) flushVitals();
}

export function initWebVitalsTracking(): void {
  if (typeof window === 'undefined' || started) return;
  started = true;
  const onMetric = (metric: Metric) => recordVital(metric);
  const opts = { reportSoftNavs: true };
  onLCP(onMetric, opts);
  onINP(onMetric, opts);
  onCLS(onMetric, opts);
  onFCP(onMetric, opts);
  onTTFB(onMetric, opts);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushVitals();
  });
  window.addEventListener('pagehide', () => flushVitals());
}

/** Test seam: forget queued reports and the started flag. */
export function resetVitalsForTests(): void {
  queue = [];
  started = false;
}
