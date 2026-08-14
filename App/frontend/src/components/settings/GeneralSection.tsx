"use client";

/**
 * General settings — appearance and response language.
 *
 * Both controls write the same state the header controls already write
 * (`lib/theme` and `useChatStore.locale`), so the header's theme button and
 * language menu stay in step with this panel; neither is a second copy of the
 * preference.
 */

import React from "react";
import { useTheme } from "../../hooks/useTheme";
import { setThemePref, type ThemePref } from "../../lib/theme";
import { LOCALE_OPTIONS } from "../../lib/locales";
import { useChatStore } from "../../store/useChatStore";
import { SegmentedOption, SelectControl, Segmented, SettingsRow, SettingsSection } from "./controls";

const THEME_OPTIONS: readonly SegmentedOption<ThemePref>[] = [
  { value: "auto", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const LOCALE_SELECT_OPTIONS = LOCALE_OPTIONS.map((o) => ({
  value: o.value,
  label: o.native === o.label ? o.label : `${o.label} — ${o.native}`,
}));

export default function GeneralSection() {
  const { pref } = useTheme();
  const locale = useChatStore((s) => s.locale);
  const setLocale = useChatStore((s) => s.setLocale);

  return (
    <>
      <SettingsSection
        title="Appearance"
        description="Applies to this browser only, and takes effect immediately."
      >
        <SettingsRow
          label="Theme"
          hint="Auto follows your device's light or dark setting."
        >
          <Segmented
            label="Theme"
            value={pref}
            options={THEME_OPTIONS}
            onChange={setThemePref}
          />
        </SettingsRow>
      </SettingsSection>

      <SettingsSection
        title="Language"
        description="Governs the language answers are written in, the narration voice, and speech recognition."
      >
        <SettingsRow
          label="Response language"
          hint="Ugandan languages are translated by Sunbird; English answers come straight from the model."
          htmlFor="setv2-locale"
        >
          <SelectControl
            id="setv2-locale"
            value={locale}
            options={LOCALE_SELECT_OPTIONS}
            onChange={setLocale}
          />
        </SettingsRow>
      </SettingsSection>
    </>
  );
}
