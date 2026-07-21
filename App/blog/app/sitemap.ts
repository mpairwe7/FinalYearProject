import type { MetadataRoute } from 'next';
import { posts } from '@/lib/posts';
import { siteConfig, parsePostDate } from '@/lib/site';

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: siteConfig.url, lastModified: now, changeFrequency: 'weekly', priority: 1 },
    { url: `${siteConfig.url}/blog`, lastModified: now, changeFrequency: 'weekly', priority: 0.8 },
    ...posts.map((p) => ({
      url: `${siteConfig.url}/blog/${p.slug}`,
      lastModified: parsePostDate(p.date),
      changeFrequency: 'monthly' as const,
      priority: 0.6,
    })),
  ];
}
