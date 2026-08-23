/**
 * Whether the operations sidebar stays open or expands on hover.
 *
 * Two modes rather than open/closed, because the rail is useful in both states
 * and people want different things from it: an operator working a queue all day
 * wants the labels pinned, while someone dipping into the console wants the
 * screen back. Collapsed is the default — the console's own pages are wide
 * tables, and 208px of permanent chrome pushed ticket columns off a laptop.
 *
 * Same shape as lib/theme.ts, deliberately: a pre-paint inline script stamps
 * the root element so a pinned rail is already 208px in the first frame, and
 * the React side reads it through useSyncExternalStore. The alternative —
 * reading localStorage in an effect — both animates the rail open on every
 * load for pinned users and trips react-hooks/set-state-in-effect.
 */
export type SidebarMode = "hover" | "always-open";

const KEY = "ura.ops.sidebarMode";
export const SIDEBAR_EVENT = "ura:sidebar-mode";

export function getSidebarMode(): SidebarMode {
  if (typeof window === "undefined") return "hover";
  try {
    return window.localStorage.getItem(KEY) === "always-open" ? "always-open" : "hover";
  } catch {
    // Private mode / storage disabled. The preference is a nicety, not a
    // requirement, so fall back to the default rather than breaking the shell.
    return "hover";
  }
}

export function setSidebarMode(mode: SidebarMode): void {
  if (typeof window === "undefined") return;
  try {
    if (mode === "always-open") window.localStorage.setItem(KEY, mode);
    else window.localStorage.removeItem(KEY);
  } catch {
    // Same reasoning as getSidebarMode.
  }
  document.documentElement.dataset.railMode = mode;
  window.dispatchEvent(new CustomEvent(SIDEBAR_EVENT));
}

/** The inline string evaluated before paint in the document head. */
export const SIDEBAR_INIT_SCRIPT =
  "(function(){try{var m=localStorage.getItem('ura.ops.sidebarMode');" +
  "document.documentElement.dataset.railMode=(m==='always-open')?'always-open':'hover';}catch(e){}})();";
