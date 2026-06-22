'use client';

import Link from 'next/link';
import { ThemeToggle } from '@/components/theme-toggle';

// Optional link back to the live chatbot. Set NEXT_PUBLIC_APP_URL in Vercel to
// show a "Chatbot" link in the blog header; omitted when not configured.
const APP_URL = process.env.NEXT_PUBLIC_APP_URL;

const navLink =
  'rounded-md px-3 py-1.5 text-sm text-muted-foreground no-underline transition-colors hover:bg-secondary hover:text-foreground';

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3 sm:px-6">
        <Link href="/" className="inline-flex items-center gap-2 no-underline">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-foreground text-xs font-bold text-background">
            U
          </span>
          <span className="font-semibold tracking-tight text-foreground">URA Chatbot</span>
          <span className="rounded-full border border-border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Blog
          </span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          <Link href="/" className={navLink}>
            Home
          </Link>
          <Link href="/blog" className={navLink}>
            Writing
          </Link>
          {APP_URL && (
            <a
              href={APP_URL}
              target="_blank"
              rel="noopener noreferrer"
              className={`hidden sm:inline-block ${navLink}`}
            >
              Chatbot ↗
            </a>
          )}
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
