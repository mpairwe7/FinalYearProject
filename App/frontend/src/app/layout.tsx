import './globals.css';
// chatv2 redesign layer — scoped under `.chatv2`, must load after globals.css.
import '../styles/chatv2/index.css';
// Operations console layer — scoped `.ops-*`, must load after globals.css so its
// tokens can build on the theme tokens defined there.
import '../styles/ops/index.css';
import type { Metadata, Viewport } from 'next';
import localFont from 'next/font/local';
import React from 'react';
import ConsentBanner from '../components/ConsentBanner';
import Providers from '../components/Providers';
import ServiceWorkerRegistrar from '../components/ServiceWorkerRegistrar';
import { THEME_INIT_SCRIPT } from '../lib/theme';
import { SIDEBAR_INIT_SCRIPT } from '../lib/sidebarMode';

import OfflineBanner from '../components/OfflineBanner';

/**
 * Console typography — IBM Plex, self-hosted.
 *
 * The staff console had no font of its own: it inherited the local stack in
 * `globals.css` ("Aptos", "Avenir Next", …), so the console an officer saw
 * depended on which fonts their machine happened to ship. Aptos is Windows,
 * Avenir Next is macOS, and neither carries a Light master — so the thin
 * metadata treatment this design calls for could not exist at all.
 *
 * The files are vendored under `src/app/fonts` rather than fetched, because
 * `globals.css` states the build must not reach the network for type. Both
 * families are SIL OFL 1.1 (see `fonts/LICENSE.txt`) and the subsets are latin
 * only — 104KB for the five faces.
 *
 * These are exposed as CSS variables and consumed by `--ops-font-*` in
 * `styles/ops/tokens.css`. The taxpayer chat is deliberately NOT switched over:
 * `AGENTS.md` treats the two surfaces separately and the chat has had its own
 * design pass.
 */
const plexSans = localFont({
  src: [
    { path: './fonts/IBMPlexSans-Regular.woff2', weight: '400', style: 'normal' },
    { path: './fonts/IBMPlexSans-Medium.woff2', weight: '500', style: 'normal' },
    { path: './fonts/IBMPlexSans-SemiBold.woff2', weight: '600', style: 'normal' },
  ],
  variable: '--font-plex-sans',
  display: 'swap',
  // Matched against the Segoe UI / system fallback so the swap does not reflow.
  fallback: ['Segoe UI', 'system-ui', 'sans-serif'],
  adjustFontFallback: false,
  // NOT preloaded. The variables are declared on <html> in the root layout, so
  // Next emits a preload for every face on every route — including `/`, the
  // taxpayer chat, which never renders a glyph in either family. That is 104KB
  // fetched at preload priority on the highest-traffic public page to no
  // effect, and it counts against the Lighthouse performance budget in
  // lighthouserc.json. Without preload the console fetches each face when the
  // CSS first matches; `display: swap` covers the gap and the fallback stack
  // above is the console's previous type, so the worst case is what shipped
  // yesterday for a few hundred milliseconds.
  preload: false,
});

const plexMono = localFont({
  src: [
    { path: './fonts/IBMPlexMono-Light.woff2', weight: '300', style: 'normal' },
    { path: './fonts/IBMPlexMono-Regular.woff2', weight: '400', style: 'normal' },
  ],
  variable: '--font-plex-mono',
  display: 'swap',
  fallback: ['Cascadia Code', 'SFMono-Regular', 'monospace'],
  adjustFontFallback: false,
  preload: false, // see plexSans
});

/**
 * Canonical origin for absolute URLs in metadata.
 *
 * Next resolves `openGraph.images` and `alternates` against `metadataBase`.
 * Without it, a relative image path is emitted relative — and every social
 * crawler (Facebook, X, LinkedIn, WhatsApp, Slack) requires an absolute URL, so
 * the card silently renders with no image. Override per deployment with
 * NEXT_PUBLIC_SITE_URL; the default is the Hugging Face Space this ships to.
 */
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://landwind22-ura-chatbot.hf.space';

const SHARE_TITLE = 'URA Tax Assistant';
const SHARE_DESCRIPTION =
  'Grounded answers on VAT, PAYE, TIN registration and customs — every one with a citation back to an official URA source.';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: 'URA Chatbot — AI Tax Assistant',
  description:
    'Ask anything about Uganda Revenue Authority services, tax policy, and procedures. Grounded answers with live citations, powered by hybrid retrieval and on-device LLM synthesis.',
  applicationName: 'URA Chatbot',
  authors: [{ name: 'mpairweLandwind' }],
  manifest: '/manifest.json',
  alternates: {
    canonical: '/',
    languages: { en: '/', lg: '/' },
  },
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'URA Chat',
  },
  // iOS does not accept SVG for the home-screen icon and applies its own corner
  // mask, so the apple entry has to be an opaque PNG. The SVG stays as the
  // browser-tab favicon, where it is sharp at every size.
  icons: {
    icon: [
      { url: '/favicon.svg', type: 'image/svg+xml' },
      { url: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: [{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }],
  },
  openGraph: {
    type: 'website',
    siteName: 'URA Tax Assistant',
    url: '/',
    title: SHARE_TITLE,
    description: SHARE_DESCRIPTION,
    locale: 'en_UG',
    alternateLocale: ['lg_UG'],
    images: [
      {
        url: '/og-image.png',
        width: 1200,
        height: 630,
        type: 'image/png',
        alt: 'URA Tax Assistant — grounded answers on VAT, PAYE, TIN and customs, with citations',
      },
    ],
  },
  // `summary` renders a small square thumbnail; a 1200x630 card needs
  // `summary_large_image` or the image is cropped to a postage stamp.
  twitter: {
    card: 'summary_large_image',
    title: SHARE_TITLE,
    description: SHARE_DESCRIPTION,
    images: ['/og-image.png'],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, 'max-image-preview': 'large' },
  },
  formatDetection: { telephone: false, address: false, email: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#F5F7FA' },
    { media: '(prefers-color-scheme: dark)', color: '#0A0A12' },
  ],
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`} suppressHydrationWarning>
      <body>
        {/* Set the theme attribute before paint to avoid a flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
        {/* Stamps the rail width before paint so a pinned sidebar does not
            animate open on every staff page load. */}
        <script dangerouslySetInnerHTML={{ __html: SIDEBAR_INIT_SCRIPT }} />
        <ServiceWorkerRegistrar />
        <Providers>
          <OfflineBanner />
          <ConsentBanner />
          {children}
        </Providers>
      </body>
    </html>
  );
}
