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
import React, { useEffect, useRef, useState } from "react";
import { useIdentity } from "../hooks/useIdentity";
import { staffDestinationsFor } from "../lib/roles";
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
  const { status, name, roleName, initials, identity, isStaff, signOut } = useIdentity();
  const [menuOpen, setMenuOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const settingsRow = (
    <button
      type="button"
      className="rail-acct-row"
      onClick={() => onOpenSettings()}
      aria-label="Open settings"
    >
      <SettingsIcon />
      <span>Settings</span>
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
              : "Sign in to keep your conversations and profile."}
          </p>
          <div className="rail-acct-btns">
            <Link className="rail-acct-primary" href="/signin">
              <SignInIcon />
              Sign in
            </Link>
            <Link className="rail-acct-ghost" href="/signup">
              <UserPlusIcon />
              Sign up
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
            type="button"
            role="menuitem"
            className="rail-acct-item"
            onClick={() => {
              setMenuOpen(false);
              onOpenSettings();
            }}
          >
            <SettingsIcon /> Settings
          </button>
          {destinations.map((d) => (
            <a key={d.href} role="menuitem" className="rail-acct-item" href={d.href}>
              <ShieldIcon /> {d.label}
            </a>
          ))}
          <button
            type="button"
            role="menuitem"
            className="rail-acct-item rail-acct-item-danger"
            onClick={() => {
              setMenuOpen(false);
              signOut();
            }}
          >
            <SignOutIcon /> Sign out
          </button>
        </div>
      )}

      <button
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
