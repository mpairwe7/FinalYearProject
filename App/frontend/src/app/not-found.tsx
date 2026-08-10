import Link from "next/link";

export default function NotFound() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#0A0A12",
        color: "#F8F9FA",
        fontFamily: "var(--font-geist-sans, system-ui, sans-serif)",
        textAlign: "center",
        padding: "2rem",
      }}
    >
      <h1 style={{ fontSize: "4rem", fontWeight: 700, margin: 0, background: "linear-gradient(135deg, #003087, #00A88F)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
        404
      </h1>
      <p style={{ fontSize: "1.1rem", color: "#9CA3AF", marginTop: "0.5rem" }}>
        Page not found
      </p>
      <Link
        href="/"
        style={{
          marginTop: "1.5rem",
          padding: "0.65rem 1.5rem",
          borderRadius: "9999px",
          background: "linear-gradient(135deg, #003087, #005BA0, #00A88F)",
          color: "#F9C74F",
          fontWeight: 600,
          fontSize: "0.88rem",
          textDecoration: "none",
          transition: "opacity 200ms",
        }}
      >
        Back to URA Chat
      </Link>
    </main>
  );
}
