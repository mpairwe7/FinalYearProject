import Link from 'next/link';
import Image from 'next/image';
import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { ChevronLeft, ChevronRight, Clock } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteFooter } from '@/components/site-footer';
import { TeamGrid } from '@/components/team-grid';
import { CodeBlock } from '@/components/code-block';
import { TableOfContents } from '@/components/table-of-contents';
import { ReadingProgress } from '@/components/reading-progress';
import { ScrollToTop } from '@/components/scroll-to-top';
import { teamMembers } from '@/lib/team';
import { posts } from '@/lib/posts';
import { siteConfig, readingTimeMinutes, extractHeadings, parsePostDate, type Heading } from '@/lib/site';

export function generateStaticParams() {
  return posts.map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = posts.find((p) => p.slug === slug);
  if (!post) return { title: 'Post not found' };

  const url = `${siteConfig.url}/blog/${slug}`;
  return {
    title: post.title,
    description: post.excerpt,
    keywords: post.tags,
    alternates: { canonical: `/blog/${slug}` },
    openGraph: {
      type: 'article',
      url,
      title: post.title,
      description: post.excerpt,
      siteName: siteConfig.name,
      publishedTime: parsePostDate(post.date).toISOString(),
      authors: [siteConfig.author],
      tags: post.tags,
    },
    twitter: { card: 'summary_large_image', title: post.title, description: post.excerpt },
  };
}

const headingClasses: Record<number, string> = {
  1: 'text-4xl font-bold leading-tight',
  2: 'text-3xl font-bold leading-snug',
  3: 'text-2xl font-bold leading-snug',
};

function renderContent(content: string, headings: Heading[]) {
  let hIdx = 0;
  return content.split('\n\n').map((paragraph, index) => {
    if (paragraph.startsWith('#')) {
      const level = paragraph.match(/^#+/)?.[0].length || 1;
      const text = paragraph.replace(/^#+\s/, '');
      const Tag = `h${level}` as 'h1';
      const id = level >= 2 && level <= 3 ? headings[hIdx++]?.id : undefined;
      return (
        <Tag
          key={index}
          id={id}
          className={`${headingClasses[level] || 'text-xl font-semibold'} mt-10 mb-4 scroll-mt-24 text-foreground`}
        >
          {text}
        </Tag>
      );
    }

    if (paragraph.startsWith('|')) {
      const lines = paragraph.split('\n');
      const headers = lines[0].split('|').filter((h) => h.trim()).map((h) => h.trim());
      const rows = lines.slice(2).map((line) =>
        line.split('|').filter((c) => c.trim()).map((c) => c.trim()),
      );
      return (
        <div key={index} className="overflow-x-auto">
          <table className="w-full border-collapse border border-border">
            <thead>
              <tr className="bg-secondary">
                {headers.map((header) => (
                  <th key={header} className="border border-border px-4 py-2 text-left font-semibold">
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="hover:bg-secondary/50">
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className="border border-border px-4 py-2">
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    if (paragraph.startsWith('```')) {
      const code = paragraph.replace(/^```\w*\n/, '').replace(/\n```$/, '');
      return <CodeBlock key={index} code={code} />;
    }

    if (paragraph.startsWith('-')) {
      const items = paragraph.split('\n').filter((l) => l.startsWith('-'));
      return (
        <ul key={index} className="list-disc space-y-3 pl-6">
          {items.map((item, itemIndex) => (
            <li key={itemIndex} className="leading-relaxed text-foreground/85">
              <span dangerouslySetInnerHTML={{ __html: item.replace(/^-\s*/, '').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>') }} />
            </li>
          ))}
        </ul>
      );
    }

    return (
      <p key={index} className="text-base leading-relaxed text-foreground/85">
        {paragraph.split('\n').map((line, lineIndex, arr) => {
          const formatted = line
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>');
          return (
            <span key={lineIndex}>
              <span dangerouslySetInnerHTML={{ __html: formatted }} />
              {lineIndex < arr.length - 1 && <br />}
            </span>
          );
        })}
      </p>
    );
  });
}

export default async function BlogPostPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = posts.find((p) => p.slug === slug);
  if (!post) notFound();

  const member = teamMembers.find((m) => m.slug === slug);
  const headings = extractHeadings(post.content);
  const minutes = readingTimeMinutes(post.content);
  const idx = posts.findIndex((p) => p.slug === slug);
  const prev = posts[idx - 1];
  const next = posts[idx + 1];

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'BlogPosting',
    headline: post.title,
    description: post.excerpt,
    datePublished: parsePostDate(post.date).toISOString(),
    author: { '@type': 'Organization', name: siteConfig.author },
    publisher: { '@type': 'Organization', name: siteConfig.name },
    keywords: post.tags.join(', '),
    url: `${siteConfig.url}/blog/${slug}`,
    mainEntityOfPage: `${siteConfig.url}/blog/${slug}`,
  };
  const breadcrumbLd = {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: [
      { '@type': 'ListItem', position: 1, name: 'Home', item: siteConfig.url },
      { '@type': 'ListItem', position: 2, name: 'Writing', item: `${siteConfig.url}/blog` },
      { '@type': 'ListItem', position: 3, name: post.title, item: `${siteConfig.url}/blog/${slug}` },
    ],
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <ReadingProgress />
      <SiteHeader />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbLd) }} />

      <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6 lg:grid lg:grid-cols-[minmax(0,1fr)_15rem] lg:gap-12">
        <article className="min-w-0 max-w-3xl">
          {/* Breadcrumb */}
          <nav aria-label="Breadcrumb" className="mb-6 flex items-center gap-1.5 text-sm text-muted-foreground">
            <Link href="/" className="no-underline hover:text-foreground">Home</Link>
            <ChevronRight className="h-3.5 w-3.5" />
            <Link href="/blog" className="no-underline hover:text-foreground">Writing</Link>
          </nav>

          {/* Header */}
          <header className="mb-10">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-accent">{post.category}</span>
              <span className="text-sm text-muted-foreground">{post.date}</span>
              <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
                <Clock className="h-3.5 w-3.5" /> {minutes} min read
              </span>
            </div>
            <h1 className="mt-4 text-balance text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
              {post.title}
            </h1>
            <p className="mt-5 text-lg leading-relaxed text-muted-foreground">{post.excerpt}</p>
            <div className="mt-6 flex flex-wrap gap-2">
              {post.tags.map((tag) => (
                <span key={tag} className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground">
                  {tag}
                </span>
              ))}
            </div>
          </header>

          {/* Team showcase on the Meet the Team page */}
          {post.slug === 'meet-the-team' && <TeamGrid />}

          {/* Photo header on a member's deep-dive page */}
          {member && (
            <div className="mb-12 flex items-center gap-4 border-b border-border pb-8">
              <Image
                src={member.photo}
                alt={`Portrait of ${member.name}`}
                width={96}
                height={96}
                className="h-24 w-24 flex-shrink-0 rounded-full border border-border object-cover"
              />
              <div>
                <h2 className="text-2xl font-bold text-foreground">{member.name}</h2>
                <div className="mt-2 flex flex-wrap gap-2">
                  {member.roles.map((role) => (
                    <span key={role} className="rounded-full bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent">
                      {role}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Content */}
          <div className="max-w-none space-y-6">{renderContent(post.content, headings)}</div>

          {/* Prev / next */}
          {(prev || next) && (
            <nav className="mt-14 grid gap-4 border-t border-border pt-8 sm:grid-cols-2">
              {prev ? (
                <Link
                  href={`/blog/${prev.slug}`}
                  className="group rounded-lg border border-border p-4 no-underline transition-colors hover:bg-secondary"
                >
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    <ChevronLeft className="h-3.5 w-3.5" /> Previous
                  </span>
                  <p className="mt-1 font-semibold text-foreground transition-colors group-hover:text-accent">
                    {prev.title}
                  </p>
                </Link>
              ) : (
                <span />
              )}
              {next ? (
                <Link
                  href={`/blog/${next.slug}`}
                  className="group rounded-lg border border-border p-4 text-right no-underline transition-colors hover:bg-secondary"
                >
                  <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                    Next <ChevronRight className="h-3.5 w-3.5" />
                  </span>
                  <p className="mt-1 font-semibold text-foreground transition-colors group-hover:text-accent">
                    {next.title}
                  </p>
                </Link>
              ) : (
                <span />
              )}
            </nav>
          )}
        </article>

        {/* Table of contents */}
        <aside className="hidden lg:block">
          <div className="sticky top-24">
            <TableOfContents headings={headings} />
          </div>
        </aside>
      </div>

      <ScrollToTop />
      <SiteFooter />
    </div>
  );
}
