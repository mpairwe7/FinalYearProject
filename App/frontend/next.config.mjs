import { fileURLToPath } from "url";
import { dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Where the FastAPI backend is reachable FROM THE NEXT.JS SERVER PROCESS
// (not from the browser).  The rewrite below proxies /api/* to this URL
// so the browser only ever talks to the frontend origin — no CORS, no
// hardcoded host:port baked into the client bundle.
const INTERNAL_API_URL = process.env.INTERNAL_API_URL || "http://127.0.0.1:8887";
const isDev = process.env.NODE_ENV !== "production";

// Origin of the OIDC provider, if one is configured.
//
// Every other call the browser makes goes through the /api/* rewrite, but the
// sign-in callback exchanges its authorization code directly with the provider's
// token endpoint. That is deliberate: a public client holds no secret, so there
// is nothing for a server-side proxy to protect (OAuth 2.1 §4.1 with PKCE), and
// the backend issues no tokens of its own to proxy through. So this one origin
// has to be allowed in connect-src or the exchange is blocked and sign-in fails
// with an opaque "NetworkError".
//
// Derived from the issuer rather than hardcoded, and omitted entirely when no
// provider is set, so a deployment that does not use OIDC keeps the tighter policy.
const OIDC_ORIGIN = (() => {
  const issuer = (process.env.NEXT_PUBLIC_OIDC_ISSUER || "").trim();
  if (!issuer) return "";
  try {
    return new URL(issuer).origin;
  } catch {
    console.warn(`[next.config] NEXT_PUBLIC_OIDC_ISSUER is not a URL: ${issuer}`);
    return "";
  }
})();

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // Pin the Turbopack workspace root to this directory so Next.js 16
  // doesn't walk up the filesystem and mis-detect an unrelated lockfile
  // (e.g. ~/package-lock.json) as the workspace root.
  // Allow dev access from 127.0.0.1 / IP (VS Code port-forward, SSH tunnel)
  allowedDevOrigins: ["127.0.0.1", "localhost", "192.168.3.51"],
  turbopack: {
    root: __dirname,
  },
  // Same-origin API proxy — the browser calls /api/v1/chat, Next.js
  // proxies it to the backend over the internal network.  Works on
  // localhost, behind Caddy, on SSH port-forward, and in Docker Compose
  // without any client-side code changes.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${INTERNAL_API_URL}/:path*`,
      },
      {
        source: "/favicon.ico",
        destination: "/favicon.svg",
      },
    ];
  },
  async headers() {
    // Frame embedding: default allows the app itself + Hugging Face, so the UI
    // renders inside the HF Space iframe. Attackers still cannot frame it (only
    // the allow-listed origins can), so clickjacking protection is preserved.
    // Set FRAME_ANCESTORS="'none'" at build time for a strict no-embed deploy.
    const frameAncestors =
      process.env.FRAME_ANCESTORS || "'self' https://huggingface.co https://*.hf.space";
    const strictNoFrame = frameAncestors.trim() === "'none'";
    const securityHeaders = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
      { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
      {
        key: "Content-Security-Policy",
        value: [
          "default-src 'self'",
          // frame-ancestors controls who may embed this app (modern browsers).
          `frame-ancestors ${frameAncestors}`,
          `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
          "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
          "font-src 'self' https://fonts.gstatic.com",
          "img-src 'self' data:",
          // API calls go through the Next.js rewrite at /api/*, so 'self' covers
          // them all; the OIDC token exchange is the one exception (see above).
          // Allow ws: for Turbopack HMR in dev.
          `connect-src 'self'${OIDC_ORIGIN ? ` ${OIDC_ORIGIN}` : ""}${isDev ? " ws: wss:" : ""}`,
        ].join("; "),
      },
    ];
    // X-Frame-Options has no allow-list (ALLOW-FROM is dead) — it can only be the
    // strict DENY, so include it ONLY for the no-embed deploy; otherwise it would
    // override frame-ancestors and block the HF iframe.
    if (strictNoFrame) {
      securityHeaders.splice(1, 0, { key: "X-Frame-Options", value: "DENY" });
    }
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
