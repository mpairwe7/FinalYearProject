/**
 * The interface translation layer.
 *
 * Two properties matter more than any individual string:
 *
 * - A key with no translation must render the ENGLISH text. On a public revenue
 *   service, a missing Luganda label reading as English is a degraded
 *   experience; reading as `composer.placeholder`, or as nothing at all, is a
 *   broken one.
 * - A key in lg/sw that does not exist in en is a typo that would silently
 *   never render. TypeScript catches it at the dictionary, but not if someone
 *   widens the type, so it is asserted here too.
 */
import { describe, expect, it } from 'vitest';

import { en } from '../../lib/i18n/en';
import { lg } from '../../lib/i18n/lg';
import { sw } from '../../lib/i18n/sw';
import { translate, translationCoverage } from '../../lib/i18n';

describe('i18n', () => {
  it('translates into each supported locale', () => {
    expect(translate('en', 'rail.newChat')).toBe('New chat');
    expect(translate('lg', 'rail.newChat')).toBe('Emboozi empya');
    expect(translate('sw', 'rail.newChat')).toBe('Mazungumzo mapya');
  });

  it('falls back to English rather than rendering a key or a blank', () => {
    // Deliberately not translated in either dictionary.
    expect(lg['message.escalated']).toBeUndefined();
    expect(translate('lg', 'message.escalated')).toBe(en['message.escalated']);
    expect(translate('sw', 'message.escalated')).toBe(en['message.escalated']);
  });

  it('falls back to English for an unknown locale', () => {
    expect(translate('fr', 'rail.chats')).toBe(en['rail.chats']);
  });

  it('interpolates named values', () => {
    expect(translate('en', 'message.sources', { count: 3 })).toBe('Sources (3)');
    expect(translate('sw', 'message.sources', { count: 3 })).toBe('Vyanzo (3)');
  });

  it('leaves an unknown placeholder in place instead of printing undefined', () => {
    expect(translate('en', 'message.listenIn', {})).toBe('Listen in {language}');
  });

  it('has no key in a translation that is missing from English', () => {
    const source = new Set(Object.keys(en));
    for (const [name, dict] of [['lg', lg], ['sw', sw]] as const) {
      const orphans = Object.keys(dict).filter((k) => !source.has(k));
      expect(orphans, `${name} has keys English does not: ${orphans.join(', ')}`).toEqual([]);
    }
  });

  it('reports coverage so a drop is visible rather than silent', () => {
    const coverage = translationCoverage();
    expect(coverage.en.translated).toBe(coverage.en.total);
    // Not a target to chase — a floor, so a large regression fails the build.
    expect(coverage.lg.translated).toBeGreaterThan(coverage.lg.total * 0.7);
    expect(coverage.sw.translated).toBeGreaterThan(coverage.sw.total * 0.7);
  });
});
