"use client";

/**
 * App Router error boundary (segment level).
 *
 * Catches uncaught errors in the chat tree and renders a recoverable
 * fallback with a reset button.  This is the segment-level boundary —
 * any uncaught throw inside `page.tsx` or its children lands here
 * without unmounting the entire app shell.
 */

import { useEffect } from "react";

import { reportClientError } from "@/lib/client-errors";

interface ErrorBoundaryProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function ErrorBoundary({ error, reset }: ErrorBoundaryProps) {
  useEffect(() => {
    // Reported without the message — it may contain user input (PII). The
    // digest is the stable hash Next.js also prints in the server log.
    reportClientError("boundary", error, error.digest ?? "");
  }, [error]);

  return (
    <main role="alert" aria-live="assertive" style={{ padding: "2rem", maxWidth: "640px", margin: "0 auto" }}>
      <h1 style={{ marginBottom: "0.5rem" }}>Something went wrong</h1>
      <p style={{ color: "var(--muted, #666)", marginBottom: "1rem" }}>
        The URA chatbot hit an unexpected error. You can retry or refresh the page.
      </p>
      {error.digest ? (
        <p style={{ color: "var(--muted, #666)", fontSize: "0.85rem", marginBottom: "1rem" }}>
          Reference: <code>{error.digest}</code>
        </p>
      ) : null}
      <button
        type="button"
        onClick={reset}
        style={{
          padding: "0.6rem 1.2rem",
          borderRadius: "0.5rem",
          border: "1px solid var(--accent, #2563eb)",
          background: "var(--accent, #2563eb)",
          color: "#fff",
          cursor: "pointer",
        }}
      >
        Try again
      </button>
    </main>
  );
}
