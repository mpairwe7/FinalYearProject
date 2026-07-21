/**
 * Theme preference: "auto" (follow the OS), "light", or "dark".
 *
 * The effective theme is applied as `data-theme="light|dark"` on
 * <html>; CSS keys the light token overrides off `:root[data-theme="light"]`.
 * A blocking inline script in the root layout sets the initial attribute before
 * paint (no flash); this module keeps it in sync at runtime.
 */
export type ThemePref = "auto" | "light" | "dark";

const KEY = "ura-theme";
export const THEME_EVENT = "ura-theme-change";

export function getThemePref(): ThemePref {
  if (typeof window === "undefined") return "auto";
  const v = window.localStorage.getItem(KEY);
  return v === "light" || v === "dark" ? v : "auto";
}

export function resolveTheme(pref: ThemePref): "light" | "dark" {
  if (pref !== "auto") return pref;
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(pref: ThemePref): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = resolveTheme(pref);
}

export function setThemePref(pref: ThemePref): void {
  if (typeof window === "undefined") return;
  if (pref === "auto") window.localStorage.removeItem(KEY);
  else window.localStorage.setItem(KEY, pref);
  applyTheme(pref);
  window.dispatchEvent(new CustomEvent(THEME_EVENT));
}

/** The inline string evaluated before paint in the document head. */
export const THEME_INIT_SCRIPT =
  "(function(){try{var p=localStorage.getItem('ura-theme');" +
  "var t=(p==='light'||p==='dark')?p:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');" +
  "document.documentElement.dataset.theme=t;}catch(e){}})();";
