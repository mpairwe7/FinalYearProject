"use client";

import { useTranslation } from '../lib/i18n';
import React, { memo, useCallback, useSyncExternalStore } from "react";
import { clearAnalyticsQueue, initAnalytics } from "../store/useAnalyticsStore";
import {
  ANALYTICS_CONSENT_EVENT,
  getAnalyticsConsent,
  setAnalyticsConsent,
} from "../lib/analyticsConsent";

type ConsentSnapshot = boolean | null | undefined;

function subscribeConsent(onStoreChange: () => void) {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => {
    if (event.key === "ura_analytics_consent") onStoreChange();
  };
  window.addEventListener(ANALYTICS_CONSENT_EVENT, onStoreChange);
  window.addEventListener("storage", onStorage);
  const id = window.setTimeout(onStoreChange, 0);
  return () => {
    window.clearTimeout(id);
    window.removeEventListener(ANALYTICS_CONSENT_EVENT, onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}

const getConsentSnapshot = (): ConsentSnapshot => getAnalyticsConsent();
const getServerConsentSnapshot = (): ConsentSnapshot => undefined;

function useConsent() {
  const consent = useSyncExternalStore(
    subscribeConsent,
    getConsentSnapshot,
    getServerConsentSnapshot,
  );
  const grant = useCallback(() => {
    setAnalyticsConsent(true);
    initAnalytics();
  }, []);
  const deny = useCallback(() => {
    setAnalyticsConsent(false);
    clearAnalyticsQueue();
  }, []);
  return { consent, grant, deny };
}

function ConsentBannerInner() {
  const { consent, grant, deny } = useConsent();
  const t = useTranslation();

  // undefined = not yet mounted (SSR), null = undecided, true/false = decided
  if (consent === undefined || consent !== null) return null;

  return (
    <section
      className="consent-banner"
      role="region"
      aria-labelledby="analytics-consent-title"
      aria-describedby="analytics-consent-description"
    >
      <div className="consent-text">
        {/* G655: consent asked in a language the reader cannot read is not informed. */}
        <h2 id="analytics-consent-title">{t('consent.title')}</h2>
        <p id="analytics-consent-description">{t('consent.body')}</p>
      </div>
      <div className="consent-actions">
        <button className="consent-btn consent-deny" onClick={deny}>
          {t('consent.decline')}
        </button>
        <button className="consent-btn consent-accept" onClick={grant}>
          {t('consent.accept')}
        </button>
      </div>
    </section>
  );
}

const ConsentBanner = memo(ConsentBannerInner);
export default ConsentBanner;
