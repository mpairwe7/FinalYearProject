import './globals.css';
import type { Metadata, Viewport } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import React from 'react';

// 2026 default Next.js font — modern variable sans (Vercel/Geist).
// The CSS variables let us reference the font from globals.css fallbacks.
const geistSans = Geist({
  subsets: ['latin'],
  variable: '--font-geist-sans',
  display: 'swap',
});
const geistMono = Geist_Mono({
  subsets: ['latin'],
  variable: '--font-geist-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'URA Chatbot — AI Tax Assistant',
  description:
    'Ask anything about Uganda Revenue Authority services, tax policy, and procedures. Grounded answers with live citations, powered by hybrid retrieval and on-device LLM synthesis.',
  applicationName: 'URA Chatbot',
  authors: [{ name: 'mpairweLandwind' }],
  openGraph: {
    title: 'URA Chatbot — AI Tax Assistant',
    description:
      'Grounded answers about URA services and tax with live citations.',
    type: 'website',
  },
};

export const viewport: Viewport = {
  themeColor: '#050509',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
