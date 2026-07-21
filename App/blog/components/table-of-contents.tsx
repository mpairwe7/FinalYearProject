'use client';

import { useEffect, useState } from 'react';
import type { Heading } from '@/lib/site';

/** Sticky "On this page" nav with scroll-spy active-section highlighting. */
export function TableOfContents({ headings }: { headings: Heading[] }) {
  const [active, setActive] = useState('');

  useEffect(() => {
    const els = headings
      .map((h) => document.getElementById(h.id))
      .filter((el): el is HTMLElement => Boolean(el));
    if (els.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: '-80px 0px -70% 0px', threshold: [0, 1] },
    );

    els.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [headings]);

  if (headings.length < 3) return null;

  return (
    <nav aria-label="Table of contents" className="text-sm">
      <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        On this page
      </p>
      <ul className="space-y-1 border-l border-border">
        {headings.map((h) => (
          <li key={h.id} style={{ paddingLeft: h.level === 3 ? '1rem' : '0' }}>
            <a
              href={`#${h.id}`}
              className={`-ml-px block border-l-2 py-1 pl-3 no-underline transition-colors ${
                active === h.id
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
