import { describe, expect, it } from 'vitest';
import { safeHttpUrl, safePortalUrl } from '../../lib/safeUrl';

describe('server-directed navigation', () => {
  it.each([
    'javascript:alert(1)', 'data:text/html,x', '//ura.go.ug',
    'https://ura.go.ug@evil.example', 'https://ura.go.ug.evil.example',
    'https://evilura.go.ug', 'http://ura.go.ug', 'https://ura.go.ug:8443',
    'https://ura.go.ug\\@evil.example', 'https://ura.go.ug\n.evil.example',
    'https://', 'https://[invalid', null,
  ])('rejects unsafe portal destinations: %s', (value) => {
    expect(safePortalUrl(value)).toBeNull();
  });

  it('allows HTTPS URA subdomains without changing case-sensitive paths', () => {
    expect(safePortalUrl('https://URA.go.ug/Case?Token=A#B')).toBe('https://URA.go.ug/Case?Token=A#B');
    expect(safePortalUrl('https://portal.ura.go.ug/services')).toBe('https://portal.ura.go.ug/services');
  });

  it('keeps external research sources usable but rejects credentials and malformed hosts', () => {
    expect(safeHttpUrl('https://www.w3.org/TR/WCAG22/')).toBe('https://www.w3.org/TR/WCAG22/');
    expect(safeHttpUrl('https://user:secret@example.org/')).toBeNull();
    expect(safeHttpUrl('https://[bad')).toBeNull();
  });
});
