import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export interface Citation {
  ref: string;
  source: string;
  page?: string;
  section?: string;
  passage?: string;
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
}

export type SpeechState = 'idle' | 'listening' | 'unavailable' | 'error';

interface ChatStore {
  message: string;
  chat: ChatTurn[];
  speechState: SpeechState;
  locale: string;
  setMessage: (value: string) => void;
  setSpeechState: (state: SpeechState) => void;
  setLocale: (locale: string) => void;
  addTurns: (turns: ChatTurn[]) => void;
  updateLastTurn: (updater: (turn: ChatTurn) => ChatTurn) => void;
  reset: () => void;
}

function generateId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const initialGreeting: ChatTurn = {
  id: 'greeting-0',
  role: 'assistant',
  content: 'Hi! I can answer your questions about URA. Type or speak your question to begin.',
  timestamp: Date.now(),
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
  },
): ChatTurn {
  return {
    id: generateId(),
    role,
    content,
    timestamp: Date.now(),
    ...meta,
  };
}

const MAX_CHAT_TURNS = 200;

export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      message: '',
      chat: [initialGreeting],
      speechState: 'idle' as SpeechState,
      locale: 'en',
      setMessage: (value) => set({ message: value }),
      setSpeechState: (state) => set({ speechState: state }),
      setLocale: (locale) => set({ locale }),
      addTurns: (turns) =>
        set((s) => {
          const updated = [...s.chat, ...turns];
          return { chat: updated.length > MAX_CHAT_TURNS ? updated.slice(-MAX_CHAT_TURNS) : updated };
        }),
      updateLastTurn: (updater) =>
        set((s) => {
          if (s.chat.length === 0) return s;
          const updated = [...s.chat];
          updated[updated.length - 1] = updater(updated[updated.length - 1]);
          return { chat: updated };
        }),
      reset: () => set({ message: '', chat: [initialGreeting], speechState: 'idle', locale: 'en' }),
    }),
    {
      name: 'ura-chat-store',
      storage: createJSONStorage(() => {
        if (typeof window === 'undefined') return sessionStorage;
        return localStorage;
      }),
      partialize: (state) => ({
        chat: state.chat,
        locale: state.locale,
      }),
    },
  ),
);
