"use client";

import React from "react";
import { useTheme } from "../hooks/useTheme";
import { SunIcon, MoonIcon, AutoThemeIcon } from "./Icons";

const LABEL = { auto: "Auto", light: "Light", dark: "Dark" } as const;

/** Header control cycling the theme preference: Auto → Light → Dark. */
export default function ThemeToggle() {
  const { pref, cycle } = useTheme();
  const Icon = pref === "light" ? SunIcon : pref === "dark" ? MoonIcon : AutoThemeIcon;
  return (
    <button
      type="button"
      className="top-bar-icon-btn"
      onClick={cycle}
      aria-label={`Theme: ${LABEL[pref]}. Click to switch.`}
      title={`Theme: ${LABEL[pref]}`}
    >
      <Icon />
    </button>
  );
}
