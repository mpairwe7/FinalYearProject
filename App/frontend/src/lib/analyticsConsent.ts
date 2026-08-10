"use client";

export const ANALYTICS_CONSENT_KEY = "ura_analytics_consent";
export const ANALYTICS_CONSENT_EVENT = "ura:analytics-consent-change";

export function getAnalyticsConsent(): boolean | null {
  if (typeof window === "undefined") return null;
  const stored = localStorage.getItem(ANALYTICS_CONSENT_KEY);
  if (stored === "true") return true;
  if (stored === "false") return false;
  return null;
}

export function hasAnalyticsConsent(): boolean {
  return getAnalyticsConsent() === true;
}

export function setAnalyticsConsent(granted: boolean): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ANALYTICS_CONSENT_KEY, granted ? "true" : "false");
  window.dispatchEvent(
    new CustomEvent(ANALYTICS_CONSENT_EVENT, {
      detail: { granted },
    }),
  );
}
