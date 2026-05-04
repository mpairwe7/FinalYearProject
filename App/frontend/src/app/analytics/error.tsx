"use client";

export default function AnalyticsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main
      style={{
        minHeight: "50vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "2rem",
        textAlign: "center",
      }}
    >
      <h2 style={{ fontSize: "1.25rem", fontWeight: 650, color: "#fafafa", marginBottom: "0.5rem" }}>
        Analytics unavailable
      </h2>
      <p style={{ fontSize: "0.85rem", color: "#94a3b8", maxWidth: 400 }}>
        {error.message || "Failed to load analytics data. The backend may be unreachable."}
      </p>
      <button
        onClick={reset}
        style={{
          marginTop: "1rem",
          padding: "0.5rem 1.25rem",
          borderRadius: "8px",
          background: "linear-gradient(135deg, #8b5cf6, #3b82f6)",
          color: "#fff",
          fontWeight: 600,
          fontSize: "0.85rem",
          border: "none",
          cursor: "pointer",
        }}
      >
        Retry
      </button>
    </main>
  );
}
