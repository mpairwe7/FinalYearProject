import './globals.css';
import type { Metadata, Viewport } from 'next';
import React from 'react';
import ConsentBanner from '../components/ConsentBanner';
import Providers from '../components/Providers';
import ServiceWorkerRegistrar from '../components/ServiceWorkerRegistrar';

export const metadata: Metadata = {
  title: 'URA Chatbot — AI Tax Assistant',
  description:
    'Ask anything about Uganda Revenue Authority services, tax policy, and procedures. Grounded answers with live citations, powered by hybrid retrieval and on-device LLM synthesis.',
  applicationName: 'URA Chatbot',
  authors: [{ name: 'mpairweLandwind' }],
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'URA Chat',
  },
  icons: {
    icon: '/favicon.svg',
    apple: '/apple-touch-icon.svg',
  },
  openGraph: {
    title: 'URA Chatbot — AI Tax Assistant',
    description:
      'Grounded answers about URA services and tax with live citations.',
    type: 'website',
  },
};

export const viewport: Viewport = {
  themeColor: '#0A0A12',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ServiceWorkerRegistrar />
        <Providers>
          <ConsentBanner />
          {children}
        </Providers>
      </body>
    </html>
  );
}
