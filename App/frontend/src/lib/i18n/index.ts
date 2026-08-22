/**
 * Interface translation for the taxpayer surface.
 *
 * **One language control, not two.** `useChatStore.locale` already decides the
 * language the assistant answers in, which TTS voice narrates it, and what
 * `<html lang>` says. The interface now follows the same value. A separate
 * "interface language" switch beside the existing one would be two controls for
 * a single user intent — nobody picks Luganda answers because they want an
 * English interface.
 *
 * No i18n dependency. The chatv2 migration shipped without adding one and this
 * follows suit: a dictionary lookup with fallback and `{name}` interpolation is
 * the whole requirement, and react-i18next would be ~40kB and a provider for it.
 *
 * Scope: the taxpayer-facing chat. The staff console at /admin, /agent and
 * /analytics stays English — it is an internal tool for URA officers, and three
 * translations of eight operations pages would be a large surface with no
 * reader.
 */
import { useCallback } from 'react';
import { useChatStore } from '../../store/useChatStore';
import { en, type Dictionary, type TranslationKey } from './en';
import { lg } from './lg';
import { sw } from './sw';

export type { TranslationKey } from './en';

const DICTIONARIES: Record<string, Partial<Dictionary>> = { en, lg, sw };

/** Values interpolated into `{placeholder}` slots. */
export type TranslationVars = Record<string, string | number>;

function interpolate(template: string, vars?: TranslationVars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole,
  );
}

/**
 * Resolve one key. Falls back to English when a locale has no entry for it —
 * an untranslated label reads as English, never as a blank or a raw key, which
 * is the only acceptable failure mode on a public revenue service.
 */
export function translate(
  locale: string,
  key: TranslationKey,
  vars?: TranslationVars,
): string {
  const dictionary = DICTIONARIES[locale];
  return interpolate(dictionary?.[key] ?? en[key], vars);
}

/**
 * `const t = useTranslation()` then `t('composer.send')`.
 *
 * Subscribed to the store's locale, so every string re-renders on a language
 * change without a provider or a reload.
 */
export function useTranslation(): (key: TranslationKey, vars?: TranslationVars) => string {
  const locale = useChatStore((s) => s.locale);
  return useCallback(
    (key: TranslationKey, vars?: TranslationVars) => translate(locale, key, vars),
    [locale],
  );
}

/** Coverage per locale, for the test that guards against silent drift. */
export function translationCoverage(): Record<string, { translated: number; total: number }> {
  const total = Object.keys(en).length;
  return Object.fromEntries(
    Object.entries(DICTIONARIES).map(([code, dict]) => [
      code,
      { translated: Object.keys(dict).length, total },
    ]),
  );
}
