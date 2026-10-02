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
  /** BCP-47 tag for the browser SpeechRecognition API, where it is used. */
  speechLang: string;
  /** Who transcribes dictation into the composer. `browser`: the Web Speech
   *  API, which types words as they are spoken but, in Chrome, sends the
   *  audio to Google's servers (other browsers may recognise on the device or
   *  remotely, depending on platform and version). `server`: the local Sunbird
   *  Whisper-SALT model through /v1/asr. Chrome's engine has no Luganda (it
   *  fails with "language-not-supported", which read as "speech recognition
   *  is unavailable"), and SALT is the more accurate for Luganda and Swahili,
   *  so both use it and their audio stays on URA's servers. */
  dictation: 'browser' | 'server';
}

export const LOCALE_OPTIONS: readonly LocaleOption[] = [
  { value: 'en', label: 'English', native: 'English', speechLang: 'en-US', dictation: 'browser' },
  { value: 'lg', label: 'Luganda', native: 'Oluganda', speechLang: 'lg-UG', dictation: 'server' },
  { value: 'sw', label: 'Swahili', native: 'Kiswahili', speechLang: 'sw-KE', dictation: 'server' },
];

/* Runyankole (nyn) and Acholi (ach) were here and were removed: the assistant
   offers English, Luganda and Swahili. `normalizeLocale` below coerces a
   persisted 'nyn'/'ach' back to English rather than leaving a stored value the
   picker can no longer show. The backend still understands both codes, so
   restoring them is a matter of adding the two lines back. */

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
