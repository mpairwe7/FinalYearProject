import { fileURLToPath } from "url";
import { dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Where the FastAPI backend is reachable FROM THE NEXT.JS SERVER PROCESS
// (not from the browser).  The rewrite below proxies /api/* to this URL
// so the browser only ever talks to the frontend origin — no CORS, no
// hardcoded host:port baked into the client bundle.
const INTERNAL_API_URL = process.env.INTERNAL_API_URL || "http://127.0.0.1:8887";
const isDev = process.env.NODE_ENV !== "production";

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
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
              "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
              "font-src 'self' https://fonts.gstatic.com",
              "img-src 'self' data:",
              // All API calls go through the Next.js rewrite at /api/*, so
              // 'self' is the only origin the browser ever needs to reach.
              // Allow ws: for Turbopack HMR in dev mode.
              `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
            ].join("; "),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
