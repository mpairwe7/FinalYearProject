"use client";

import Image from 'next/image';
import Link from 'next/link';
import React, { useEffect, useRef, useState } from 'react';
import ThemeToggle from './ThemeToggle';
import LanguageMenu, { LanguageOption } from './LanguageMenu';
import { useIdentity } from '../hooks/useIdentity';
import {
  BookIcon,
  KebabIcon,
  MenuIcon,
  PanelLeftIcon,
  PlusIcon,
  SettingsIcon,
  TrashIcon,
} from './Icons';

/**
 * chatv2 header: sidebar toggles, brand, then a compact action cluster.
 * Conversation-level controls (language, voice, attach) live in the composer;
 * the header keeps navigation, theme, and an overflow menu with Settings, Clear
 * and the blog link. All aria-labels match the legacy header so tests and AT
 * behavior carry over.
 *
 * Two speech controls used to sit here and no longer do. The "Voice ready"
 * status pill reported a backend detail continuously in the corner of every
 * screen — it earned its space only in the seconds after a failure, and the
 * composer's mic already goes quiet and explains itself when speech is
 * unreachable, which is where someone is looking when it matters. The
 * voice-overlay mic was a second, differently-shaped entry into speech sitting
 * a few centimetres from the composer's own: two mics in one viewport, neither
 * saying which was which. Speech now has one home, in the composer.
 *
 * Signed out, the header also carries the Sign in / Sign up pair. The sidebar's
 * account block is the primary home for those, but it is behind the hamburger
 * on a phone — and the first screen someone lands on is this one.
 */
interface ChatHeaderProps {
  hasStartedChat: boolean;
  onOpenSidebarMobile: () => void;
  onToggleRailCollapse: () => void;
  onNewChat: () => void;
  /** Already confirm-wrapped by the page. */
  onRequestClear: () => void;
  onOpenSettings: () => void;
  blogUrl: string;
  /* Language is a session-level setting — it governs the reply language, the
     TTS voice and STT recognition for the whole conversation — so it sits with
     the other settings here rather than in the composer toolbar. */
  locale: string;
  localeOptions: readonly LanguageOption[];
  onLocaleChange: (code: string) => void;
}

export default function ChatHeader({
  hasStartedChat,
  onOpenSidebarMobile,
  onToggleRailCollapse,
  onNewChat,
  onRequestClear,
  onOpenSettings,
  blogUrl,
  locale,
  localeOptions,
  onLocaleChange,
}: ChatHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  // Only a confirmed sign-in hides the call to action: while the token is being
  // checked the buttons stay, and they come back if it turns out to be stale.
  const { status } = useIdentity();
  const signedIn = status === 'signed-in';

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  return (
    <header className="hdrv2">
      <button
        className="top-bar-icon-btn hdrv2-collapse"
        onClick={onToggleRailCollapse}
        aria-label="Toggle conversation history"
        title="Toggle sidebar"
      >
        <PanelLeftIcon />
      </button>
      <button
        className="top-bar-icon-btn sidebar-toggle-btn"
        onClick={onOpenSidebarMobile}
        aria-label="Open conversation history"
      >
        <MenuIcon />
      </button>
      <div className="top-bar-brand">
        <Image
          src="/ura-assistant-logo.svg"
          alt="URA Assistant"
          className="top-bar-logo-img"
          width={28}
          height={28}
          priority
        />
        <span className="top-bar-title">URA Tax Assistant</span>
      </div>
      <div className="hdrv2-spacer" />
      {hasStartedChat && (
        <button
          className="top-bar-icon-btn"
          onClick={onNewChat}
          aria-label="New conversation"
          title="New chat"
        >
          <PlusIcon />
        </button>
      )}
      <LanguageMenu
        locale={locale}
        options={localeOptions}
        onLocaleChange={onLocaleChange}
      />
      <ThemeToggle />
      {!signedIn && (
        <div className="hdrv2-auth">
          <Link className="hdrv2-signin" href="/signin">
            Sign in
          </Link>
          <Link className="hdrv2-signup" href="/signup">
            Sign up
          </Link>
        </div>
      )}
      <div className="hdrv2-kebab" ref={menuRef}>
        <button
          className="top-bar-icon-btn"
          aria-label="More options"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
        >
          <KebabIcon />
        </button>
        {menuOpen && (
          <div className="hdrv2-menu" role="menu">
            <button
              role="menuitem"
              className="hdrv2-menu-item"
              onClick={() => {
                setMenuOpen(false);
                onOpenSettings();
              }}
              aria-label="Settings"
            >
              <SettingsIcon /> Settings
            </button>
            <button
              role="menuitem"
              className="hdrv2-menu-item"
              onClick={() => {
                setMenuOpen(false);
                onRequestClear();
              }}
              disabled={!hasStartedChat}
              aria-label="Clear conversation"
            >
              <TrashIcon /> Clear conversation
            </button>
            <a
              role="menuitem"
              className="hdrv2-menu-item"
              href={blogUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Project blog"
              onClick={() => setMenuOpen(false)}
            >
              <BookIcon /> Project blog ↗
            </a>
          </div>
        )}
      </div>
    </header>
  );
}
