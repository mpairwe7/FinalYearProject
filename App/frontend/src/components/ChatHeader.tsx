"use client";

import Image from 'next/image';
import Link from 'next/link';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import ThemeToggle from './ThemeToggle';
import LanguageMenu, { LanguageOption } from './LanguageMenu';
import { useIdentity } from '../hooks/useIdentity';
import { useTheme } from '../hooks/useTheme';
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
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const menuItemRefs = useRef<(HTMLElement | null)[]>([]);

  const closeMenu = useCallback((restoreFocus = false) => {
    setMenuOpen(false);
    if (restoreFocus) menuButtonRef.current?.focus();
  }, []);

  /**
   * Close the menu, hand focus back to the button that owns it, then act.
   *
   * The focus hand-back is the WAI-ARIA menu-button pattern (APG: activating
   * a menuitem closes the menu and returns focus to the button), and here it
   * also decides where focus lands after a dialog opened from this menu is
   * closed again. SettingsDialog remembers whatever is focused as it opens
   * and restores it on close; the menuitem is unmounted by then, so without
   * this the dialog remembered <body> and Escape stranded keyboard and
   * screen-reader users at the top of the page. Focusing here — synchronously
   * in the handler, while the menu is still mounted — leaves it a live element
   * to remember. `action` runs last so it sees the settled focus.
   */
  const closeMenuThen = (action?: () => void) => {
    closeMenu(true);
    action?.();
  };
  // Only a confirmed sign-in hides the call to action: while the token is being
  // checked the buttons stay, and they come back if it turns out to be stale.
  const { status } = useIdentity();
  const signedIn = status === 'signed-in';
  const { pref: themePref, cycle: cycleTheme } = useTheme();
  const themeLabel = themePref === 'light' ? 'Light' : themePref === 'dark' ? 'Dark' : 'Auto';

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeMenu(true);
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    // This is a WAI-ARIA menu, not a tab-order popup: move focus into its
    // first item as it opens so Arrow keys work immediately.
    menuItemRefs.current[0]?.focus();
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen, closeMenu]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const items = menuItemRefs.current.filter(
      (item): item is HTMLElement => Boolean(item) && !(item instanceof HTMLButtonElement && item.disabled),
    );
    if (!items.length) return;
    const currentIndex = Math.max(0, items.indexOf(event.currentTarget));
    const move = (next: number) => items[(next + items.length) % items.length]?.focus();

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        move(currentIndex + 1);
        break;
      case 'ArrowUp':
        event.preventDefault();
        move(currentIndex - 1);
        break;
      case 'Home':
        event.preventDefault();
        items[0]?.focus();
        break;
      case 'End':
        event.preventDefault();
        items.at(-1)?.focus();
        break;
      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        closeMenu(true);
        break;
    }
  };

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
      <button
        className="top-bar-icon-btn"
        onClick={onNewChat}
        aria-label="New conversation"
        title="New chat"
      >
        <PlusIcon />
      </button>
      <LanguageMenu
        locale={locale}
        options={localeOptions}
        onLocaleChange={onLocaleChange}
      />
      <div className="hdrv2-theme">
        <ThemeToggle />
      </div>
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
          ref={menuButtonRef}
          className="top-bar-icon-btn"
          aria-label="More options"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-controls={menuOpen ? 'header-options-menu' : undefined}
          onClick={() => setMenuOpen((v) => !v)}
          onKeyDown={(event) => {
            if (!menuOpen && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
              event.preventDefault();
              setMenuOpen(true);
            }
          }}
        >
          <KebabIcon />
        </button>
        {menuOpen && (
          <div id="header-options-menu" className="hdrv2-menu" role="menu">
            <button
              ref={(element) => {
                menuItemRefs.current[0] = element;
              }}
              role="menuitem"
              className="hdrv2-menu-item hdrv2-menu-theme"
              onClick={() => {
                cycleTheme();
              }}
              aria-label={`Theme: ${themeLabel}. Click to switch.`}
              onKeyDown={onMenuKeyDown}
            >
              Theme: {themeLabel}
            </button>
            <button
              ref={(element) => {
                menuItemRefs.current[1] = element;
              }}
              role="menuitem"
              className="hdrv2-menu-item"
              onClick={() => closeMenuThen(onOpenSettings)}
              aria-label="Settings"
              onKeyDown={onMenuKeyDown}
            >
              <SettingsIcon /> Settings
            </button>
            <button
              ref={(element) => {
                menuItemRefs.current[2] = element;
              }}
              role="menuitem"
              className="hdrv2-menu-item"
              onClick={() => closeMenuThen(onRequestClear)}
              disabled={!hasStartedChat}
              aria-label="Clear conversation"
              onKeyDown={onMenuKeyDown}
            >
              <TrashIcon /> Clear conversation
            </button>
            <a
              ref={(element) => {
                menuItemRefs.current[3] = element;
              }}
              role="menuitem"
              className="hdrv2-menu-item"
              href={blogUrl}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Project blog"
              onClick={() => closeMenuThen()}
              onKeyDown={onMenuKeyDown}
            >
              <BookIcon /> Project blog ↗
            </a>
          </div>
        )}
      </div>
    </header>
  );
}
