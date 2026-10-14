/**
 * The chat session (G122): one id per browser, not per tab, so a conversation
 * reopened in a new tab keeps its server-side history; replaced once the server
 * has forgotten everything bound to it, or when the taxpayer clears history.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CONVERSATION_TTL_DAYS, conversationTtlMs, getChatSessionId, resetChatSession } from '@/lib/chatSession';
import { createTurn, useChatStore } from '@/store/useChatStore';

const DAY = 86_400_000;
const KEY = 'ura_chat_session';

afterEach(() => {
  resetChatSession();
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe('getChatSessionId', () => {
  it('is the same across calls, and survives what a new tab loses', () => {
    const first = getChatSessionId(1_000);
    sessionStorage.clear(); // a new tab starts with empty sessionStorage
    expect(getChatSessionId(2_000)).toBe(first);
    expect(first).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
  });

  it('follows the server retention, seven days by default', () => {
    expect(CONVERSATION_TTL_DAYS).toBe(7);
    expect(conversationTtlMs()).toBe(7 * DAY);
  });

  it('moves its expiry forward while it is used', () => {
    const start = Date.UTC(2026, 9, 1);
    const first = getChatSessionId(start);
    expect(getChatSessionId(start + 6 * DAY)).toBe(first);
    expect(getChatSessionId(start + 12 * DAY)).toBe(first);
  });

  it('is replaced after the retention period without use', () => {
    const start = Date.UTC(2026, 9, 1);
    const first = getChatSessionId(start);
    expect(getChatSessionId(start + 7 * DAY + 1)).not.toBe(first);
  });

  it('replaces a malformed stored value', () => {
    localStorage.setItem(KEY, JSON.stringify({ id: '../../etc', lastUsedAt: Date.now() }));
    expect(getChatSessionId()).not.toBe('../../etc');
    localStorage.setItem(KEY, 'not json');
    expect(getChatSessionId()).toMatch(/^[A-Za-z0-9_-]{1,64}$/);
  });

  it('still holds for the page when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const first = getChatSessionId();
    expect(getChatSessionId()).toBe(first);
  });

  it('starts over after a reset', () => {
    const first = getChatSessionId();
    resetChatSession();
    expect(getChatSessionId()).not.toBe(first);
  });
});

describe('the browser keeps conversations as long as the server does', () => {
  const stored = (conversations: unknown[]) =>
    localStorage.setItem(
      'ura-chat-store',
      JSON.stringify({ state: { locale: 'en', conversations, activeConversationId: null }, version: 2 }),
    );
  const conversation = (id: string, updatedAt: number, extra: Record<string, unknown> = {}) => ({
    id,
    title: id,
    preview: '',
    turns: [createTurn('user', 'What is VAT?'), createTurn('assistant', 'VAT is a tax on supplies.')],
    createdAt: updatedAt,
    updatedAt,
    ...extra,
  });

  afterEach(() => {
    localStorage.removeItem('ura-chat-store');
    useChatStore.getState().reset();
  });

  it('drops a conversation the server has already forgotten, but not a pinned one', () => {
    const now = Date.now();
    stored([
      conversation('recent', now - 2 * DAY),
      conversation('expired', now - 8 * DAY),
      conversation('pinned-old', now - 30 * DAY, { pinned: true }),
    ]);
    useChatStore.getState().hydratePersisted();
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['recent', 'pinned-old']);
  });

  it('starts the clock now for a conversation stored without a time', () => {
    stored([conversation('undated', 0)]);
    useChatStore.getState().hydratePersisted();
    const [kept] = useChatStore.getState().conversations;
    expect(kept.id).toBe('undated');
    expect(kept.updatedAt).toBeGreaterThan(0);
  });

  it('a tab left open past the retention period drops what the server forgot when switching', () => {
    const now = Date.now();
    useChatStore.setState({
      conversations: [conversation('recent', now - DAY), conversation('expired', now - 8 * DAY)],
      activeConversationId: null,
    });
    useChatStore.getState().switchSession('expired');
    expect(useChatStore.getState().activeConversationId).toBeNull();
    expect(useChatStore.getState().conversations.map((c) => c.id)).toEqual(['recent']);
    useChatStore.getState().switchSession('recent');
    expect(useChatStore.getState().activeConversationId).toBe('recent');
  });

  it('clearing chat history also starts a new chat session', () => {
    const before = getChatSessionId();
    useChatStore.getState().clearAllSessions();
    expect(getChatSessionId()).not.toBe(before);
  });
});
