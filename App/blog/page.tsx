'use client';

import Link from 'next/link';
import { useState, useMemo } from 'react';
import { ChevronRight, Search } from 'lucide-react';
import { ThemeToggle } from '@/components/theme-toggle';
import { posts } from '@/lib/posts';

export default function BlogPage() {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const filteredPosts = useMemo(() => {
    return posts.filter((post) => {
      const matchesSearch =
        post.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
        post.excerpt.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesCategory = !selectedCategory || post.category === selectedCategory;
      return matchesSearch && matchesCategory;
    });
  }, [searchQuery, selectedCategory]);

  const categories = Array.from(new Set(posts.map((p) => p.category)));

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="border-b border-border sticky top-0 z-50 bg-background/95 backdrop-blur">
        <div className="max-w-6xl mx-auto px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between">
            <Link href="/" className="text-2xl font-bold text-primary">
              URA Chatbot
            </Link>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <div className="flex flex-col lg:flex-row">
        {/* Sidebar */}
        <aside className="w-full lg:w-64 lg:border-r border-border px-4 py-8 sm:px-6 lg:px-8 lg:sticky lg:top-16 lg:h-[calc(100vh-4rem)] lg:overflow-y-auto">
          <div className="space-y-6">
            {/* About */}
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-widest text-muted-foreground mb-3">
                About
              </h2>
              <p className="text-sm text-muted-foreground leading-relaxed">
                A conversational AI customer service system for the Uganda Revenue Authority. Built with modern security and multilingual support.
              </p>
            </div>

            {/* Categories */}
            <div>
              <h2 className="text-sm font-semibold uppercase tracking-widest text-muted-foreground mb-3">
                Categories
              </h2>
              <div className="space-y-2">
                <button
                  onClick={() => setSelectedCategory(null)}
                  className={`block text-sm transition-colors ${
                    selectedCategory === null
                      ? 'text-accent font-medium'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  All Posts
                </button>
                {categories.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setSelectedCategory(cat)}
                    className={`block text-sm transition-colors ${
                      selectedCategory === cat
                        ? 'text-accent font-medium'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </aside>

        {/* Main Content */}
        <main className="flex-1 px-4 py-8 sm:px-6 lg:px-8">
          <div className="max-w-3xl space-y-8">
            {/* Search */}
            <div>
              <div className="relative">
                <Search className="absolute left-3 top-3 w-5 h-5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Search posts..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-10 pr-4 py-2 rounded-lg border border-border bg-input text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
            </div>

            {/* Posts */}
            <div className="space-y-6">
              {filteredPosts.length === 0 ? (
                <p className="text-muted-foreground text-center py-8">
                  No posts found.
                </p>
              ) : (
                filteredPosts.map((post, index) => (
                  <article
                    key={index}
                    className="border-b border-border pb-8 last:border-0 hover:bg-secondary/20 -mx-4 px-4 py-2 rounded-lg transition-colors duration-200"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <Link href={`/blog/${post.slug}`}>
                          <h3 className="text-2xl font-bold text-foreground hover:text-accent transition-colors duration-200 leading-snug">
                            {post.title}
                          </h3>
                        </Link>
                        <div className="flex items-center gap-3 mt-2">
                          <span className="inline-block px-2 py-1 text-xs font-semibold uppercase tracking-wider text-accent bg-accent/10 rounded">
                            {post.category}
                          </span>
                          <time className="text-sm text-muted-foreground font-medium">{post.date}</time>
                        </div>
                        <p className="text-foreground/80 mt-4 text-base leading-relaxed">
                          {post.excerpt}
                        </p>
                        <Link
                          href={`/blog/${post.slug}`}
                          className="inline-flex items-center gap-1 text-accent hover:text-accent/80 transition-all duration-200 mt-4 text-sm font-semibold"
                        >
                          Read More
                          <ChevronRight className="w-4 h-4" />
                        </Link>
                      </div>
                    </div>
                  </article>
                ))
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
