"use client";

import { useCallback, useSyncExternalStore } from "react";
import {
  getSidebarMode,
  setSidebarMode as writeSidebarMode,
  SIDEBAR_EVENT,
  type SidebarMode,
} from "../lib/sidebarMode";

function subscribe(onChange: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(SIDEBAR_EVENT, onChange);
  // Another tab pinning the rail should settle this one too.
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(SIDEBAR_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/**
 * Current sidebar mode + a toggle.
 *
 * The server snapshot is the default rather than the stored value, because the
 * server cannot know it; the pre-paint script in lib/sidebarMode.ts is what
 * keeps a pinned rail from animating open after hydration.
 */
export function useSidebarMode(): {
  mode: SidebarMode;
  setMode: (next: SidebarMode) => void;
} {
  const mode = useSyncExternalStore(
    subscribe,
    getSidebarMode,
    () => "hover" as SidebarMode,
  );
  const setMode = useCallback((next: SidebarMode) => writeSidebarMode(next), []);
  return { mode, setMode };
}

export type { SidebarMode };
