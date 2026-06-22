import Link from 'next/link';
import { ArrowRight, Clock } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteFooter } from '@/components/site-footer';
import { posts } from '@/lib/posts';
import { readingTimeMinutes } from '@/lib/site';

// Display categories in a deliberate reading order; any other categories that
// exist in posts.ts are appended afterwards so nothing is ever hidden.
const CATEGORY_ORDER = [
  'Introduction',
  'Technical',
  'Features',
  'Security',
  'Quality',
  'Operations',
  'Team',
];

export default function BlogIndex() {
  const present = Array.from(new Set(posts.map((p) => p.category)));
  const categories = [
    ...CATEGORY_ORDER.filter((c) => present.includes(c)),
    ...present.filter((c) => !CATEGORY_ORDER.includes(c)),
  ];

  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteHeader />

      <main className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
          Writing
        </p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight">Project writeups</h1>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-muted-foreground">
          How we designed, built, secured, and shipped the URA Chatbot — plus the team behind it.
        </p>

        <div className="mt-14 space-y-14">
          {categories.map((category) => {
            const items = posts.filter((p) => p.category === category);
            if (items.length === 0) return null;
            return (
              <section key={category}>
                <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
                  {category}
                </h2>
                <div className="mt-4 divide-y divide-border border-y border-border">
                  {items.map((post) => (
                    <Link
                      key={post.slug}
                      href={`/blog/${post.slug}`}
                      className="group block py-5 no-underline"
                    >
                      <div className="flex items-start justify-between gap-6">
                        <div className="min-w-0">
                          <h3 className="text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                            {post.title}
                          </h3>
                          <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
                            {post.excerpt}
                          </p>
                          <span className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                            {post.date}
                            <span className="inline-flex items-center gap-1">
                              <Clock className="h-3 w-3" /> {readingTimeMinutes(post.content)} min read
                            </span>
                          </span>
                        </div>
                        <ArrowRight className="mt-1 h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-accent" />
                      </div>
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
