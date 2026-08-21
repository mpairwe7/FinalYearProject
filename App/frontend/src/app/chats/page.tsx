"use client";

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import React, { useMemo, useState } from 'react';
import { useChatStore } from '../../store/useChatStore';
import {
  filterConversations,
  groupConversations,
  sortConversations,
} from '../../lib/conversationGroups';
import { useClientClock, useRelativeTime } from '../../hooks/useClientClock';
import ConfirmDialog, { ConfirmRequest } from '../../components/ConfirmDialog';
import ConversationMenu from '../../components/ConversationMenu';
import { MessageSquareIcon, SearchIcon } from '../../components/Icons';

/**
 * Every saved conversation, with room to read them.
 *
 * The rail shows the recent ones and stops; past a dozen threads, finding an
 * older one there means scrolling a 300px column. This is where "View all
 * conversations" at the foot of the rail goes: the same threads, full width,
 * grouped by when they were last touched, with the same Pin / Rename / Delete
 * menu so nothing has to be done back in the rail.
 *
 * The store is populated by Providers at the root layout, so this route reads
 * the same localStorage-backed history as the chat. Opening a thread switches
 * the store and returns to the conversation.
 */

function GroupTime({ timestamp }: { timestamp: number }) {
  const label = useRelativeTime(timestamp);
  return <span className="chatspg-time">{label}</span>;
}

export default function ChatsPage() {
  const router = useRouter();
  const conversations = useChatStore((s) => s.conversations);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const renameSession = useChatStore((s) => s.renameSession);
  const togglePinSession = useChatStore((s) => s.togglePinSession);

  const [query, setQuery] = useState('');
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  const now = useClientClock();
  const filtered = useMemo(
    () => filterConversations(sortConversations(conversations), query),
    [conversations, query],
  );
  const pinned = useMemo(() => filtered.filter((c) => c.pinned), [filtered]);
  const rest = useMemo(() => filtered.filter((c) => !c.pinned), [filtered]);
  // Time grouping needs the client clock, or "Today" differs between the
  // server render and the browser.
  const grouped = useMemo(() => (now ? groupConversations(rest, now) : []), [rest, now]);

  const open = (id: string) => {
    switchSession(id);
    router.push('/');
  };

  const requestDelete = (id: string) => {
    const conv = conversations.find((c) => c.id === id);
    setConfirmReq({
      title: 'Delete conversation?',
      message: `"${conv?.title ?? 'This conversation'}" and its ${conv?.turns.length ?? 0} messages will be permanently deleted.`,
      confirmLabel: 'Delete',
      danger: true,
      action: () => deleteSession(id),
    });
  };

  const commitRename = (id: string) => {
    setRenamingId(null);
    renameSession(id, draft);
  };

  const renderRow = (id: string, title: string, updatedAt: number, isPinned: boolean) => (
    <li key={id} className="chatspg-row">
      {renamingId === id ? (
        <input
          className="chatspg-rename"
          value={draft}
          aria-label={`Rename ${title}`}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commitRename(id);
            } else if (e.key === 'Escape') {
              e.preventDefault();
              setRenamingId(null);
            }
          }}
          onBlur={() => commitRename(id)}
        />
      ) : (
        <button type="button" className="chatspg-open" onClick={() => open(id)}>
          <MessageSquareIcon />
          <span className="chatspg-title">{title}</span>
          {now > 0 && <GroupTime timestamp={updatedAt} />}
        </button>
      )}
      <ConversationMenu
        pinned={isPinned}
        onPin={() => togglePinSession(id)}
        onRename={() => {
          setDraft(title);
          setRenamingId(id);
        }}
        onDelete={() => requestDelete(id)}
        triggerLabel={`Options for ${title}`}
        triggerClassName="chatspg-menu"
        triggerChildren={<span aria-hidden="true">⋯</span>}
      />
    </li>
  );

  return (
    <div className="chatv2 chatspg-shell">
      <main id="main-content" className="chatspg" tabIndex={-1}>
        <div className="chatspg-head">
          <h1 className="chatspg-h1">Chats</h1>
          <Link className="chatspg-back" href="/">
            Back to chat
          </Link>
        </div>

        <div className="chatspg-search">
          <SearchIcon />
          <input
            type="text"
            value={query}
            placeholder="Search your conversations"
            aria-label="Search your conversations"
            autoComplete="off"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {conversations.length === 0 ? (
          <p className="chatspg-empty">
            No conversations yet. <Link href="/">Ask a question</Link> and it will show up here.
          </p>
        ) : filtered.length === 0 ? (
          <p className="chatspg-empty">No conversations match that search.</p>
        ) : (
          <>
            {pinned.length > 0 && (
              <section className="chatspg-group">
                <h2 className="chatspg-h2">Pinned</h2>
                <ul className="chatspg-list">
                  {pinned.map((c) => renderRow(c.id, c.title, c.updatedAt, true))}
                </ul>
              </section>
            )}
            {grouped.map(([group, items]) => (
              <section key={group} className="chatspg-group">
                <h2 className="chatspg-h2">{group}</h2>
                <ul className="chatspg-list">
                  {items.map((c) => renderRow(c.id, c.title, c.updatedAt, false))}
                </ul>
              </section>
            ))}
          </>
        )}
      </main>

      <ConfirmDialog
        confirm={confirmReq}
        onClose={(run) => {
          const req = confirmReq;
          setConfirmReq(null);
          if (run) req?.action();
        }}
      />
    </div>
  );
}
