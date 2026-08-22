"use client";

/**
 * The sidebar's bottom block: who you are, and Settings.
 *
 * This is where the app's auth entry points live, following the pattern the
 * sidebar-chat layout has settled on (Grok, ChatGPT, Claude): conversation list
 * above, account below, settings reached from the account. Signed out it is a
 * pair of calls to action; signed in it is an identity row that opens a popover
 * with Settings, whatever operations pages the role can open, and Sign out.
 *
 * It renders the same in both states in one respect: Settings is always
 * reachable, because most of what settings controls (theme, language, voice,
 * local data) needs no account at all.
 */

import Link from "next/link";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useIdentity } from "../hooks/useIdentity";
import { staffDestinationsFor } from "../lib/roles";
import { useTranslation } from "../lib/i18n";
import {
  ChevronUpDownIcon,
  SettingsIcon,
  ShieldIcon,
  SignInIcon,
  SignOutIcon,
  UserPlusIcon,
} from "./Icons";

interface AccountRailProps {
  /**
   * Opens the settings dialog on its default tab. Deliberately takes no tab
   * argument: both entry points here — the signed-out Settings row and the
   * account menu's Settings item — mean "settings", not "my account". The one
   * caller that wants the Account tab is the landing page's own link.
   */
  onOpenSettings: () => void;
}

export default function AccountRail({ onOpenSettings }: AccountRailProps) {
  const t = useTranslation();
  const { status, name, roleName, initials, identity, isStaff, signOut } = useIdentity();
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuItemRefs = useRef<(HTMLElement | null)[]>([]);

  const closeMenu = useCallback((restoreFocus = false) => {
    setMenuOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMenu(true);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    // A menu uses arrow-key navigation, so place focus in it as it opens.
    menuItemRefs.current[0]?.focus();
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen, closeMenu]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const items = menuItemRefs.current.filter((item): item is HTMLElement => Boolean(item));
    if (!items.length) return;
    const currentIndex = Math.max(0, items.indexOf(event.currentTarget));
    const move = (next: number) => items[(next + items.length) % items.length]?.focus();

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(currentIndex + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        move(currentIndex - 1);
        break;
      case "Home":
        event.preventDefault();
        items[0]?.focus();
        break;
      case "End":
        event.preventDefault();
        items.at(-1)?.focus();
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
        break;
    }
  };

  const settingsRow = (
    <button
      type="button"
      className="rail-acct-row"
      onClick={() => onOpenSettings()}
      aria-label="Open settings"
    >
      <SettingsIcon />
      <span>{t('account.settings')}</span>
    </button>
  );

  // A token that the backend has not accepted yet (or has refused) must not be
  // drawn as a signed-in identity — that is how a stale session becomes an
  // invisible failure. Anything other than a confirmed sign-in shows the
  // signed-out block, and Settings › Account explains which case it is.
  if (status !== "signed-in" || !identity) {
    return (
      <div className="rail-account">
        <div className="rail-acct-cta">
          <p className="rail-acct-pitch">
            {status === "rejected" || status === "unavailable"
              ? "Your saved sign-in is no longer valid."
              : t('account.prompt')}
          </p>
          <div className="rail-acct-btns">
            <Link className="rail-acct-primary" href="/signin">
              <SignInIcon />
              {t('account.signIn')}
            </Link>
            <Link className="rail-acct-ghost" href="/signup">
              <UserPlusIcon />
              {t('account.signUp')}
            </Link>
          </div>
        </div>
        {settingsRow}
      </div>
    );
  }

  const destinations = staffDestinationsFor(identity.role);

  return (
    <div className="rail-account" ref={wrapRef}>
      {menuOpen && (
        <div className="rail-acct-menu" role="menu" aria-label="Account">
          <button
            ref={(element) => {
              menuItemRefs.current[0] = element;
            }}
            type="button"
            role="menuitem"
            className="rail-acct-item"
            onClick={() => {
              closeMenu(true);
              onOpenSettings();
            }}
            onKeyDown={onMenuKeyDown}
          >
            <SettingsIcon /> Settings
          </button>
          {destinations.map((d, index) => (
            <a
              key={d.href}
              ref={(element) => {
                menuItemRefs.current[index + 1] = element;
              }}
              role="menuitem"
              className="rail-acct-item"
              href={d.href}
              onKeyDown={onMenuKeyDown}
            >
              <ShieldIcon /> {d.label}
            </a>
          ))}
          <button
            ref={(element) => {
              menuItemRefs.current[destinations.length + 1] = element;
            }}
            type="button"
            role="menuitem"
            className="rail-acct-item rail-acct-item-danger"
            onClick={() => {
              closeMenu(true);
              signOut();
            }}
            onKeyDown={onMenuKeyDown}
          >
            <SignOutIcon /> Sign out
          </button>
        </div>
      )}

      <button
        ref={triggerRef}
        type="button"
        className="rail-acct-row rail-acct-user"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((v) => !v)}
      >
        <span className="rail-avatar" aria-hidden="true">
          {initials}
        </span>
        <span className="rail-acct-who">
          <span className="rail-acct-name">{name}</span>
          <span className="rail-acct-role">
            {roleName}
            {isStaff ? " · URA staff" : ""}
          </span>
        </span>
        <ChevronUpDownIcon />
      </button>
    </div>
  );
}
