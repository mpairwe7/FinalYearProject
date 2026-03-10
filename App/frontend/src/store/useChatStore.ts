import { create } from 'zustand';

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
  setMessage: (value: string) => void;
  setSpeechState: (state: SpeechState) => void;
  addTurns: (turns: ChatTurn[]) => void;
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

export const useChatStore = create<ChatStore>((set) => ({
  message: '',
  chat: [initialGreeting],
  speechState: 'idle',
  setMessage: (value) => set({ message: value }),
  setSpeechState: (state) => set({ speechState: state }),
  addTurns: (turns) =>
    set((s) => {
      const updated = [...s.chat, ...turns];
      // Cap history at 200 turns to prevent memory exhaustion (H5 audit fix)
      return { chat: updated.length > 200 ? updated.slice(-200) : updated };
    }),
  reset: () => set({ message: '', chat: [initialGreeting], speechState: 'idle' }),
}));
