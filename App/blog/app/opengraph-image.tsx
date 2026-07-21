import { ImageResponse } from 'next/og';
import { siteConfig } from '@/lib/site';

export const alt = siteConfig.title;
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function Image() {
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 12,
              background: '#fafafa',
              color: '#0a0a0a',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 28,
              fontWeight: 700,
            }}
          >
            U
          </div>
          <div style={{ fontSize: 26, letterSpacing: 4, textTransform: 'uppercase', color: '#a1a1aa' }}>
            URA Chatbot · Blog
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 70, fontWeight: 800, lineHeight: 1.1 }}>
            Conversational AI for Uganda Revenue Authority
          </div>
          <div style={{ fontSize: 30, color: '#a1a1aa', marginTop: 28 }}>
            Bilingual, grounded, and secure — by four Makerere students.
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ width: 130, height: 6, borderRadius: 3, background: '#6366f1' }} />
          <div style={{ fontSize: 22, color: '#71717a' }}>Final-year project · Makerere University</div>
        </div>
      </div>
    ),
    { ...size },
  );
}
