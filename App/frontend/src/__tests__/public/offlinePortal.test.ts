/**
 * The offline page shows no tax figure (G618).
 *
 * public/offline.html is what the service worker serves when a taxpayer has no
 * connection. It carried a "Statutory" PAYE and VAT calculator for FY2026/27
 * whose bands were not the year's: 10% on 335,001–410,000 where the rate table
 * says 20%, a flat 20% above where it says 25% then 30%, and a VAT threshold of
 * UGX 150M where it is 300M. UGX 1,000,000 a month came out at 125,500 PAYE;
 * the server's calculator says 188,250. Nothing on the page said "estimate",
 * nothing checked it, and the service worker kept the page until its cache name
 * changed. A figure offline cannot be checked, so the page gives none: it gives
 * URA's lines, in the taxpayer's language.
 *
 * The page ships verbatim, so the test reads the shipped file and runs its
 * script in the jsdom window, as a browser would.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { runInThisContext } from 'node:vm';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { URA_CONTACTS } from '../../lib/uraContacts';
import { LOCALE_OPTIONS } from '../../lib/locales';

const LOCALES = LOCALE_OPTIONS.map((option) => option.value);

const PAGE = readFileSync(join(process.cwd(), 'public/offline.html'), 'utf8');
const SW = readFileSync(join(process.cwd(), 'public/sw.js'), 'utf8');

function load(): void {
  const parsed = new DOMParser().parseFromString(PAGE, 'text/html');
  document.documentElement.lang = parsed.documentElement.lang;
  document.head.innerHTML = parsed.head.innerHTML;
  document.body.innerHTML = parsed.body.innerHTML;
  const script = parsed.querySelector('script');
  if (script?.textContent) runInThisContext(script.textContent);
}

function visibleText(): string {
  return document.body.textContent?.replace(/\s+/g, ' ') ?? '';
}

describe('offline page', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    document.body.innerHTML = '';
    window.localStorage.clear();
  });

  it('has no calculator, rate or amount', () => {
    load();
    expect(document.querySelector('input, select, form')).toBeNull();
    expect(document.querySelector('table')).toBeNull();
    const text = visibleText();
    expect(text).not.toMatch(/\d\s?%/);
    expect(text).not.toMatch(/UGX\s?[\d,.]+/i);
    expect(text).not.toMatch(/FY\s?20\d\d/i);
    expect(PAGE).not.toMatch(/runPayeCalc|runVatCalc|\b0\.18\b|\*\s*0\.[0-9]/);
  });

  it('lists the toll-free and WhatsApp lines the app uses', () => {
    load();
    const text = visibleText();
    for (const line of [...URA_CONTACTS.tollFree, URA_CONTACTS.whatsapp.replace(/^0/, '+256 ')]) {
      expect(text).toContain(line);
    }
    for (const line of URA_CONTACTS.tollFree) {
      expect(document.querySelector(`a[href="tel:${line.replace(/\s/g, '')}"]`)).not.toBeNull();
    }
  });

  it('says why figures are not shown, in every app language, and marks the page language', () => {
    load();
    const seen = new Set<string>();
    for (const locale of LOCALES) {
      (document.querySelector(`button[data-lang="${locale}"]`) as HTMLButtonElement).click();
      expect(document.documentElement.lang).toBe(locale);
      const note = document.getElementById('figuresNote')?.textContent?.trim() ?? '';
      expect(note.length).toBeGreaterThan(20);
      seen.add(note);
      expect(document.querySelector(`button[data-lang="${locale}"]`)?.getAttribute('aria-pressed')).toBe('true');
    }
    expect(seen.size).toBe(LOCALES.length);
  });

  it("opens in the language the taxpayer chose in the app", () => {
    window.localStorage.setItem('ura-chat-store', JSON.stringify({ state: { locale: 'ach' }, version: 0 }));
    load();
    expect(document.documentElement.lang).toBe('ach');
  });

  it('survives an unreadable stored language', () => {
    window.localStorage.setItem('ura-chat-store', '{not json');
    load();
    expect(document.documentElement.lang).toBe('en');
  });
});

describe('service worker cache for the offline page', () => {
  it('is named after the page it holds, so a corrected page replaces an installed one', () => {
    const rev = createHash('sha256').update(PAGE).digest('hex').slice(0, 12);
    expect(SW).toContain(`const OFFLINE_PAGE_REV = '${rev}';`);
    expect(SW).toMatch(/const CACHE_NAME = `ura-v\d+-\$\{OFFLINE_PAGE_REV\}`;/);
  });
});
