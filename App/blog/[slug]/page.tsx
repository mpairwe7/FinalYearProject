'use client';

import Link from 'next/link';
import { ChevronLeft } from 'lucide-react';
import { ThemeToggle } from '@/components/theme-toggle';
import { posts } from '@/lib/posts';
import { use } from 'react';

interface BlogPostPageProps {
  params: Promise<{ slug: string }>;
}

export default function BlogPostPage({ params }: BlogPostPageProps) {
  const resolvedParams = use(params);
  const post = posts.find((p) => p.slug === resolvedParams.slug);

  if (!post) {
    return (
      <div className="min-h-screen bg-background text-foreground flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-3xl font-bold mb-4">Post not found</h1>
          <Link href="/" className="text-accent hover:underline">
            Back to all posts
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="border-b border-border sticky top-0 z-50 bg-background/95 backdrop-blur">
        <div className="max-w-4xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between">
            <Link href="/" className="inline-flex items-center gap-2 text-primary hover:text-accent transition-colors">
              <ChevronLeft className="w-5 h-5" />
              <span>Back</span>
            </Link>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* Article */}
      <article className="max-w-3xl mx-auto px-4 py-12 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="mb-12">
          <div className="flex items-center gap-3 mb-4">
            <span className="inline-block px-3 py-1 text-xs font-semibold uppercase tracking-wider text-accent bg-accent/10 rounded-full">{post.category}</span>
            <span className="text-sm text-muted-foreground">Published {post.date}</span>
          </div>
          <h1 className="text-5xl sm:text-4xl font-bold leading-tight mb-6 text-foreground">{post.title}</h1>
          <p className="text-lg leading-relaxed text-foreground/75">{post.excerpt}</p>
        </div>

        {/* Tags */}
        <div className="flex flex-wrap gap-2 mb-8 pb-8 border-b border-border">
          {post.tags.map((tag) => (
            <span
              key={tag}
              className="px-3 py-1 text-sm rounded-full bg-secondary text-foreground"
            >
              {tag}
            </span>
          ))}
        </div>

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
                  className="bg-secondary p-4 rounded-lg overflow-x-auto border border-border"
                >
                  <code className="text-sm font-mono text-foreground">
                    {code}
                  </code>
                </pre>
              );
            }

            if (paragraph.startsWith('-')) {
              const items = paragraph.split('\n').filter((l) => l.startsWith('-'));
              return (
                <ul key={index} className="list-disc space-y-3 pl-6">
                  {items.map((item, itemIndex) => (
                    <li key={itemIndex} className="text-foreground/85 leading-relaxed">
                      {item.replace(/^-\s*/, '')}
                    </li>
                  ))}
                </ul>
              );
            }

            return (
              <p key={index} className="text-foreground/85 leading-relaxed text-base">
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

        {/* Navigation */}
        <nav className="mt-12 pt-8 border-t border-border">
          <div className="flex items-center justify-between">
            <Link
              href="/"
              className="inline-flex items-center gap-2 text-accent hover:text-accent/80 transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
              All Posts
            </Link>
          </div>
        </nav>
      </article>
    </div>
  );
}
