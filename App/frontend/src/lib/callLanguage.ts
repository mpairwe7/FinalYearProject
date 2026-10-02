/**
 * Call languages as the staff console names them. The console is English-only
 * (see the frontend AGENTS.md), so these are plain English names, not
 * dictionary keys — the caller-facing names live in `lib/i18n`.
 */
const NAMES: Record<string, string> = {
  en: 'English',
  lg: 'Luganda',
  sw: 'Swahili',
  nyn: 'Runyankole',
  ach: 'Acholi',
};

/** "Luganda" for `lg`; the code itself for anything unrecognised. */
export function callLanguageName(code: string | null | undefined): string {
  if (!code) return 'English';
  return NAMES[code] ?? code;
}

/** How the call's language was settled, in words an officer reads at a glance. */
export function callLanguageSourceLabel(source: string | null | undefined): string {
  switch (source) {
    case 'auto':
      return 'detected';
    case 'explicit':
      return 'asked for';
    case 'override':
      return 'chosen on screen';
    default:
      return 'default';
  }
}
