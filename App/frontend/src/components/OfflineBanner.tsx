"use client";

import React, { useSyncExternalStore } from "react";
import { useNetworkStatus } from "../hooks/useNetworkStatus";
import { useTranslation } from "../lib/i18n";

const emptySubscribe = () => () => {};

export default function OfflineBanner() {
  const mounted = useSyncExternalStore(emptySubscribe, () => true, () => false);
  const { isOffline, isLowBandwidth } = useNetworkStatus();
  const t = useTranslation();

  if (!mounted || (!isOffline && !isLowBandwidth)) {
    return null;
  }

  if (isOffline) {
    return (
      <aside
        className="offline-banner"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        <div className="offline-banner-content">
          <svg
            className="offline-banner-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <line x1="1" y1="1" x2="23" y2="23" />
            <path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55" />
            <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39" />
            <path d="M10.71 5.05A16 16 0 0 1 22.58 9" />
            <path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88" />
            <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
            <line x1="12" y1="20" x2="12.01" y2="20" />
          </svg>
          <span className="offline-banner-text">
            <strong>You are offline.</strong>{" "}
            Cached emergency guidance and tax calculators remain functional.
          </span>
          <a
            href="/offline.html"
            className="offline-banner-link"
            style={{
              marginLeft: "auto",
              padding: "0.2rem 0.6rem",
              background: "rgba(255,255,255,0.15)",
              borderRadius: "4px",
              color: "inherit",
              textDecoration: "underline",
              fontSize: "0.8rem",
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            {t("common.offline_calculator") || "Offline Portal"} &rarr;
          </a>
        </div>
      </aside>
    );
  }

  // Low bandwidth state
  return (
    <aside
      className="offline-banner low-bandwidth-banner"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      style={{ background: "#78350F", borderBottom: "1px solid #B45309" }}
    >
      <div className="offline-banner-content">
        <svg
          className="offline-banner-icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39" />
          <path d="M8.53 16.11a6 6 0 0 1 6.95 0" />
          <line x1="12" y1="20" x2="12.01" y2="20" />
        </svg>
        <span className="offline-banner-text">
          <strong>Low Bandwidth:</strong>{" "}
          {t("common.low_bandwidth") ||
            "Connection bandwidth is limited. Data Saver mode active."}
        </span>
      </div>
    </aside>
  );
}
