"use client";

/**
 * Root error boundary: replaces the root layout when it, or anything the
 * segment-level boundary (error.tsx) cannot catch, throws. It must render its
 * own <html> and <body>. The failure is reported (class and digest only, see
 * src/lib/client-errors.ts) so a crash of the whole app is no longer visible
 * only in the visitor's console.
 */

import { useEffect } from "react";

import { reportClientError } from "@/lib/client-errors";

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    reportClientError("global-boundary", error, error.digest ?? "");
  }, [error]);

  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <main role="alert" aria-live="assertive" style={{ padding: "2rem", maxWidth: "640px", margin: "0 auto" }}>
          <h1 style={{ marginBottom: "0.5rem" }}>Something went wrong</h1>
          <p style={{ marginBottom: "1rem" }}>
            The URA assistant could not load. Please try again, or call the URA contact centre on 0800 117 000.
          </p>
          {error.digest ? (
            <p style={{ fontSize: "0.85rem", marginBottom: "1rem" }}>
              Reference: <code>{error.digest}</code>
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              padding: "0.6rem 1.2rem",
              borderRadius: "0.5rem",
              border: "1px solid #1d4ed8",
              background: "#1d4ed8",
              color: "#fff",
              cursor: "pointer",
            }}
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
