/**
 * Canonical set of locales the assistant can route, translate, and narrate
 * in — the single place the frontend declares "which languages exist" so
 * the composer's language picker, chat persistence, and the voice surfaces
 * can't drift out of sync with each other (they previously each hardcoded
 * their own English/Luganda-only list independently).
 *
 * This mirrors what the backend already keys its per-locale support on:
 *   - app/sunbird.py LOCALE_TO_SUNBIRD (translation + native TTS voices)
 *   - app/llm.py _select_adapter's fine-tuned LoRA allowlist
 *   - app/agents/patterns (locale-aware supervisor routing tables)
 * Adding a locale here without matching backend support would put a
 * non-functional option in the picker, so keep this list in step with
 * those three.
 */

export interface LocaleOption {
  /** ISO 639-1 (2-letter) or 639-3 (3-letter) code sent to the backend. */
  value: string;
  label: string;
  /** The language's own name for itself, shown under the label in the picker. */
  native: string;
  /** BCP-47 tag for the browser SpeechRecognition API (best-effort — most
   *  browser speech engines only recognise en/sw; lg/nyn/ach fail over
   *  gracefully to the existing "speech unavailable" state). */
  speechLang: string;
}

export const LOCALE_OPTIONS: readonly LocaleOption[] = [
  { value: 'en', label: 'English', native: 'English', speechLang: 'en-US' },
  { value: 'lg', label: 'Luganda', native: 'Oluganda', speechLang: 'lg-UG' },
  { value: 'sw', label: 'Swahili', native: 'Kiswahili', speechLang: 'sw-KE' },
  { value: 'nyn', label: 'Runyankole', native: 'Runyankore', speechLang: 'nyn' },
  { value: 'ach', label: 'Acholi', native: 'Leb Acoli', speechLang: 'ach' },
];

export const DEFAULT_LOCALE = 'en';

export function isSupportedLocale(value: unknown): value is string {
  return typeof value === 'string' && LOCALE_OPTIONS.some((o) => o.value === value);
}

/** Coerce a persisted or externally-supplied value to a known locale. */
export function normalizeLocale(value: unknown): string {
  return isSupportedLocale(value) ? value : DEFAULT_LOCALE;
}

/** Display label for a locale code (English for unknown/legacy codes). */
export function localeLabel(code: string): string {
  return LOCALE_OPTIONS.find((o) => o.value === code)?.label ?? LOCALE_OPTIONS[0].label;
}
