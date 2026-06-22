import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteFooter } from '@/components/site-footer';
import { TeamStrip } from '@/components/team-grid';
import { posts } from '@/lib/posts';

const stats = [
  { value: '94%', label: 'Answer accuracy' },
  { value: '24/7', label: 'Availability' },
  { value: '2', label: 'Languages' },
  { value: '<2s', label: 'Avg response' },
];

const highlights = [
  {
    title: 'Retrieval-augmented answers',
    body: 'A multi-phase RAG pipeline grounds every reply in official URA documents, with corrective re-retrieval when confidence drops.',
  },
  {
    title: 'English & Luganda',
    body: 'Bilingual understanding plus voice — speech-to-text and text-to-speech — so taxpayers can read, type, or talk.',
  },
  {
    title: 'Secure by design',
    body: 'OWASP LLM Top-10 guardrails, PII redaction, and encryption in transit and at rest protect sensitive taxpayer data.',
  },
];

const featuredSlugs = [
  'project-overview',
  'system-architecture',
  'bilingual-support',
  'meet-the-team',
];

export default function Landing() {
  const featured = featuredSlugs
    .map((slug) => posts.find((p) => p.slug === slug))
    .filter((p): p is (typeof posts)[number] => Boolean(p));

  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteHeader />

      {/* Hero */}
      <section className="relative overflow-hidden border-b border-border">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_0%,oklch(0.58_0.22_262/0.10),transparent)]"
        />
        <div className="relative mx-auto max-w-5xl px-4 py-24 sm:px-6 sm:py-32">
          <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
            Final-Year Project · Makerere University
          </p>
          <h1 className="mt-4 max-w-3xl text-balance text-4xl font-bold leading-[1.1] tracking-tight sm:text-6xl">
            A conversational AI for Uganda Revenue Authority
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-relaxed text-muted-foreground">
            The URA Chatbot answers tax questions 24/7 in English and Luganda — grounded in
            official sources, accessible by text or voice, and built to enterprise security
            standards. This blog documents how we built it.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link
              href="/blog"
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-foreground px-5 py-3 text-sm font-semibold text-background no-underline transition-opacity hover:opacity-90"
            >
              Read the writing
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/blog/project-overview"
              className="inline-flex items-center justify-center rounded-lg border border-border px-5 py-3 text-sm font-semibold text-foreground no-underline transition-colors hover:bg-secondary"
            >
              Project overview
            </Link>
          </div>
        </div>
      </section>

      {/* Stats */}
      <section className="border-b border-border">
        <div className="mx-auto grid max-w-5xl grid-cols-2 gap-px overflow-hidden px-4 sm:grid-cols-4 sm:px-6">
          {stats.map((s) => (
            <div key={s.label} className="py-8 text-center sm:text-left">
              <p className="text-3xl font-bold tracking-tight sm:text-4xl">{s.value}</p>
              <p className="mt-1 text-sm text-muted-foreground">{s.label}</p>
            </div>
          ))}
        </div>
      </section>

      {/* What we built */}
      <section className="mx-auto max-w-5xl px-4 py-20 sm:px-6">
        <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
          What we built
        </p>
        <h2 className="mt-3 max-w-2xl text-balance text-3xl font-bold tracking-tight">
          Accurate, accessible, and safe by default
        </h2>
        <div className="mt-12 grid gap-px sm:grid-cols-3">
          {highlights.map((h) => (
            <div key={h.title} className="sm:px-6 sm:first:pl-0">
              <h3 className="text-lg font-semibold">{h.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{h.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Team */}
      <section className="border-y border-border bg-secondary/20">
        <div className="mx-auto max-w-5xl px-4 py-20 sm:px-6">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
                The team
              </p>
              <h2 className="mt-3 text-3xl font-bold tracking-tight">Four students, one system</h2>
            </div>
            <Link
              href="/blog/meet-the-team"
              className="hidden shrink-0 items-center gap-1 text-sm font-semibold text-accent no-underline hover:text-accent/80 sm:inline-flex"
            >
              Roles & ownership
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <div className="mt-10">
            <TeamStrip />
          </div>
        </div>
      </section>

      {/* Featured writing */}
      <section className="mx-auto max-w-5xl px-4 py-20 sm:px-6">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">
              Writing
            </p>
            <h2 className="mt-3 text-3xl font-bold tracking-tight">Start reading</h2>
          </div>
          <Link
            href="/blog"
            className="hidden shrink-0 items-center gap-1 text-sm font-semibold text-accent no-underline hover:text-accent/80 sm:inline-flex"
          >
            All writing
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
        <div className="mt-8 divide-y divide-border border-y border-border">
          {featured.map((post) => (
            <Link
              key={post.slug}
              href={`/blog/${post.slug}`}
              className="group flex items-center justify-between gap-6 py-5 no-underline"
            >
              <div>
                <div className="flex items-center gap-3">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-accent">
                    {post.category}
                  </span>
                  <span className="text-xs text-muted-foreground">{post.date}</span>
                </div>
                <h3 className="mt-1 text-lg font-semibold text-foreground transition-colors group-hover:text-accent">
                  {post.title}
                </h3>
              </div>
              <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-accent" />
            </Link>
          ))}
        </div>
      </section>

      <SiteFooter />
    </div>
  );
}
