/**
 * Next.js 15 App Router loading fallback.
 *
 * Rendered automatically inside a Suspense boundary while the chat
 * route segment is streaming from the server.  Keeps the user on
 * the page instead of a blank flash.
 */

export default function Loading() {
  return (
    <main aria-busy="true" aria-live="polite" style={{ padding: "2rem", textAlign: "center" }}>
      <div
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: "0.5rem",
          color: "var(--muted, #666)",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            width: "14px",
            height: "14px",
            borderRadius: "50%",
            border: "2px solid var(--accent, #2563eb)",
            borderTopColor: "transparent",
            animation: "spin 0.9s linear infinite",
          }}
        />
        <span>Loading URA assistant…</span>
      </div>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </main>
  );
}
