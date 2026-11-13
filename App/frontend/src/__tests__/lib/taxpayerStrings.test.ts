/**
 * Taxpayer-facing words come from the dictionary (G655; App/frontend/AGENTS.md).
 *
 * The status line a screen reader announces ("URA response ready."), the
 * analytics consent notice and the screen-reader names of who said what were
 * English literals, so a Luganda, Swahili, Runyankole or Acholi reader heard
 * them in English. A consent asked in a language the reader cannot read is not
 * informed consent.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (path: string) => readFileSync(join(process.cwd(), path), 'utf8');

describe('taxpayer strings are dictionary keys', () => {
  it('announces status only through translated keys', () => {
    const page = read('src/app/page.tsx');
    expect(page).not.toMatch(/setChatLiveStatus\(\s*['"`]/);
    expect(page).not.toMatch(/setChatLiveStatus\([^)]*\?\s*[^:]*:\s*['"`]/);
  });

  it('asks for consent in the reader\'s language', () => {
    const banner = read('src/components/ConsentBanner.tsx');
    expect(banner).not.toMatch(/>\s*(Privacy notice|Accept|Decline)\s*</);
    expect(banner).not.toContain('No personal data is stored');
  });

  it('names who said what in the reader\'s language', () => {
    const message = read('src/components/ChatMessage.tsx');
    expect(message).not.toMatch(/'You said'|'Assistant replied'/);
  });
});
