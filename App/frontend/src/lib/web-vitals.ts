/**
 * Core Web Vitals Monitoring
 *
 * Tracks three key metrics for responsive design & performance:
 * - LCP (Largest Contentful Paint): How quickly main content loads
 * - FID/INP (Interaction to Next Paint): Responsiveness to user input
 * - CLS (Cumulative Layout Shift): Visual stability during page load
 *
 * These metrics are critical for responsive design because they
 * measure actual performance on different devices/networks.
 */

export interface WebVitals {
  name: 'LCP' | 'FID' | 'INP' | 'CLS' | 'TTFB';
  value: number;
  rating: 'good' | 'needs-improvement' | 'poor';
  delta: number;
  id: string;
  navigationType: 'navigate' | 'reload' | 'back-forward' | 'restore';
}

export interface VitalsThresholds {
  good: number;
  poor: number;
}

/**
 * Web Vitals thresholds (in milliseconds for timing, 0-1 for CLS)
 * Based on Google's Core Web Vitals assessment criteria
 */
export const VITALS_THRESHOLDS: Record<string, VitalsThresholds> = {
  LCP: { good: 2500, poor: 4000 },      // Largest Contentful Paint
  FID: { good: 100, poor: 300 },        // First Input Delay
  INP: { good: 200, poor: 500 },        // Interaction to Next Paint
  CLS: { good: 0.1, poor: 0.25 },       // Cumulative Layout Shift
  TTFB: { good: 600, poor: 1800 },      // Time to First Byte
};

/**
 * Initialize Core Web Vitals tracking
 * Reports metrics to console (in dev) and optional endpoint
 */
export function initWebVitalsTracking(_endpoint?: string): void {
  if (typeof window === 'undefined') return;

  // Detect if we can use the Web Vitals API
  if ('web-vital' in window || 'PerformanceObserver' in window) {
    trackLCP();
    trackINP();
    trackCLS();
    trackFCP();
  }
}

/**
 * Track LCP (Largest Contentful Paint)
 * Measures how quickly the main content loads
 */
function trackLCP(): void {
  const observer = new PerformanceObserver((list) => {
    const entries = list.getEntries();
    const lastEntry = entries[entries.length - 1] as LargestContentfulPaint;

    const value = lastEntry.renderTime || lastEntry.startTime || 0;
    const rating = getRating('LCP', value);

    if (process.env.NODE_ENV === 'development') {
      console.log(`LCP: ${value.toFixed(0)}ms (${rating})`);
    }

    // Send to analytics if needed
    reportWebVital({ name: 'LCP', value, rating, entryType: lastEntry.entryType });
  });

  observer.observe({ entryTypes: ['largest-contentful-paint'] });
}

/**
 * Track INP (Interaction to Next Paint)
 * Measures responsiveness to user interactions
 */
function trackINP(): void {
  const observer = new PerformanceObserver((list) => {
    let maxDuration = 0;

    for (const entry of list.getEntries()) {
      const duration = (entry as PerformanceEventTiming).duration;
      if (duration > maxDuration) {
        maxDuration = duration;
      }
    }

    if (maxDuration > 0) {
      const rating = getRating('INP', maxDuration);

      if (process.env.NODE_ENV === 'development') {
        console.log(`INP: ${maxDuration.toFixed(0)}ms (${rating})`);
      }

      reportWebVital({ name: 'INP', value: maxDuration, rating, entryType: 'event' });
    }
  });

  observer.observe({ entryTypes: ['event'], buffered: true });
}

/**
 * Track CLS (Cumulative Layout Shift)
 * Measures visual stability
 */
function trackCLS(): void {
  let clsValue = 0;
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (!(entry as LayoutShift).hadRecentInput) {
        clsValue += (entry as LayoutShift).value;
      }
    }

    const rating = getRating('CLS', clsValue);

    if (process.env.NODE_ENV === 'development') {
      console.log(`CLS: ${clsValue.toFixed(3)} (${rating})`);
    }

    reportWebVital({ name: 'CLS', value: clsValue, rating, entryType: 'layout-shift' });
  });

  observer.observe({ entryTypes: ['layout-shift'], buffered: true });
}

/**
 * Track FCP (First Contentful Paint)
 * Measures when first content appears
 */
function trackFCP(): void {
  const observer = new PerformanceObserver((list) => {
    const entries = list.getEntries();
    const lastEntry = entries[entries.length - 1];

    const value = lastEntry.startTime;
    const rating = getRating('TTFB', value);

    if (process.env.NODE_ENV === 'development') {
      console.log(`FCP: ${value.toFixed(0)}ms (${rating})`);
    }

    reportWebVital({ name: 'TTFB', value, rating, entryType: lastEntry.entryType });
  });

  observer.observe({ entryTypes: ['first-contentful-paint'] });
}

/**
 * Determine rating (good/needs-improvement/poor) based on thresholds
 */
function getRating(
  metric: 'LCP' | 'FID' | 'INP' | 'CLS' | 'TTFB',
  value: number
): 'good' | 'needs-improvement' | 'poor' {
  const thresholds = VITALS_THRESHOLDS[metric];
  if (!thresholds) return 'poor';

  if (value <= thresholds.good) return 'good';
  if (value <= thresholds.poor) return 'needs-improvement';
  return 'poor';
}

/**
 * Report Web Vital to console or analytics endpoint
 */
interface VitalReport {
  name: string;
  value: number;
  rating: string;
  entryType: string;
}

function reportWebVital(_vital: VitalReport): void {
  // In production, send to analytics service
  if (process.env.NODE_ENV === 'production' && typeof navigator !== 'undefined') {
    // Send to analytics (example, replace with actual endpoint)
    // const payload = {
    //   metric: vital.name,
    //   value: vital.value,
    //   rating: vital.rating,
    //   url: window.location.href,
    //   userAgent: navigator.userAgent,
    //   timestamp: new Date().toISOString(),
    // };
    // fetch('/api/analytics/vitals', { method: 'POST', body: JSON.stringify(payload) });
  }
}

interface WebVitalsSnapshot {
  lcp: PerformanceEntry | undefined;
  inp: PerformanceEntry[];
  cls: PerformanceEntry[];
  fcp: PerformanceEntry | undefined;
}

/**
 * Get current Web Vitals snapshot for testing
 */
export async function getWebVitalsSnapshot(): Promise<WebVitalsSnapshot> {
  if (typeof window === 'undefined') return { lcp: undefined, inp: [], cls: [], fcp: undefined };

  const entries = performance.getEntries();
  return {
    lcp: entries.find((e) => e.entryType === 'largest-contentful-paint'),
    inp: entries.filter((e) => e.entryType === 'event' && (e as PerformanceEventTiming).duration > 100),
    cls: entries.filter((e) => e.entryType === 'layout-shift'),
    fcp: entries.find((e) => e.name === 'first-contentful-paint'),
  };
}

// Type definitions for layout shift
interface LayoutShift extends PerformanceEntry {
  value: number;
  hadRecentInput: boolean;
}

// Type definitions for LCP
interface LargestContentfulPaint extends PerformanceEntry {
  renderTime: number;
  startTime: number;
}
