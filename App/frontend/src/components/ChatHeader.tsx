"use client";

import Image from 'next/image';
import React, { useEffect, useRef, useState } from 'react';
import ThemeToggle from './ThemeToggle';
import LanguageMenu, { LanguageOption } from './LanguageMenu';
import {
  BookIcon,
  HeadphonesIcon,
  KebabIcon,
  MenuIcon,
  MicIcon,
  PanelLeftIcon,
  PlusIcon,
  TrashIcon,
} from './Icons';

/**
 * chatv2 header: sidebar toggles, brand, then a compact action cluster.
 * Conversation-level controls (language, Voice, Narrate, attach) live in the
 * composer; the header keeps navigation, theme, the voice-overlay entry, the
 * speech-health status pill, and an overflow menu with Clear + the blog link.
 * All aria-labels match the legacy header so tests and AT behavior carry over.
 */
interface ChatHeaderProps {
  hasStartedChat: boolean;
  onOpenSidebarMobile: () => void;
  onToggleRailCollapse: () => void;
  onNewChat: () => void;
  /** Already confirm-wrapped by the page. */
  onRequestClear: () => void;
  voiceChatOpen: boolean;
  onToggleVoiceChat: () => void;
  blogUrl: string;
  healthOk: boolean;
  healthLabel: string;
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
  voiceChatOpen,
  onToggleVoiceChat,
  blogUrl,
  healthOk,
  healthLabel,
  locale,
  localeOptions,
  onLocaleChange,
}: ChatHeaderProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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
      <button
        className="top-bar-icon-btn"
        onClick={onToggleVoiceChat}
        aria-label={voiceChatOpen ? 'Close voice chat' : 'Open voice chat'}
        title="Voice-first mode"
        style={voiceChatOpen ? { color: 'var(--cv2-accent, #00a88f)' } : undefined}
      >
        <MicIcon />
      </button>
      <ThemeToggle />
      <div
        className={`pill-sm ${healthOk ? 'pill-ok' : 'pill-warn'}`}
        aria-live="polite"
      >
        <HeadphonesIcon /> <span className="pill-sm-label">{healthLabel}</span>
      </div>
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
