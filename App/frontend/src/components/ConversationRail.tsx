import React, { memo, useMemo, useState, useSyncExternalStore } from 'react';
import { Conversation } from '../store/useChatStore';
import AccountRail from './AccountRail';
import { CloseIcon, MessageSquareIcon, PlusIcon, TrashIcon } from './Icons';

interface ConversationRailProps {
  open: boolean;
  conversations: Conversation[];
  activeConversationId: string | null;
  onClose: () => void;
  onNewConversation: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void;
  /** Account block at the foot of the rail — settings and sign-in live there. */
  onOpenSettings: () => void;
}

function formatTimestamp(timestamp: number) {
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return 'now';
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h`;
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' }).format(timestamp);
}

let clockSnapshot = 0;
let clockTimer: number | null = null;
const clockListeners = new Set<() => void>();

function emitClockTick() {
  clockSnapshot = Date.now();
  for (const listener of clockListeners) listener();
}

function subscribeClock(listener: () => void) {
  if (typeof window === 'undefined') return () => {};
  clockListeners.add(listener);
  if (!clockTimer) {
    emitClockTick();
    clockTimer = window.setInterval(emitClockTick, 60_000);
  }
  return () => {
    clockListeners.delete(listener);
    if (clockListeners.size === 0 && clockTimer) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}

const getClockSnapshot = () => clockSnapshot;
const getServerClockSnapshot = () => 0;

function useClientClock(): number {
  return useSyncExternalStore(subscribeClock, getClockSnapshot, getServerClockSnapshot);
}

/** Suppress hydration mismatch for relative timestamps by deferring to client. */
function useRelativeTime(timestamp: number): string {
  const now = useClientClock();
  return now ? formatTimestamp(timestamp) : '';
}

function RelativeTime({ timestamp }: { timestamp: number }) {
  const label = useRelativeTime(timestamp);
  return <>{label}</>;
}

type TimeGroup = 'Today' | 'Yesterday' | 'Previous 7 days' | 'Previous 30 days' | 'Older';

function getTimeGroup(timestamp: number, now: number): TimeGroup {
  const d = new Date(now);
  const today = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const yesterday = today - 86_400_000;
  const week = today - 7 * 86_400_000;
  const month = today - 30 * 86_400_000;

  if (timestamp >= today) return 'Today';
  if (timestamp >= yesterday) return 'Yesterday';
  if (timestamp >= week) return 'Previous 7 days';
  if (timestamp >= month) return 'Previous 30 days';
  return 'Older';
}

function groupConversations(convs: Conversation[], now: number): [TimeGroup, Conversation[]][] {
  const groups = new Map<TimeGroup, Conversation[]>();
  const order: TimeGroup[] = ['Today', 'Yesterday', 'Previous 7 days', 'Previous 30 days', 'Older'];

  for (const c of convs) {
    const g = getTimeGroup(c.updatedAt, now);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(c);
  }

  return order.filter((g) => groups.has(g)).map((g) => [g, groups.get(g)!]);
}

function ConversationRailInner({
  open,
  conversations,
  activeConversationId,
  onClose,
  onNewConversation,
  onSelectConversation,
  onDeleteConversation,
  onOpenSettings,
}: ConversationRailProps) {
  const [searchQuery, setSearchQuery] = useState('');

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return conversations;
    const q = searchQuery.toLowerCase();
    return conversations.filter(
      (c) => c.title.toLowerCase().includes(q) || c.preview.toLowerCase().includes(q),
    );
  }, [conversations, searchQuery]);

  // Defer time-based grouping to client to prevent hydration mismatch.
  const now = useClientClock();
  const grouped = useMemo(
    () => (now ? groupConversations(filtered, now) : []),
    [filtered, now],
  );

  return (
    <>
      <div
        className={`rail-overlay ${open ? 'rail-overlay-open' : ''}`}
        onClick={onClose}
        aria-hidden={!open}
      />
      <aside className={`conversation-rail ${open ? 'conversation-rail-open' : ''}`}>
        <div className="rail-top">
          <h2 className="rail-title">Chats</h2>
          <button className="rail-close" onClick={onClose} aria-label="Close sidebar">
            <CloseIcon />
          </button>
        </div>

        <button className="rail-new" onClick={onNewConversation}>
          <PlusIcon /> New chat
        </button>

        <div className="rail-search-wrap">
          <input
            className="rail-search"
            type="text"
            placeholder="Search conversations..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            aria-label="Search conversations"
          />
        </div>

        {/* role="list" requires rendered listitem children (WCAG 1.3.1 /
            axe aria-required-children), so drop the role when only the
            empty-state placeholder is shown. */}
        <div
          className="rail-list"
          role={grouped.length > 0 ? 'list' : undefined}
          aria-label={grouped.length > 0 ? 'Conversation threads' : undefined}
        >
          {grouped.map(([group, items]) => (
            <div key={group} className="rail-group">
              <div className="rail-group-label">{group}</div>
              {items.map((conversation) => {
                const isActive = conversation.id === activeConversationId;
                const preview =
                  conversation.preview ||
                  conversation.turns.find((t) => t.role === 'user')?.content ||
                  'Start a new conversation';
                return (
                  <div
                    key={conversation.id}
                    className={`rail-item ${isActive ? 'rail-item-active' : ''}`}
                    role="listitem"
                  >
                    <button
                      className="rail-item-main"
                      onClick={() => onSelectConversation(conversation.id)}
                    >
                      <span className="rail-item-head">
                        <span className="rail-item-title">{conversation.title}</span>
                        <span className="rail-item-time">
                          <RelativeTime timestamp={conversation.updatedAt} />
                        </span>
                      </span>
                      <span className="rail-item-preview">{preview}</span>
                    </button>
                    <button
                      className="rail-item-delete"
                      onClick={() => onDeleteConversation(conversation.id)}
                      aria-label={`Delete ${conversation.title}`}
                      title="Delete conversation"
                    >
                      <TrashIcon />
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
          {conversations.length === 0 && (
            <div className="rail-empty">
              <MessageSquareIcon />
              <span>Your conversations will appear here.</span>
            </div>
          )}
          {searchQuery && filtered.length === 0 && conversations.length > 0 && (
            <div className="rail-empty">
              <span>No matching conversations</span>
            </div>
          )}
        </div>

        <AccountRail onOpenSettings={onOpenSettings} />
      </aside>
    </>
  );
}

export default memo(ConversationRailInner);
