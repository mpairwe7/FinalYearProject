import { create } from 'zustand';

export interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
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

const initialGreeting: ChatTurn = {
  role: 'assistant',
  content: 'Hi! I can answer your questions about URA. Type or speak your question to begin.',
  timestamp: Date.now(),
};

export const useChatStore = create<ChatStore>((set) => ({
  message: '',
  chat: [initialGreeting],
  speechState: 'idle',
  setMessage: (value) => set({ message: value }),
  setSpeechState: (state) => set({ speechState: state }),
  addTurns: (turns) => set((s) => ({ chat: [...s.chat, ...turns] })),
  reset: () => set({ message: '', chat: [initialGreeting], speechState: 'idle' }),
}));
