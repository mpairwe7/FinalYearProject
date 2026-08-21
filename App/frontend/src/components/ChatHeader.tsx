"use client";

import React, { useCallback, useEffect, useRef, useState } from 'react';
import LanguageMenu, { LanguageOption } from './LanguageMenu';
import ConversationMenu from './ConversationMenu';
import { useTheme } from '../hooks/useTheme';
import {
  AutoThemeIcon,
  BookIcon,
  ChevronDownIcon,
  GlobeIcon,
  KebabIcon,
  MoonIcon,
  PanelLeftIcon,
  SettingsIcon,
  SunIcon,
  TrashIcon,
} from './Icons';

/**
 * chatv2 top strip — what is left of the header after the navbar was removed.
 *
 * The bar is gone as a bar: no brand, no border, no background. What remains is
 * three things floating over the canvas — the sidebar toggle at top left when
 * the rail is away, the current conversation's title beside it, and a single
 * 3-dot menu at top right. The transcript scrolls up underneath and fades out
 * (the mask lives on `.message-list`), so no line divides this strip from the
 * conversation.
 *
 * Everything that used to sit here has moved to where it belongs:
 *  - "New chat" and the brand are the sidebar's, which is always one click away.
 *  - Sign in / Sign up are the sidebar's account block, their primary home.
 *  - Theme and language are settings, so they are menu items in the 3-dot menu
 *    rather than two more permanent icons.
 *
 * The menu is a WAI-ARIA menu, not a tab-order popup: focus moves into the
 * first item as it opens, Arrow keys and Home/End move within it, and Escape
 * closes it and hands focus back to the button that owns it.
 */
interface ChatHeaderProps {
  hasStartedChat: boolean;
  /** Mobile drawer opener — desktop uses `onToggleRailCollapse` instead. */
  onOpenSidebarMobile: () => void;
  onToggleRailCollapse: () => void;
  /** True while the desktop rail is collapsed, which is when the toggle shows here. */
  railCollapsed: boolean;
  /** Hover on the collapsed toggle peeks the rail open — see page.tsx. */
  onPeekEnter: () => void;
  onPeekLeave: () => void;
  /** Already confirm-wrapped by the page. */
  onRequestClear: () => void;
  onOpenSettings: () => void;
  blogUrl: string;
  /* Language is a session-level setting — it governs the reply language, the
     TTS voice and STT recognition for the whole conversation — so it sits with
     the other settings in this menu rather than in the composer toolbar. */
  locale: string;
  localeOptions: readonly LanguageOption[];
  onLocaleChange: (code: string) => void;
  /** Current conversation, shown at top left. Absent on the landing screen. */
  conversationTitle?: string;
  conversationPinned?: boolean;
  onPinConversation?: () => void;
  /** Commits a new title. The inline editor is owned here, beside the title. */
  onRenameConversation?: (title: string) => void;
  onDeleteConversation?: () => void;
}

export default function ChatHeader({
  hasStartedChat,
  onOpenSidebarMobile,
  onToggleRailCollapse,
  railCollapsed,
  onPeekEnter,
  onPeekLeave,
  onRequestClear,
  onOpenSettings,
  blogUrl,
  locale,
  localeOptions,
  onLocaleChange,
  conversationTitle,
  conversationPinned = false,
  onPinConversation,
  onRenameConversation,
  onDeleteConversation,
}: ChatHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [langOpen, setLangOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState('');
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
  const { pref: themePref, cycle: cycleTheme } = useTheme();
  const themeLabel = themePref === 'light' ? 'Light' : themePref === 'dark' ? 'Dark' : 'Auto';
  // Every other item in this menu leads with an icon; without one here the
  // label starts in the icon column and the whole list looks misaligned.
  const ThemeIcon = themePref === 'light' ? SunIcon : themePref === 'dark' ? MoonIcon : AutoThemeIcon;
  const currentLocale = localeOptions.find((o) => o.value === locale) ?? localeOptions[0];

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

  const showTitle = hasStartedChat && Boolean(conversationTitle);

  const commitRename = () => {
    setRenaming(false);
    onRenameConversation?.(draft);
  };

  return (
    <header className="hdrv2">
      {/* Desktop: only shown while the rail is away — otherwise the rail owns
          its own toggle, in its brand row. Hovering peeks; clicking docks. */}
      {railCollapsed && (
        <button
          className="top-bar-icon-btn hdrv2-collapse"
          onClick={onToggleRailCollapse}
          onMouseEnter={onPeekEnter}
          onMouseLeave={onPeekLeave}
          onFocus={onPeekEnter}
          onBlur={onPeekLeave}
          aria-label="Toggle conversation history"
          title="Open sidebar  Ctrl+B"
        >
          <PanelLeftIcon />
        </button>
      )}
      {/* Mobile drawer opener. Same control as before and the same accessible
          name; only the glyph changed from a hamburger to the panel icon. */}
      <button
        className="top-bar-icon-btn sidebar-toggle-btn"
        onClick={onOpenSidebarMobile}
        aria-label="Open conversation history"
      >
        <PanelLeftIcon />
      </button>

      {showTitle && renaming && (
        <input
          className="hdrv2-convrename"
          value={draft}
          aria-label={`Rename ${conversationTitle}`}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commitRename();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setRenaming(false);
            }
          }}
          onBlur={commitRename}
        />
      )}

      {showTitle && !renaming && (
        <ConversationMenu
          pinned={conversationPinned}
          onPin={() => onPinConversation?.()}
          onRename={() => {
            setDraft(conversationTitle ?? '');
            setRenaming(true);
          }}
          onDelete={() => onDeleteConversation?.()}
          triggerLabel={`Conversation options: ${conversationTitle}`}
          triggerClassName="hdrv2-convtitle"
          align="start"
          triggerChildren={
            <>
              <span className="hdrv2-convtitle-text">{conversationTitle}</span>
              <ChevronDownIcon />
            </>
          }
        />
      )}

      <div className="hdrv2-spacer" />

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
              className="hdrv2-menu-item"
              onClick={() => closeMenuThen(() => setLangOpen(true))}
              aria-label={`Response language: ${currentLocale.label}`}
              onKeyDown={onMenuKeyDown}
            >
              <GlobeIcon /> Response language: {currentLocale.label}
            </button>
            <button
              ref={(element) => {
                menuItemRefs.current[1] = element;
              }}
              role="menuitem"
              className="hdrv2-menu-item hdrv2-menu-theme"
              onClick={() => {
                cycleTheme();
              }}
              aria-label={`Theme: ${themeLabel}. Click to switch.`}
              onKeyDown={onMenuKeyDown}
            >
              <ThemeIcon /> Theme: {themeLabel}
            </button>
            <button
              ref={(element) => {
                menuItemRefs.current[2] = element;
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
                menuItemRefs.current[3] = element;
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
                menuItemRefs.current[4] = element;
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

      {/* The picker itself is unchanged — only its trigger moved into the menu
          above, so the overlay is driven from here instead of a header button. */}
      <LanguageMenu
        locale={locale}
        options={localeOptions}
        onLocaleChange={onLocaleChange}
        hideTrigger
        controlledOpen={langOpen}
        onRequestClose={() => setLangOpen(false)}
        returnFocusRef={menuButtonRef}
      />
    </header>
  );
}
