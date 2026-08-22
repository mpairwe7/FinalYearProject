"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Whether the operations sidebar stays open or expands on hover.
 *
 * Two modes rather than a toggle-open/closed, because the rail is useful in
 * both states and people want different things from it: an operator working a
 * queue all day wants the labels pinned, while someone dipping into the console
 * wants the screen back. Collapsed is the default — the console's own pages are
 * wide tables, and 208px of permanent chrome pushed the ticket columns off a
 * laptop.
 *
 * Persisted per browser, read once on mount rather than during render, so the
 * server and first client paint agree (`"hover"`) and only then settle to the
 * stored preference. Reading localStorage during render would hydrate-mismatch
 * every staff page.
 */
export type SidebarMode = "hover" | "always-open";

const STORAGE_KEY = "ura.ops.sidebarMode";

function readStored(): SidebarMode | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "hover" || raw === "always-open" ? raw : null;
  } catch {
    // Private mode / storage disabled — the preference is a nicety, not a
    // requirement, so fall back to the default rather than breaking the shell.
    return null;
  }
}

export function useSidebarMode(): {
  mode: SidebarMode;
  setMode: (next: SidebarMode) => void;
  /** False until the stored preference has been applied, so the sidebar can
   *  suppress its width transition on the very first paint. */
  ready: boolean;
} {
  const [mode, setModeState] = useState<SidebarMode>("hover");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = readStored();
    if (stored) setModeState(stored);
    setReady(true);
  }, []);

  const setMode = useCallback((next: SidebarMode) => {
    setModeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Same reasoning as readStored: preference only.
    }
  }, []);

  return { mode, setMode, ready };
}
