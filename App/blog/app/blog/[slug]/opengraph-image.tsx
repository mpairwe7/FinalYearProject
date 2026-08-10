import { ImageResponse } from 'next/og';
import { posts } from '@/lib/posts';
import { siteConfig } from '@/lib/site';

export const alt = 'URA Chatbot Blog post';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export function generateStaticParams() {
  return posts.map((p) => ({ slug: p.slug }));
}

export default async function Image({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = posts.find((p) => p.slug === slug);
  const title = post?.title ?? siteConfig.title;
  const category = post?.category ?? 'Blog';

  return new ImageResponse(
    (
      <div
        style={{
          height: '100%',
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          background: '#0a0a0a',
          color: '#fafafa',
          padding: 80,
          fontFamily: 'sans-serif',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <div
              style={{
                width: 44,
                height: 44,
                borderRadius: 10,
                background: '#fafafa',
                color: '#0a0a0a',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 26,
                fontWeight: 700,
              }}
            >
              U
            </div>
            <div style={{ fontSize: 24, letterSpacing: 3, textTransform: 'uppercase', color: '#a1a1aa' }}>
              URA Chatbot · Blog
            </div>
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 600,
              color: '#6366f1',
              textTransform: 'uppercase',
              letterSpacing: 2,
            }}
          >
            {category}
          </div>
        </div>

        <div style={{ display: 'flex', fontSize: title.length > 60 ? 56 : 68, fontWeight: 800, lineHeight: 1.12 }}>
          {title}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ width: 130, height: 6, borderRadius: 3, background: '#6366f1' }} />
          <div style={{ fontSize: 22, color: '#71717a' }}>{siteConfig.url.replace('https://', '')}</div>
        </div>
      </div>
    ),
    { ...size },
  );
}
