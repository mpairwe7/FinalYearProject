"use client";

import React from "react";
import "./analytics.css";

/**
 * Route-level error boundary for the analytics section.
 *
 * It was styled entirely inline with a fixed dark palette — a `#fafafa`
 * heading, `#94a3b8` body text and a violet-to-blue gradient button that
 * appears nowhere else in the product. On the light theme the heading was
 * near-white on white, so the one page whose job is to explain a failure was
 * the one page you could not read. It now uses the console's own vocabulary,
 * which follows the theme like everything around it.
 */
export default function AnalyticsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="ops-page is-narrow" id="staff-main">
      <div className="ops-empty">
        <span className="ops-empty-mark" aria-hidden="true">
          !
        </span>
        <h1 className="ops-empty-title">Analytics unavailable</h1>
        <p className="ops-empty-body">
          {error.message || "The analytics data could not be loaded. The backend may be unreachable."}
        </p>
        <div className="ops-row-inline">
          <button type="button" className="ops-btn is-primary" onClick={reset}>
            Try again
          </button>
          <a className="ops-btn" href="/admin">
            Operations overview
          </a>
        </div>
        {error.digest ? <p className="ops-stat-hint">Reference {error.digest}</p> : null}
      </div>
    </main>
  );
}
