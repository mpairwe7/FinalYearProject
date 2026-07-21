// Central site configuration + small content helpers, shared across metadata,
// sitemap, robots, RSS feed, OG images, and the post reader.

export const siteConfig = {
  name: 'URA Chatbot Blog',
  shortName: 'URA Chatbot',
  title: 'URA Chatbot — Project Blog',
  description:
    'How we designed, built, secured, and shipped a bilingual conversational AI for the Uganda Revenue Authority — a final-year project at Makerere University.',
  url: (process.env.NEXT_PUBLIC_SITE_URL || 'https://blog-two-mu-45.vercel.app').replace(/\/$/, ''),
  author: 'URA Chatbot Team',
  locale: 'en_US',
};

/** Rough reading-time estimate (~200 words/min). */
export function readingTimeMinutes(content: string): number {
  const words = content.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / 200));
}

/** Stable slug for a heading, used for ToC anchors. */
export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-');
}

export interface Heading {
  id: string;
  text: string;
  level: number;
}

/** Extract H2/H3 headings (with de-duplicated ids) for the table of contents. */
export function extractHeadings(content: string): Heading[] {
  const out: Heading[] = [];
  for (const line of content.split('\n')) {
    const m = /^(#{2,3})\s+(.*)$/.exec(line.trim());
    if (m) {
      out.push({ id: slugifyHeading(m[2].replace(/\*\*/g, '')), text: m[2].replace(/\*\*/g, '').trim(), level: m[1].length });
    }
  }
  const seen = new Map<string, number>();
  for (const h of out) {
    const n = seen.get(h.id) ?? 0;
    if (n > 0) h.id = `${h.id}-${n}`;
    seen.set(h.id, n + 1);
  }
  return out;
}

/** Parse a "Month YYYY" date string into a Date (falls back to project start). */
export function parsePostDate(date: string): Date {
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? new Date('2026-06-01') : d;
}
