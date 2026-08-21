"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Conversation } from '../store/useChatStore';
import { filterConversations, sortConversations } from '../lib/conversationGroups';
import { useClientClock, useRelativeTime } from '../hooks/useClientClock';
import { restoreFocus } from '../lib/focus';
import { CloseIcon, MessageSquareIcon, SearchIcon } from './Icons';

/**
 * Search across saved conversations.
 *
 * A centred, focus-trapped overlay rather than an anchored panel, matching the
 * language picker next door: the rail is 300px wide and clips its children, so
 * anything anchored inside it would be cut off or would have to escape twice.
 *
 * Opening with an empty query lists everything — the overlay doubles as the
 * "jump to a conversation" list, which is how it is used most of the time. With
 * nothing saved yet it still opens, and says so; a search box that refuses to
 * appear is harder to understand than one that opens empty.
 *
 * Arrow keys move a roving highlight, Enter opens it, Escape closes. The list
 * is a listbox so a screen reader is told which row is active as it moves.
 */

interface ConversationSearchProps {
  conversations: Conversation[];
  onClose: () => void;
  onSelect: (id: string) => void;
  /** Where focus goes on close — the button that opened this. */
  returnFocusRef?: React.RefObject<HTMLElement | null>;
}

function ResultTime({ timestamp }: { timestamp: number }) {
  const label = useRelativeTime(timestamp);
  return <span className="csrch-time">{label}</span>;
}

export default function ConversationSearch({
  conversations,
  onClose,
  onSelect,
  returnFocusRef,
}: ConversationSearchProps) {
  const [query, setQuery] = useState('');
  const [rawActiveIdx, setActiveIdx] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Grouping is time-based, so it has to wait for the client clock or the
  // server and the browser disagree about what "Today" is.
  const now = useClientClock();

  const results = useMemo(
    () => filterConversations(sortConversations(conversations), query),
    [conversations, query],
  );

  /* Clamped during render rather than corrected in an effect: typing shrinks
     the result set, and a highlight left past the end would survive for a
     frame and could be committed by Enter. */
  const activeIdx = rawActiveIdx < results.length ? rawActiveIdx : 0;

  const close = useCallback(() => {
    onClose();
    restoreFocus(returnFocusRef?.current ?? null);
  }, [onClose, returnFocusRef]);

  useEffect(() => {
    inputRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prevOverflow;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled])',
      );
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  const onInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
      return;
    }
    if (!results.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIdx((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIdx((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const target = results[activeIdx];
      if (target) {
        onSelect(target.id);
        close();
      }
    }
  };

  return (
    <div
      className="csrch-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && close()}
    >
      <div
        ref={panelRef}
        className="csrch"
        role="dialog"
        aria-modal="true"
        aria-label="Search conversations"
      >
        <div className="csrch-head">
          <SearchIcon />
          <input
            ref={inputRef}
            className="csrch-input"
            type="text"
            value={query}
            placeholder="Search your conversations"
            aria-label="Search your conversations"
            aria-controls="csrch-results"
            autoComplete="off"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onInputKeyDown}
          />
          <button
            type="button"
            className="csrch-x"
            onClick={close}
            aria-label="Close search"
          >
            <CloseIcon />
          </button>
        </div>

        <div
          ref={listRef}
          id="csrch-results"
          className="csrch-list"
          role={results.length > 0 ? 'listbox' : undefined}
          aria-label={results.length > 0 ? 'Conversations' : undefined}
        >
          {results.map((c, i) => (
            <button
              key={c.id}
              type="button"
              role="option"
              aria-selected={i === activeIdx}
              data-active={i === activeIdx}
              className={`csrch-opt ${i === activeIdx ? 'csrch-opt-active' : ''}`}
              onMouseEnter={() => setActiveIdx(i)}
              onClick={() => {
                onSelect(c.id);
                close();
              }}
            >
              <MessageSquareIcon />
              <span className="csrch-title">{c.title}</span>
              {now > 0 && <ResultTime timestamp={c.updatedAt} />}
            </button>
          ))}

          {results.length === 0 && (
            <p className="csrch-empty">
              {conversations.length === 0
                ? 'No conversations yet. Ask a question and it will show up here.'
                : 'No conversations match that search.'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
