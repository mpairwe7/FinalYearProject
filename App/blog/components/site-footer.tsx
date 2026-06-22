import Link from 'next/link';

const footLink = 'text-muted-foreground no-underline transition-colors hover:text-foreground';

export function SiteFooter() {
  return (
    <footer className="border-t border-border">
      <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
        <div className="flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-center">
          <div>
            <p className="font-semibold text-foreground">URA Chatbot</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Final-year project · Makerere University
            </p>
          </div>
          <nav className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <Link href="/blog" className={footLink}>
              Writing
            </Link>
            <Link href="/blog/meet-the-team" className={footLink}>
              Team
            </Link>
            <Link href="/blog/project-overview" className={footLink}>
              Overview
            </Link>
          </nav>
        </div>
        <p className="mt-8 text-xs text-muted-foreground">
          © 2026 URA Chatbot Team. Built with Next.js.
        </p>
      </div>
    </footer>
  );
}
