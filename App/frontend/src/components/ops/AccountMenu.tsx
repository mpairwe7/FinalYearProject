"use client";

/**
 * The console sidebar's account row, and the menu it opens.
 *
 * What was there before was the account *surface* spelled out flat: a role
 * pill, the raw email address, and a bare "Sign out" button, stacked in a 208px
 * column and taking three lines of the rail to say who you are. Every
 * sidebar-and-account layout people already use — Claude, ChatGPT, Linear —
 * spends one line on it: initials, a name, the account type, a chevron. This is
 * that row, and everything the three stacked pieces used to offer moved into
 * the menu behind it.
 *
 * The initials are the account *type* (AD / ST / AU), not a person's: an OIDC
 * token carries an email and a role and no name, so initials of a name would be
 * invented. See `roleInitials` in lib/roles.ts.
 *
 * The menu opens upward — the row is the last thing in the rail — and it is
 * laid out to fit inside the expanded rail's width, because `.staff-rail` is
 * `overflow: hidden` and anything wider is clipped. That is also why Language
 * expands in place instead of flying out to the side.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  accountDisplayName,
  roleInitials,
  roleShortLabel,
  signedInName,
} from "../../lib/roles";
import { LOCALE_OPTIONS } from "../../lib/locales";
import { setThemePref, type ThemePref } from "../../lib/theme";
import { useTheme } from "../../hooks/useTheme";
import { useChatStore } from "../../store/useChatStore";
import {
  AutoThemeIcon,
  GlobeIcon,
  MoonIcon,
  SettingsIcon,
  SignOutIcon,
  SunIcon,
} from "../Icons";
import { CheckIcon, ChevronDownIcon, HelpCircleIcon } from "./icons";

export interface AccountMenuIdentity {
  role: string;
  email?: string;
  external_id?: string;
}

const THEME_OPTIONS: readonly { value: ThemePref; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Follow the device" },
  { value: "light", label: "Light", hint: "" },
  { value: "dark", label: "Dark", hint: "" },
];

const THEME_ICON = { auto: AutoThemeIcon, light: SunIcon, dark: MoonIcon } as const;

/** Only one section is expanded at a time — two open lists in a 191px menu is
 *  a scrolling column, and they are alternatives, not a checklist. */
type Section = "theme" | "language" | null;

export default function AccountMenu({
  who,
  open,
  onOpenChange,
  onOpenSettings,
  onSignOut,
  connected,
}: {
  who: AccountMenuIdentity;
  /**
   * Lifted to the rail. In `hover` mode the rail collapses on pointer-leave,
   * and a menu inside a 52px column is a clipped sliver — so the rail keeps
   * itself expanded for as long as this is open.
   */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenSettings: () => void;
  onSignOut: () => void;
  /** Live-escalation socket state, shown as a dot on this row. */
  connected?: boolean;
}) {
  const [section, setSection] = useState<Section>(null);
  const locale = useChatStore((s) => s.locale);
  const setLocale = useChatStore((s) => s.setLocale);
  const { pref } = useTheme();
  const wrapRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const name = accountDisplayName(who);
  const type = roleShortLabel(who.role);
  const ThemeIcon = THEME_ICON[pref];
  const liveLabel = connected
    ? "Live escalations connected"
    : "Reconnecting to the escalation stream";

  const close = useCallback(
    (restoreFocus = false) => {
      onOpenChange(false);
      setSection(null);
      if (restoreFocus) triggerRef.current?.focus();
    },
    [onOpenChange],
  );

  const toggleSection = (next: Exclude<Section, null>) =>
    setSection((current) => (current === next ? null : next));

  /* Whatever rows are rendered right now, in document order. Read from the DOM
     rather than kept in a ref array because the language options join and leave
     the list as that section expands — an array would have to be rebuilt during
     render to stay in step, and a ref cannot be written there. */
  const items = useCallback(
    () =>
      Array.from(
        menuRef.current?.querySelectorAll<HTMLElement>(
          '[role="menuitem"], [role="menuitemradio"]',
        ) ?? [],
      ),
    [],
  );

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close(true);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    items()[0]?.focus();
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close, items]);

  const onItemKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const rows = items();
    if (!rows.length) return;
    const index = Math.max(0, rows.indexOf(event.currentTarget));
    const move = (next: number) => rows[(next + rows.length) % rows.length]?.focus();

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(index + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        move(index - 1);
        break;
      case "Home":
        event.preventDefault();
        rows[0]?.focus();
        break;
      case "End":
        event.preventDefault();
        rows.at(-1)?.focus();
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        break;
    }
  };

  return (
    <div className="staff-acct" ref={wrapRef}>
      {open ? (
        <div className="staff-acct-menu" role="menu" aria-label="Account" ref={menuRef}>
          {/* The address the footer used to print as a permanent line. It is
              identity, not a destination, so it heads the menu instead. */}
          <p className="staff-acct-menu-id" title={who.email || who.external_id}>
            {signedInName(who)}
          </p>

          <button
            type="button"
            role="menuitem"
            className="staff-acct-item"
            onClick={() => {
              close(true);
              onOpenSettings();
            }}
            onKeyDown={onItemKeyDown}
          >
            <SettingsIcon />
            <span>Settings</span>
          </button>

          {/* Was a bare icon button in the footer toolbar that cycled
              Auto → Light → Dark on click — three states behind one glyph, with
              no way to see what the other two were without pressing it. Here
              the three are named and the current one is ticked. */}
          <button
            type="button"
            role="menuitem"
            className={`staff-acct-item staff-acct-expand${section === "theme" ? " is-open" : ""}`}
            aria-expanded={section === "theme"}
            onClick={() => toggleSection("theme")}
            onKeyDown={onItemKeyDown}
          >
            <ThemeIcon />
            <span>Theme</span>
            <span className="staff-acct-trail" aria-hidden="true">
              <ChevronDownIcon />
            </span>
          </button>

          {section === "theme" ? (
            <div className="staff-acct-sub" role="group" aria-label="Theme">
              {THEME_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={option.value === pref}
                  className="staff-acct-item staff-acct-subitem"
                  onClick={() => {
                    setThemePref(option.value);
                    setSection(null);
                  }}
                  onKeyDown={onItemKeyDown}
                >
                  <span className="staff-acct-tick" aria-hidden="true">
                    {option.value === pref ? <CheckIcon /> : null}
                  </span>
                  <span>
                    {option.label}
                    {option.hint ? <span className="staff-acct-native"> · {option.hint}</span> : null}
                  </span>
                </button>
              ))}
            </div>
          ) : null}

          <button
            type="button"
            role="menuitem"
            className={`staff-acct-item staff-acct-expand${section === "language" ? " is-open" : ""}`}
            aria-expanded={section === "language"}
            onClick={() => toggleSection("language")}
            onKeyDown={onItemKeyDown}
          >
            <GlobeIcon />
            <span>Language</span>
            <span className="staff-acct-trail" aria-hidden="true">
              <ChevronDownIcon />
            </span>
          </button>

          {section === "language" ? (
            <div className="staff-acct-sub" role="group" aria-label="Response language">
              {LOCALE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={option.value === locale}
                  className="staff-acct-item staff-acct-subitem"
                  onClick={() => {
                    setLocale(option.value);
                    setSection(null);
                  }}
                  onKeyDown={onItemKeyDown}
                >
                  <span className="staff-acct-tick" aria-hidden="true">
                    {option.value === locale ? <CheckIcon /> : null}
                  </span>
                  <span>
                    {option.label}
                    {option.native !== option.label ? (
                      <span className="staff-acct-native"> · {option.native}</span>
                    ) : null}
                  </span>
                </button>
              ))}
              {/* Says what the setting actually governs. It is the assistant's
                  answer language, shared with the taxpayer chat — the console's
                  own text is English only (see lib/i18n). */}
              <p className="staff-acct-note">The language the assistant answers taxpayers in.</p>
            </div>
          ) : null}

          <a
            role="menuitem"
            className="staff-acct-item"
            href="/"
            target="_blank"
            rel="noopener noreferrer"
            onKeyDown={onItemKeyDown}
            onClick={() => close()}
          >
            <HelpCircleIcon />
            <span>Get help</span>
          </a>

          <button
            type="button"
            role="menuitem"
            className="staff-acct-item staff-acct-item-danger"
            onClick={() => {
              close();
              onSignOut();
            }}
            onKeyDown={onItemKeyDown}
          >
            <SignOutIcon />
            <span>Sign out</span>
          </button>
        </div>
      ) : null}

      <button
        ref={triggerRef}
        type="button"
        className="staff-acct-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        /* Spelled out rather than left to the text nodes. The name computation
           trims each element's text, so the visible "Officer · Admin" collapses
           to "Officer·Admin" — two words with no boundary between them. This is
           the visible label verbatim, which is what WCAG 2.5.3 asks for. */
        aria-label={name ? `${name} · ${type}` : type}
        onClick={() => onOpenChange(!open)}
        title={signedInName(who)}
      >
        <span className="staff-acct-avatar" aria-hidden="true">
          {roleInitials(who.role)}
        </span>
        {/* The separator is real text, not an aria-hidden decoration: hiding it
            left the button's accessible name as "OfficerAdmin" — two words run
            together — because the name is computed from text nodes and nothing
            else here supplies the space between them. */}
        <span className="staff-acct-label">
          {name ? (
            <>
              {name}
              <span className="staff-acct-dot"> · </span>
            </>
          ) : null}
          <span className="staff-acct-type">{type}</span>
        </span>
        {/* The escalation socket's state. It was a loose dot in the footer
            toolbar, on its own line above this row and aligned to nothing; it
            belongs against the identity it qualifies. Collapsed, CSS pins it to
            the avatar's corner, which is where a presence dot is read.
            Decorative here — the live region below is what gets announced, so
            the button's own name stays "Officer · Admin". */}
        <span
          className={`staff-live-dot${connected ? " is-on" : ""}`}
          title={liveLabel}
          aria-hidden="true"
        />
        <span className="staff-acct-caret" aria-hidden="true">
          <ChevronDownIcon />
        </span>
      </button>

      <span className="ops-sr-only" role="status">
        {liveLabel}
      </span>
    </div>
  );
}
