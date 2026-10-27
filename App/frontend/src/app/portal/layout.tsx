import localFont from 'next/font/local';
import type { ReactNode } from 'react';

/**
 * ura.go.ug sets its pages in a wide geometric sans; the workspace has to read
 * like the site a taxpayer will meet next. Montserrat (SIL OFL 1.1, see
 * `../fonts/LICENSE.txt`) is the open match, vendored like IBM Plex rather than
 * fetched — one variable latin file, 38KB.
 *
 * Loaded here and not in the root layout, so only `/portal/*` downloads it:
 * the taxpayer chat never renders a glyph in it.
 */
const montserrat = localFont({
  src: [{ path: '../fonts/Montserrat-Variable-latin.woff2', weight: '100 900', style: 'normal' }],
  variable: '--font-urap',
  display: 'swap',
  fallback: ['Segoe UI', 'Arial', 'sans-serif'],
  adjustFontFallback: false,
});

export default function PortalLayout({ children }: { children: ReactNode }) {
  return <div className={montserrat.variable}>{children}</div>;
}
