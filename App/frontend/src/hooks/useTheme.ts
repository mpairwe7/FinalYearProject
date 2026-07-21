"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  applyTheme,
  getThemePref,
  setThemePref,
  THEME_EVENT,
  type ThemePref,
} from "../lib/theme";

const ORDER: ThemePref[] = ["auto", "light", "dark"];

function subscribe(onChange: () => void) {
  if (typeof window === "undefined") return () => {};
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  // When following the OS, re-apply on system theme changes.
  const onMq = () => {
    if (getThemePref() === "auto") applyTheme("auto");
  };
  window.addEventListener(THEME_EVENT, onChange);
  mq.addEventListener("change", onMq);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    mq.removeEventListener("change", onMq);
  };
}

/** Current theme preference + a cycler (auto → light → dark → auto). */
export function useTheme() {
  const pref = useSyncExternalStore(subscribe, getThemePref, () => "auto" as ThemePref);
  const cycle = useCallback(() => {
    const next = ORDER[(ORDER.indexOf(getThemePref()) + 1) % ORDER.length];
    setThemePref(next);
  }, []);
  return { pref, cycle };
}
