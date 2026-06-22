'use client';

import Link from 'next/link';
import Image from 'next/image';
import { ChevronLeft } from 'lucide-react';
import { SiteHeader } from '@/components/site-header';
import { SiteFooter } from '@/components/site-footer';
import { posts } from '@/lib/posts';
import { TeamGrid } from '@/components/team-grid';
import { teamMembers } from '@/lib/team';
import { use } from 'react';

interface BlogPostPageProps {
  params: Promise<{ slug: string }>;
}

export default function BlogPostPage({ params }: BlogPostPageProps) {
  const resolvedParams = use(params);
  const post = posts.find((p) => p.slug === resolvedParams.slug);
  const member = teamMembers.find((m) => m.slug === resolvedParams.slug);

  if (!post) {
    return (
      <div className="min-h-screen bg-background text-foreground">
        <SiteHeader />
        <div className="mx-auto flex max-w-3xl flex-col items-center px-4 py-32 text-center">
          <h1 className="text-3xl font-bold">Post not found</h1>
          <Link href="/blog" className="mt-4 text-accent hover:underline">
            Back to all writing
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      <SiteHeader />

      <article className="mx-auto max-w-3xl px-4 py-14 sm:px-6">
        <Link
          href="/blog"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground no-underline transition-colors hover:text-foreground"
        >
          <ChevronLeft className="h-4 w-4" />
          All writing
        </Link>

        {/* Header */}
        <header className="mt-8 mb-10">
          <div className="flex items-center gap-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-accent">
              {post.category}
            </span>
            <span className="text-sm text-muted-foreground">{post.date}</span>
          </div>
          <h1 className="mt-4 text-balance text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
            {post.title}
          </h1>
          <p className="mt-5 text-lg leading-relaxed text-muted-foreground">{post.excerpt}</p>
          <div className="mt-6 flex flex-wrap gap-2">
            {post.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground"
              >
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
                  <span
                    key={role}
                    className="rounded-full bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent"
                  >
                    {role}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Content */}
        <div className="max-w-none space-y-6">
          {post.content.split('\n\n').map((paragraph, index) => {
            if (paragraph.startsWith('#')) {
              const level = paragraph.match(/^#+/)?.[0].length || 1;
              const text = paragraph.replace(/^#+\s/, '');
              const HeadingTag = `h${level}` as any;

              const headingClasses = {
                1: 'text-4xl font-bold leading-tight',
                2: 'text-3xl font-bold leading-snug',
                3: 'text-2xl font-bold leading-snug',
              };

              return (
                <HeadingTag
                  key={index}
                  className={`${
                    headingClasses[level as keyof typeof headingClasses] ||
                    'text-xl font-semibold'
                  } mt-8 mb-4 text-foreground`}
                >
                  {text}
                </HeadingTag>
              );
            }

            if (paragraph.startsWith('|')) {
              const lines = paragraph.split('\n');
              const headers = lines[0]
                .split('|')
                .filter((h) => h.trim())
                .map((h) => h.trim());
              const rows = lines
                .slice(2)
                .map((line) =>
                  line
                    .split('|')
                    .filter((c) => c.trim())
                    .map((c) => c.trim()),
                );

              return (
                <div key={index} className="overflow-x-auto">
                  <table className="w-full border-collapse border border-border">
                    <thead>
                      <tr className="bg-secondary">
                        {headers.map((header) => (
                          <th
                            key={header}
                            className="border border-border px-4 py-2 text-left font-semibold"
                          >
                            {header}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, rowIndex) => (
                        <tr key={rowIndex} className="hover:bg-secondary/50">
                          {row.map((cell, cellIndex) => (
                            <td
                              key={cellIndex}
                              className="border border-border px-4 py-2"
                            >
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
              const code = paragraph
                .replace(/^```\w*\n/, '')
                .replace(/\n```$/, '');

              return (
                <pre
                  key={index}
                  className="overflow-x-auto rounded-lg border border-border bg-secondary p-4"
                >
                  <code className="font-mono text-sm text-foreground">{code}</code>
                </pre>
              );
            }

            if (paragraph.startsWith('-')) {
              const items = paragraph.split('\n').filter((l) => l.startsWith('-'));
              return (
                <ul key={index} className="list-disc space-y-3 pl-6">
                  {items.map((item, itemIndex) => (
                    <li key={itemIndex} className="leading-relaxed text-foreground/85">
                      {item.replace(/^-\s*/, '')}
                    </li>
                  ))}
                </ul>
              );
            }

            return (
              <p key={index} className="text-base leading-relaxed text-foreground/85">
                {paragraph.split('\n').map((line, lineIndex, arr) => {
                  const formattedLine = line
                    .replace(/\*\*(.*?)\*\*/g, (_, text) => `<strong>${text}</strong>`)
                    .replace(/\*(.*?)\*/g, (_, text) => `<em>${text}</em>`);

                  return (
                    <span key={lineIndex}>
                      <span dangerouslySetInnerHTML={{ __html: formattedLine }} />
                      {lineIndex < arr.length - 1 && <br />}
                    </span>
                  );
                })}
              </p>
            );
          })}
        </div>

        {/* Footer nav */}
        <nav className="mt-14 border-t border-border pt-8">
          <Link
            href="/blog"
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-accent no-underline hover:text-accent/80"
          >
            <ChevronLeft className="h-4 w-4" />
            All writing
          </Link>
        </nav>
      </article>

      <SiteFooter />
    </div>
  );
}
