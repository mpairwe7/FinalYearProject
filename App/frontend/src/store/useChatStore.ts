import { create } from 'zustand';
import { normalizeLocale } from '../lib/locales';

export interface Citation {
  ref: string;
  source: string;
  page?: string;
  section?: string;
  passage?: string;
  url?: string;
  effective_date?: string;
  title?: string;
}

/** A document attached to a user turn (analysed via /v1/documents/analyze). */
export interface ChatAttachment {
  /** Backend document id — keys the PDF analysis report download. */
  id: string;
  name: string;
  docType?: string;
}

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  citations?: Citation[];
  faithfulnessScore?: number | null;
  retrievalMode?: string;
  escalationRequired?: boolean;
  escalationReason?: string;
  /** Whether this turn was answered from the offline RAG pipeline */
  offlineMode?: boolean;
  /** Documents attached to this (user) turn */
  attachments?: ChatAttachment[];
  /**
   * Wall-clock time the assistant spent on this turn, from the moment the
   * question was sent to the moment the reply finished. Persisted so the
   * "Thought for 13.9s" note survives a reload rather than vanishing with the
   * in-flight state that produced it.
   */
  thoughtForMs?: number;
}

export interface Conversation {
  id: string;
  title: string;
  preview: string;
  turns: ChatTurn[];
  createdAt: number;
  updatedAt: number;
  /** Pinned threads sort above everything else in the rail and the chats page. */
  pinned?: boolean;
  /**
   * The title was typed by the user, so `deriveTitle` must stop overwriting it.
   * Without this flag a rename survives only until the next turn is saved.
   */
  titleCustom?: boolean;
}

/** `processing` = recording stopped, transcription in flight. Only the
 *  server-ASR dictation path reaches it; the browser Speech API returns its
 *  transcript synchronously in onresult and goes straight back to `idle`. */
export type SpeechState = 'idle' | 'listening' | 'processing' | 'unavailable' | 'error';

interface ChatStore {
  message: string;
  chat: ChatTurn[];
  speechState: SpeechState;
  locale: string;
  // Session management
  conversations: Conversation[];
  activeConversationId: string | null;
  // Actions
  setMessage: (value: string) => void;
  setSpeechState: (state: SpeechState) => void;
  setLocale: (locale: string) => void;
  addTurns: (turns: ChatTurn[]) => void;
  updateLastTurn: (updater: (turn: ChatTurn) => ChatTurn) => void;
  reset: () => void;
  // Session actions
  ensureActiveConversationId: () => string;
  saveCurrentSession: () => void;
  createNewSession: () => void;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  togglePinSession: (id: string) => void;
  clearAllSessions: () => void;
  hydratePersisted: () => void;
}

function generateId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const GREETING: ChatTurn = {
  id: 'greeting-0',
  role: 'assistant',
  content: 'Hi! I can answer your questions about URA. Type or speak your question to begin.',
  timestamp: 0,
};

export function createTurn(
  role: 'user' | 'assistant',
  content: string,
  meta?: {
    citations?: Citation[];
    faithfulnessScore?: number | null;
    retrievalMode?: string;
    escalationRequired?: boolean;
    escalationReason?: string;
    attachments?: ChatAttachment[];
    thoughtForMs?: number;
  },
): ChatTurn {
  return { id: generateId(), role, content, timestamp: Date.now(), ...meta };
}

const MAX_TURNS = 200;
const MAX_SESSIONS = 50;
const CHAT_STORAGE_KEY = 'ura-chat-store';
const CHAT_STORAGE_VERSION = 2;

/** Derive a short title from the first user message. */
function deriveTitle(turns: ChatTurn[]): string {
  const first = turns.find((t) => t.role === 'user');
  if (!first) return 'New chat';
  return first.content.slice(0, 60) + (first.content.length > 60 ? '...' : '');
}

type PersistedChatState = Pick<ChatStore, 'locale' | 'conversations' | 'activeConversationId'>;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function getChatStorage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function sanitizeTurn(value: unknown): ChatTurn | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === 'string' && value.id ? value.id : generateId();
  const role = value.role === 'user' || value.role === 'assistant' ? value.role : null;
  const content = typeof value.content === 'string' ? value.content : null;
  if (!role || content == null) return null;

  const turn: ChatTurn = {
    id,
    role,
    content,
    timestamp: typeof value.timestamp === 'number' ? value.timestamp : 0,
  };

  if (Array.isArray(value.citations)) turn.citations = value.citations as Citation[];
  if (typeof value.faithfulnessScore === 'number' || value.faithfulnessScore === null) {
    turn.faithfulnessScore = value.faithfulnessScore;
  }
  if (typeof value.retrievalMode === 'string') turn.retrievalMode = value.retrievalMode;
  if (typeof value.escalationRequired === 'boolean') turn.escalationRequired = value.escalationRequired;
  if (typeof value.escalationReason === 'string') turn.escalationReason = value.escalationReason;
  if (typeof value.offlineMode === 'boolean') turn.offlineMode = value.offlineMode;
  if (typeof value.thoughtForMs === 'number' && Number.isFinite(value.thoughtForMs)) {
    turn.thoughtForMs = value.thoughtForMs;
  }
  if (Array.isArray(value.attachments)) {
    const attachments = value.attachments
      .filter(isRecord)
      .filter((a) => typeof a.id === 'string' && a.id && typeof a.name === 'string')
      .map((a) => {
        const attachment: ChatAttachment = { id: a.id as string, name: a.name as string };
        if (typeof a.docType === 'string') attachment.docType = a.docType;
        return attachment;
      });
    if (attachments.length) turn.attachments = attachments;
  }

  return turn;
}

function sanitizeTurns(value: unknown): ChatTurn[] {
  if (!Array.isArray(value)) return [];
  return value.map(sanitizeTurn).filter((turn): turn is ChatTurn => Boolean(turn)).slice(-MAX_TURNS);
}

function sanitizeConversation(value: unknown): Conversation | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === 'string' && value.id ? value.id : null;
  if (!id) return null;

  const turns = sanitizeTurns(value.turns);
  const lastTurn = turns[turns.length - 1];
  const conversation: Conversation = {
    id,
    title: typeof value.title === 'string' && value.title ? value.title : deriveTitle(turns),
    preview:
      typeof value.preview === 'string'
        ? value.preview.slice(0, 120)
        : lastTurn?.content.slice(0, 80) ?? '',
    turns,
    createdAt: typeof value.createdAt === 'number' ? value.createdAt : 0,
    updatedAt: typeof value.updatedAt === 'number' ? value.updatedAt : 0,
  };
  if (value.pinned === true) conversation.pinned = true;
  if (value.titleCustom === true) conversation.titleCustom = true;
  return conversation;
}

function readPersistedChatState(): PersistedChatState | null {
  const storage = getChatStorage();
  if (!storage) return null;

  try {
    const raw = storage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    const state: Record<string, unknown> | null =
      isRecord(parsed) && isRecord(parsed.state)
        ? parsed.state
        : isRecord(parsed)
          ? parsed
          : null;
    if (!state) return null;

    let conversations = Array.isArray(state.conversations)
      ? state.conversations
          .map(sanitizeConversation)
          .filter((conversation): conversation is Conversation => Boolean(conversation))
          .slice(0, MAX_SESSIONS)
      : [];

    let activeConversationId =
      typeof state.activeConversationId === 'string' ? state.activeConversationId : null;

    const legacyChat = sanitizeTurns(state.chat);
    if (legacyChat.length > 1 && !conversations.some((conversation) => conversation.id === activeConversationId)) {
      const id = activeConversationId ?? generateId();
      const now = Date.now();
      const lastTurn = legacyChat[legacyChat.length - 1];
      conversations = [
        {
          id,
          title: deriveTitle(legacyChat),
          preview: lastTurn?.content.slice(0, 80) ?? '',
          turns: legacyChat,
          createdAt: now,
          updatedAt: now,
        },
        ...conversations.filter((conversation) => conversation.id !== id),
      ].slice(0, MAX_SESSIONS);
      activeConversationId = id;
    }

    if (!conversations.some((conversation) => conversation.id === activeConversationId)) {
      activeConversationId = null;
    }

    return {
      locale: normalizeLocale(state.locale),
      conversations,
      activeConversationId,
    };
  } catch {
    return null;
  }
}

function writePersistedChatState(state: PersistedChatState): void {
  const storage = getChatStorage();
  if (!storage) return;

  try {
    storage.setItem(
      CHAT_STORAGE_KEY,
      JSON.stringify({
        state: {
          locale: normalizeLocale(state.locale),
          conversations: state.conversations.slice(0, MAX_SESSIONS),
          activeConversationId: state.activeConversationId,
        },
        version: CHAT_STORAGE_VERSION,
      }),
    );
  } catch {
    // Storage can fail in private mode or under quota pressure.
  }
}

/**
 * Strip LLM chain-of-thought reasoning and normalize formatting.
 *
 * Strategy: The LLM produces two sections:
 *   1. Internal reasoning (talks about "the user", "passage [1]", "I should", etc.)
 *   2. The actual answer (addresses the user directly)
 *
 * We detect the boundary by finding where reasoning ends and the
 * direct answer begins, using signals like:
 *   - Triple newline separator (\n\n\n)
 *   - Blocks that reference internal reasoning patterns
 */

const THINKING_SIGNALS = [
  // Meta-commentary about the task
  /^(okay|alright|hmm|well|right|sure|let me|now,)/i,
  /^the user (is |want|ask)/i,
  /^i (need|should|also|will|can|have) /i,
  // Reasoning about passages/sources
  /^(looking at|checking|passage |but it|but the|it (also|doesn|mention))/i,
  /passage \[?\d/i,
  /^so,? (the|i |none|based|from)/i,
  /^since (the|none|it|this|there)/i,
  /^(therefore|however|also|additionally),? (note that|i think|i should|the user|the passage|looking)/i,
  // Self-narration about what to do
  /^(the (main|key|relevant) point|the answer|my response|i.ll |let.s )/i,
  /^(this|that) (means|is|doesn|seem|suggest|passage)/i,
  /not (list|mention|provide|explain|specify|include)/i,
];

function looksLikeThinking(block: string): boolean {
  const trimmed = block.trimStart();
  if (!trimmed) return true;
  return THINKING_SIGNALS.some((rx) => rx.test(trimmed));
}

export function normalizeAssistantResponse(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/(^|\n)\s*(\d+)\)\s+/g, '$1$2. ')
    .replace(/(^|\n)\s*\u2022\s+/g, '$1- ')
    .replace(/(^|\n)\s*([*+])\s+/g, '$1- ')
    .replace(/([.!?])\s+(?=(Note|Important|Tip|Warning|Caution|Summary):\s)/g, '$1\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function cleanResponse(text: string): string {
  let cleaned = text.trim();
  if (!cleaned) return cleaned;

  // Split into paragraph blocks
  const blocks = cleaned.split('\n\n').filter((b) => b.trim());
  if (blocks.length < 2) return normalizeAssistantResponse(cleaned);

  // If the first block isn't thinking, return as-is
  if (!looksLikeThinking(blocks[0])) return normalizeAssistantResponse(cleaned);

  // Tag each block as thinking or answer
  const tags = blocks.map((b) => looksLikeThinking(b));

  // Find the LAST contiguous run of answer blocks — that's the real response.
  // The LLM pattern is: thinking... (maybe mixed answer)... FINAL ANSWER blocks
  let lastAnswerEnd = blocks.length - 1;
  while (lastAnswerEnd >= 0 && tags[lastAnswerEnd]) lastAnswerEnd--;

  if (lastAnswerEnd < 0) return normalizeAssistantResponse(cleaned); // all thinking, return as-is

  // Actually: find the start of the final answer cluster
  let lastAnswerStart = lastAnswerEnd;
  for (let i = lastAnswerEnd - 1; i >= 0; i--) {
    if (!tags[i]) lastAnswerStart = i;
    else break;
  }

  cleaned = blocks.slice(lastAnswerStart).filter((_, i) => !tags[lastAnswerStart + i]).join('\n\n').trim();

  // Normalize *Note: disclaimers into visually separated sections
  cleaned = cleaned.replace(/\n---\n\*Note/g, '\n\n---\n\n*Note');
  cleaned = cleaned.replace(/([.!?])\s*\*Note:/g, '$1\n\n---\n\n*Note:');

  return normalizeAssistantResponse(cleaned || text.trim());
}

export const useChatStore = create<ChatStore>()((set, get) => ({
  message: '',
  chat: [GREETING],
  speechState: 'idle' as SpeechState,
  locale: 'en',
  conversations: [] as Conversation[],
  activeConversationId: null as string | null,

  setMessage: (value) => set({ message: value }),
  setSpeechState: (state) => set({ speechState: state }),
  setLocale: (locale) => {
    set({ locale: normalizeLocale(locale) });
    writePersistedChatState(get());
  },

  addTurns: (turns) =>
    set((s) => {
      const updated = [...s.chat, ...turns];
      return { chat: updated.length > MAX_TURNS ? updated.slice(-MAX_TURNS) : updated };
    }),

  updateLastTurn: (updater) =>
    set((s) => {
      if (s.chat.length === 0) return s;
      const updated = [...s.chat];
      updated[updated.length - 1] = updater(updated[updated.length - 1]);
      return { chat: updated };
    }),

  reset: () => {
    set({
      message: '',
      chat: [GREETING],
      speechState: 'idle',
      locale: 'en',
      activeConversationId: null,
    });
    writePersistedChatState(get());
  },

  /** Ensure there is a stable id for the active conversation thread. */
  ensureActiveConversationId: () => {
    const { activeConversationId } = get();
    if (activeConversationId) return activeConversationId;
    const id = generateId();
    set({ activeConversationId: id });
    return id;
  },

  /** Save the current chat as a conversation (or update existing). */
  saveCurrentSession: () => {
    const { chat, conversations, activeConversationId } = get();
    // Only save if there are user messages
    if (chat.length <= 1) {
      writePersistedChatState(get());
      return;
    }
    const now = Date.now();
    const title = deriveTitle(chat);
    const lastMsg = chat[chat.length - 1];
    const preview = lastMsg.content.slice(0, 80);

    const id = activeConversationId ?? generateId();
    const conv: Conversation = { id, title, preview, turns: chat, createdAt: now, updatedAt: now };
    const existing = conversations.find((c) => c.id === id);

    if (existing) {
      // A title the user typed is theirs. `deriveTitle` re-runs on every save,
      // so without this guard a rename lasts exactly until the next turn.
      set({
        activeConversationId: id,
        conversations: conversations.map((c) =>
          c.id === id
            ? { ...c, title: c.titleCustom ? c.title : title, preview, turns: chat, updatedAt: now }
            : c,
        ),
      });
    } else {
      set({
        activeConversationId: id,
        conversations: [conv, ...conversations].slice(0, MAX_SESSIONS),
      });
    }
    writePersistedChatState(get());
  },

  /** Save current, then start fresh. */
  createNewSession: () => {
    const { saveCurrentSession } = get();
    saveCurrentSession();
    set({ chat: [GREETING], message: '', activeConversationId: null });
    writePersistedChatState(get());
  },

  /** Save current, then load target session. */
  switchSession: (id) => {
    const { saveCurrentSession } = get();
    saveCurrentSession();
    const target = get().conversations.find((c) => c.id === id);
    if (!target) return;
    set({ chat: target.turns, activeConversationId: id, message: '' });
    writePersistedChatState(get());
  },

  /** Delete a session. If it's active, start fresh. */
  deleteSession: (id) => {
    const { conversations, activeConversationId } = get();
    const filtered = conversations.filter((c) => c.id !== id);
    if (id === activeConversationId) {
      set({ conversations: filtered, chat: [GREETING], activeConversationId: null, message: '' });
    } else {
      set({ conversations: filtered });
    }
    writePersistedChatState(get());
  },

  /**
   * Rename a conversation. Marks the title as user-owned so `saveCurrentSession`
   * stops deriving it from the first message.
   *
   * An empty or whitespace-only title is a no-op rather than an erasure — the
   * inline editor commits on blur, so an accidental click-away must not leave a
   * nameless row.
   */
  renameSession: (id, title) => {
    const next = title.trim().slice(0, 80);
    if (!next) return;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title: next, titleCustom: true } : c,
      ),
    }));
    writePersistedChatState(get());
  },

  /** Pin / unpin. Ordering is applied at read time by `sortConversations`. */
  togglePinSession: (id) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, pinned: !c.pinned } : c,
      ),
    }));
    writePersistedChatState(get());
  },

  /**
   * Delete every stored conversation and return to the start screen.
   *
   * This is the settings panel's "clear chat history": local-only, because the
   * conversations live in this browser's localStorage. Server-side history is a
   * separate thing, removed by `DELETE /v1/me` (right to erasure).
   */
  clearAllSessions: () => {
    set({
      conversations: [],
      chat: [GREETING],
      activeConversationId: null,
      message: '',
    });
    writePersistedChatState(get());
  },

  hydratePersisted: () => {
    const persisted = readPersistedChatState();
    if (!persisted) return;

    const activeConversation = persisted.activeConversationId
      ? persisted.conversations.find((conversation) => conversation.id === persisted.activeConversationId)
      : null;

    set({
      message: '',
      chat: activeConversation?.turns.length ? activeConversation.turns : [GREETING],
      speechState: 'idle',
      locale: persisted.locale,
      conversations: persisted.conversations,
      activeConversationId: activeConversation?.id ?? null,
    });
    writePersistedChatState(get());
  },
}));
