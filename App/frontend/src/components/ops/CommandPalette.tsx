"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import { staffSectionsFor, type StaffDestination } from "../../lib/roles";
import { CommandIcon } from "./icons";

/**
 * ⌘K / Ctrl-K — jump to any console page from anywhere.
 *
 * The workbench already had good keyboard ergonomics inside a page (j/k to move
 * the queue, `/` to search, `r` to reply, `a` to assign) but no way to *leave*
 * one without going back to the mouse, and the only hint that any of it existed
 * was an 11px line of text on one page. A palette is the conventional answer —
 * Linear, GitHub, Stripe and Vercel all put it on the same chord — and it
 * doubles as the place those hotkeys are documented.
 *
 * Role-scoped from the same destination list the nav renders, so the palette
 * can never offer a page the signed-in role would be refused.
 */

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  run: () => void;
}

export function useCommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return { open, setOpen };
}

export function CommandPaletteTrigger({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className="ops-cmdk-trigger" onClick={onOpen} aria-haspopup="dialog">
      <CommandIcon />
      Search
      <kbd className="ops-kbd">⌘K</kbd>
    </button>
  );
}

/**
 * Mounted only while open, so the query and the highlighted row start fresh
 * every time without an effect resetting them after the fact.
 */
export function CommandPalette(props: {
  role: string;
  open: boolean;
  onClose: () => void;
  onSignOut: () => void;
}) {
  if (!props.open) return null;
  return <PaletteDialog {...props} />;
}

function PaletteDialog({
  role,
  onClose,
  onSignOut,
}: {
  role: string;
  onClose: () => void;
  onSignOut: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const { pref, cycle } = useTheme();

  const go = useCallback(
    (href: string) => () => {
      window.location.assign(href);
    },
    [],
  );

  const commands = useMemo<Command[]>(() => {
    const pages: Command[] = staffSectionsFor(role).flatMap((section) =>
      section.items.map((item: StaffDestination) => ({
        id: `go:${item.href}`,
        label: item.navLabel,
        hint: item.blurb,
        group: section.label,
        run: go(item.href),
      })),
    );
    return [
      ...pages,
      {
        id: "action:theme",
        label: `Theme: ${pref}`,
        hint: "Cycle Auto → Light → Dark",
        group: "Actions",
        run: cycle,
      },
      {
        id: "action:chat",
        label: "Back to the assistant",
        hint: "The taxpayer-facing chat",
        group: "Actions",
        run: go("/"),
      },
      { id: "action:signout", label: "Sign out", group: "Actions", run: onSignOut },
    ];
  }, [role, pref, cycle, go, onSignOut]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((command) =>
      `${command.label} ${command.hint ?? ""} ${command.group}`.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // Focus the input on mount and hand focus back to whatever opened the palette
  // when it goes. Both are DOM work, not state.
  useEffect(() => {
    const restoreTo = document.activeElement as HTMLElement | null;
    const raf = requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      cancelAnimationFrame(raf);
      restoreTo?.focus?.();
    };
  }, []);

  // Keep the highlighted row in view when arrowing past the fold.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(".ops-cmdk-item.is-active")
      ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!results.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((prev) => (prev + step + results.length) % results.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const command = results[active];
      if (!command) return;
      onClose();
      command.run();
    }
  };

  let lastGroup = "";

  return (
    <div
      className="ops-cmdk-scrim"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="ops-cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onKeyDown={onKeyDown}
      >
        <input
          ref={inputRef}
          className="ops-cmdk-input"
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="ops-cmdk-list"
          aria-activedescendant={results[active] ? `ops-cmd-${active}` : undefined}
          aria-autocomplete="list"
          placeholder="Jump to a page or run an action…"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
        />
        <ul className="ops-cmdk-list" id="ops-cmdk-list" role="listbox" ref={listRef}>
          {results.length === 0 ? (
            <li className="ops-cmdk-group">Nothing matches “{query}”</li>
          ) : null}
          {results.map((command, index) => {
            const header = command.group !== lastGroup ? command.group : null;
            lastGroup = command.group;
            return (
              <React.Fragment key={command.id}>
                {header ? (
                  <li className="ops-cmdk-group" role="presentation">
                    {header}
                  </li>
                ) : null}
                <li role="presentation">
                  <button
                    type="button"
                    id={`ops-cmd-${index}`}
                    role="option"
                    aria-selected={index === active}
                    className={`ops-cmdk-item${index === active ? " is-active" : ""}`}
                    onMouseEnter={() => setActive(index)}
                    onClick={() => {
                      onClose();
                      command.run();
                    }}
                  >
                    {command.label}
                    {command.hint ? <span className="ops-cmdk-item-sub">{command.hint}</span> : null}
                  </button>
                </li>
              </React.Fragment>
            );
          })}
        </ul>
        <div className="ops-cmdk-foot ops-hints">
          <span className="ops-hint">
            <kbd className="ops-kbd">↑</kbd>
            <kbd className="ops-kbd">↓</kbd>
            move
          </span>
          <span className="ops-hint">
            <kbd className="ops-kbd">↵</kbd>
            open
          </span>
          <span className="ops-hint">
            <kbd className="ops-kbd">esc</kbd>
            close
          </span>
        </div>
      </div>
    </div>
  );
}
