import './globals.css';
// chatv2 redesign layer — scoped under `.chatv2`, must load after globals.css.
import '../styles/chatv2/index.css';
// Operations console layer — scoped `.ops-*`, must load after globals.css so its
// tokens can build on the theme tokens defined there.
import '../styles/ops/index.css';
import type { Metadata, Viewport } from 'next';
import React from 'react';
import ConsentBanner from '../components/ConsentBanner';
import Providers from '../components/Providers';
import ServiceWorkerRegistrar from '../components/ServiceWorkerRegistrar';
import { THEME_INIT_SCRIPT } from '../lib/theme';
import { SIDEBAR_INIT_SCRIPT } from '../lib/sidebarMode';

import OfflineBanner from '../components/OfflineBanner';

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
    <html lang="en" suppressHydrationWarning>
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
