/**
 * Whole-corpus presentation guard.
 *
 * The formatting transforms in Markdown.tsx reshape retrieved answers, and the
 * only thing making that defensible is that they stay lossless. One hand-picked
 * example cannot show that, so this renders EVERY answer the app can serve
 * (dumped from the same loader the backend uses) and asserts the two properties
 * that matter universally:
 *
 *   - no word of any answer is dropped by reformatting;
 *   - no list item is so long it is a wall wearing a bullet.
 *
 * It also reports how many answers become lists and how many remain walls, so a
 * regression shows up as a number moving rather than a silent change in shape.
 * At the time of writing: 506 answers, 102 rendered as lists, 0 lossy, 0
 * oversized items, 0 walls.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import Markdown from '../components/Markdown';
import corpus from './corpus.json';

type Row = { tag: string; q: string; a: string };
const rows = corpus as Row[];
const words = (s: string) => (s.toLowerCase().match(/[a-z0-9]+/g) ?? []);

describe('whole-corpus rendering', () => {
  it('never loses content and only lists real enumerations', () => {
    let listed = 0, lossy = 0, longItems = 0, walls = 0;
    const samples: string[] = [];
    for (const r of rows) {
      const { container, unmount } = render(<Markdown content={r.a} />);
      const items = Array.from(container.querySelectorAll('li'));
      // textContent concatenates block elements with no separator, so
      // "Stamps"+"tax" becomes one token. Join blocks explicitly.
      const rendered = Array.from(container.querySelectorAll('p, li, h1, h2, h3, h4, td, th, blockquote, code'))
        .map((el) => el.textContent ?? '')
        .join(' ') || (container.textContent ?? '');
      // 1. lossless: every source word survives
      // An ordered list's "1)" marker is CSS-generated, so the digit legitimately
      // leaves the text. Strip those markers from the source before comparing.
      const src = words(r.a.replace(/(^|\s)\d+[).]\s/g, ' ')), out = new Set(words(rendered));
      const missing = src.filter((w) => !out.has(w));
      if (missing.length) { lossy++; if (samples.length < 4) samples.push(`LOSS ${r.tag}: ${missing.slice(0,6).join(',')}`); }
      // 2. items should read as items, not shredded clauses
      if (items.length) {
        listed++;
        for (const li of items) {
          const t = li.textContent ?? '';
          if (t.length > 220) { longItems++; if (samples.length < 8) samples.push(`LONG ${r.tag}: ${t.slice(0,80)}…`); }
        }
      }
      // 3. anything still a single >600-char paragraph is an unfixed wall
      const paras = Array.from(container.querySelectorAll('p'));
      if (paras.some((p) => (p.textContent ?? '').length > 600)) {
        walls++; if (samples.length < 8) samples.push(`WALL ${r.tag}: ${(r.a).slice(0,70)}…`);
      }
      unmount();
    }
    console.log(`\n  answers        : ${rows.length}`);
    console.log(`  rendered as list: ${listed}`);
    console.log(`  content loss    : ${lossy}`);
    console.log(`  oversized items : ${longItems}`);
    console.log(`  remaining walls : ${walls}`);
    samples.forEach((s) => console.log(`    ${s}`));
    expect(lossy).toBe(0);
    expect(longItems).toBe(0);
  });
});
