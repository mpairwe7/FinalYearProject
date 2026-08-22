"use client";

import Image from 'next/image';
import Link from 'next/link';
import React, { memo, useMemo, useState } from 'react';
import { Conversation } from '../store/useChatStore';
import { sortConversations } from '../lib/conversationGroups';
import AccountRail from './AccountRail';
import ConversationMenu from './ConversationMenu';
import { useTranslation } from '../lib/i18n';
import {
  ChevronDownIcon,
  KebabIcon,
  MessageSquareIcon,
  PanelLeftIcon,
  PlusIcon,
  SearchIcon,
} from './Icons';

/**
 * The conversation sidebar.
 *
 * It now owns the two controls that used to sit in the navbar — the panel
 * toggle and, beside it, a search icon — under the URA mark, the way Claude
 * puts its wordmark and those same two icons at the top of its rail. The old
 * "Chats" heading with an X, and the always-on search input below it, are gone:
 * the heading was a label for a panel whose contents already say what they are,
 * and a permanent text field spent rail height on something wanted rarely.
 * Search is a button that opens an overlay instead.
 *
 * Rows are one line. Two lines of title-plus-preview meant every row carried a
 * fragment of an answer nobody was reading, at twice the height, which is why
 * neither reference design does it. Timestamps and previews still exist, in the
 * search overlay and on /chats, where scanning them is the point.
 *
 * The rail is a persistent grid column at >=1024px and an off-canvas drawer
 * below that, which is why the toggle is two buttons: on desktop it collapses
 * the column, on a phone it closes the drawer. One button with a label that
 * changed by viewport would have to guess the viewport during SSR.
 */

/** Below this many threads the rail shows everything, so /chats adds nothing. */
const VIEW_ALL_THRESHOLD = 10;

interface ConversationRailProps {
  open: boolean;
  conversations: Conversation[];
  activeConversationId: string | null;
  onClose: () => void;
  onNewConversation: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void;
  onPinConversation: (id: string) => void;
  onOpenSearch: () => void;
  onToggleRailCollapse: () => void;
  /** Account block at the foot of the rail — settings and sign-in live there. */
  onOpenSettings: () => void;
}

/**
 * The rename editor is its own component so that mounting it *is* the reset:
 * `useState(title)` seeds the draft on the way in, rather than an effect
 * writing state after the row has already rendered with a stale one.
 */
function RenameRow({
  title,
  onCommit,
  onCancel,
}: {
  title: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(title);

  return (
    <div className="rail-item rail-item-renaming" role="listitem">
      <input
        className="rail-item-rename"
        value={draft}
        aria-label={`Rename ${title}`}
        autoFocus
        // Select the whole title so typing replaces it, as a file rename does.
        onFocus={(e) => e.currentTarget.select()}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onCommit(draft);
          } else if (e.key === 'Escape') {
            e.preventDefault();
            onCancel();
          }
        }}
        onBlur={() => onCommit(draft)}
      />
    </div>
  );
}

interface RowProps {
  conversation: Conversation;
  isActive: boolean;
  onStartRename: () => void;
  onSelect: () => void;
  onDelete: () => void;
  onPin: () => void;
}

function ConversationRow({
  conversation,
  isActive,
  onStartRename,
  onSelect,
  onDelete,
  onPin,
}: RowProps) {
  return (
    <div
      className={`rail-item ${isActive ? 'rail-item-active' : ''}`}
      role="listitem"
    >
      <button className="rail-item-main" onClick={onSelect}>
        <span className="rail-item-dot" aria-hidden="true" />
        <span className="rail-item-title">{conversation.title}</span>
      </button>
      <ConversationMenu
        pinned={Boolean(conversation.pinned)}
        onPin={onPin}
        onRename={onStartRename}
        onDelete={onDelete}
        triggerLabel={`Options for ${conversation.title}`}
        triggerClassName="rail-item-menu"
        triggerChildren={<KebabIcon />}
      />
    </div>
  );
}

function ConversationRailInner({
  open,
  conversations,
  activeConversationId,
  onClose,
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
  onPinConversation,
  onOpenSearch,
  onToggleRailCollapse,
  onOpenSettings,
}: ConversationRailProps) {
  const t = useTranslation();
  const [chatsOpen, setChatsOpen] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);

  const sorted = useMemo(() => sortConversations(conversations), [conversations]);
  const pinned = useMemo(() => sorted.filter((c) => c.pinned), [sorted]);
  const unpinned = useMemo(() => sorted.filter((c) => !c.pinned), [sorted]);

  const commitRename = (id: string, title: string) => {
    setRenamingId(null);
    onRenameConversation(id, title);
  };

  const renderRow = (conversation: Conversation) =>
    renamingId === conversation.id ? (
      <RenameRow
        key={conversation.id}
        title={conversation.title}
        onCommit={(title) => commitRename(conversation.id, title)}
        onCancel={() => setRenamingId(null)}
      />
    ) : (
      <ConversationRow
        key={conversation.id}
        conversation={conversation}
        isActive={conversation.id === activeConversationId}
        onStartRename={() => setRenamingId(conversation.id)}
        onSelect={() => onSelectConversation(conversation.id)}
        onDelete={() => onDeleteConversation(conversation.id)}
        onPin={() => onPinConversation(conversation.id)}
      />
    );

  return (
    <>
      <div
        className={`rail-overlay ${open ? 'rail-overlay-open' : ''}`}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside className={`conversation-rail ${open ? 'conversation-rail-open' : ''}`}>
        <div className="rail-brand">
          <Image
            src="/ura-assistant-logo.svg"
            alt=""
            aria-hidden="true"
            className="top-bar-logo-img"
            width={26}
            height={26}
            priority
          />
          <span className="top-bar-title">URA AI Assistant</span>
          <div className="rail-brand-actions">
            <button
              type="button"
              className="rail-icon-btn rail-toggle-desktop"
              onClick={onToggleRailCollapse}
              aria-label="Toggle conversation history"
              title={`${t('rail.close')}  Ctrl+B`}
            >
              <PanelLeftIcon />
            </button>
            <button
              type="button"
              className="rail-icon-btn rail-toggle-mobile"
              onClick={onClose}
              aria-label={t('rail.close')}
            >
              <PanelLeftIcon />
            </button>
            <button
              type="button"
              className="rail-icon-btn"
              onClick={onOpenSearch}
              aria-label={t('rail.search')}
              title={t('rail.search')}
            >
              <SearchIcon />
            </button>
          </div>
        </div>

        <button className="rail-new" onClick={onNewConversation}>
          <PlusIcon /> {t('rail.newChat')}
        </button>

        <div className="rail-scroll">
          {pinned.length > 0 && (
            <div className="rail-group">
              <div className="rail-group-label">Pinned</div>
              <div className="rail-list" role="list" aria-label="Pinned conversations">
                {pinned.map(renderRow)}
              </div>
            </div>
          )}

          <div className="rail-group">
            {/* The chevron is hover/focus-revealed: at rest the label is just a
                label, and the control appears where the pointer already is. */}
            <button
              type="button"
              className="rail-cat"
              aria-expanded={chatsOpen}
              aria-controls="rail-chats-list"
              onClick={() => setChatsOpen((v) => !v)}
            >
              <span className="rail-group-label">{t('rail.chats')}</span>
              <span className={`rail-cat-chevron ${chatsOpen ? '' : 'rail-cat-chevron-up'}`}>
                <ChevronDownIcon />
              </span>
            </button>

            {chatsOpen && (
              /* role="list" requires rendered listitem children (WCAG 1.3.1 /
                 axe aria-required-children), so drop the role when only the
                 empty-state placeholder is shown. */
              <div
                id="rail-chats-list"
                className="rail-list"
                role={unpinned.length > 0 ? 'list' : undefined}
                aria-label={unpinned.length > 0 ? 'Conversation threads' : undefined}
              >
                {unpinned.map(renderRow)}
                {conversations.length === 0 && (
                  <div className="rail-empty">
                    <MessageSquareIcon />
                    <span>{t('rail.empty')}</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {conversations.length >= VIEW_ALL_THRESHOLD && (
            <Link className="rail-view-all" href="/chats" onClick={onClose}>
              {t('rail.viewAll')}
            </Link>
          )}
        </div>

        <AccountRail onOpenSettings={onOpenSettings} />
      </aside>
    </>
  );
}

export default memo(ConversationRailInner);
