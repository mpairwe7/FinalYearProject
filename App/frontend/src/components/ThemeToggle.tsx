"use client";

import React from "react";
import { useTheme } from "../hooks/useTheme";
import { SunIcon, MoonIcon, AutoThemeIcon } from "./Icons";

const LABEL = { auto: "Auto", light: "Light", dark: "Dark" } as const;

/**
 * Header control cycling the theme preference: Auto → Light → Dark.
 *
 * `className` lets the operations console use its own 34px icon button rather
 * than the chat header's 44px one; the behaviour is identical either way.
 */
export default function ThemeToggle({ className = "top-bar-icon-btn" }: { className?: string } = {}) {
  const { pref, cycle } = useTheme();
  const Icon = pref === "light" ? SunIcon : pref === "dark" ? MoonIcon : AutoThemeIcon;
  return (
    <button
      type="button"
      className={className}
      onClick={cycle}
      aria-label={`Theme: ${LABEL[pref]}. Click to switch.`}
      title={`Theme: ${LABEL[pref]}`}
    >
      <Icon />
    </button>
  );
}
