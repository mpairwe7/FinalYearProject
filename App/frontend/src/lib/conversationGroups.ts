/**
 * Conversation list ordering, time grouping and relative-time formatting.
 *
 * Extracted from ConversationRail so the rail, the search overlay and the
 * /chats page all present history the same way. Pure functions only — the
 * hydration-safe clock that feeds `now` lives in hooks/useClientClock.
 */
import type { Conversation } from '../store/useChatStore';

export type TimeGroup =
  | 'Today'
  | 'Yesterday'
  | 'Previous 7 days'
  | 'Previous 30 days'
  | 'Older';

const GROUP_ORDER: TimeGroup[] = [
  'Today',
  'Yesterday',
  'Previous 7 days',
  'Previous 30 days',
  'Older',
];

/** Short relative label — "now", "12m", "3h", then a calendar date. */
export function formatTimestamp(timestamp: number, now: number = Date.now()): string {
  const diff = now - timestamp;
  if (diff < 60_000) return 'now';
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h`;
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' }).format(timestamp);
}

export function getTimeGroup(timestamp: number, now: number): TimeGroup {
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

export function groupConversations(
  convs: Conversation[],
  now: number,
): [TimeGroup, Conversation[]][] {
  const groups = new Map<TimeGroup, Conversation[]>();

  for (const c of convs) {
    const g = getTimeGroup(c.updatedAt, now);
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g)!.push(c);
  }

  return GROUP_ORDER.filter((g) => groups.has(g)).map((g) => [g, groups.get(g)!]);
}

/**
 * Display order: pinned first, then most recently updated.
 *
 * The store keeps conversations in insertion order and never reorders on pin,
 * so ordering is applied here, at read time — one rule, three views.
 */
export function sortConversations(convs: Conversation[]): Conversation[] {
  return [...convs].sort((a, b) => {
    if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1;
    return b.updatedAt - a.updatedAt;
  });
}

/** Case-insensitive match over title and preview. Empty query matches all. */
export function filterConversations(convs: Conversation[], query: string): Conversation[] {
  const q = query.trim().toLowerCase();
  if (!q) return convs;
  return convs.filter(
    (c) => c.title.toLowerCase().includes(q) || c.preview.toLowerCase().includes(q),
  );
}
