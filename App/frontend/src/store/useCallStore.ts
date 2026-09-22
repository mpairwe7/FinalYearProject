import { create } from 'zustand';

export type CallStatus =
  | 'idle'
  | 'consent'
  | 'dialing'
  | 'ai'
  | 'transferring'
  | 'officer'
  | 'ended';

export interface CaptionEntry {
  speaker: 'caller' | 'assistant' | 'officer' | 'system';
  text: string;
  final?: boolean;
  turn_id?: number;
}

interface CallStoreState {
  isOpen: boolean;
  status: CallStatus;
  duration: number;
  isMuted: boolean;
  officerName: string | null;
  ticketRef: string | null;
  captions: CaptionEntry[];
  currentCaption: CaptionEntry | null;
  error: string | null;

  openCall: () => void;
  closeCall: () => void;
  setStatus: (status: CallStatus) => void;
  setDuration: (duration: number | ((prev: number) => number)) => void;
  setMuted: (isMuted: boolean | ((prev: boolean) => boolean)) => void;
  setOfficerName: (name: string | null) => void;
  setTicketRef: (ref: string | null) => void;
  addCaption: (caption: CaptionEntry) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const initialState = {
  isOpen: false,
  status: 'idle' as CallStatus,
  duration: 0,
  isMuted: false,
  officerName: null,
  ticketRef: null,
  captions: [],
  currentCaption: null,
  error: null,
};

export const useCallStore = create<CallStoreState>((set) => ({
  ...initialState,

  openCall: () => set({ isOpen: true, status: 'consent', error: null, duration: 0, captions: [], currentCaption: null }),
  closeCall: () => set({ isOpen: false, status: 'idle' }),
  setStatus: (status) => set({ status }),
  setDuration: (duration) =>
    set((state) => ({
      duration: typeof duration === 'function' ? duration(state.duration) : duration,
    })),
  setMuted: (isMuted) =>
    set((state) => ({
      isMuted: typeof isMuted === 'function' ? isMuted(state.isMuted) : isMuted,
    })),
  setOfficerName: (officerName) => set({ officerName }),
  setTicketRef: (ticketRef) => set({ ticketRef }),
  addCaption: (caption) =>
    set((state) => ({
      captions: [...state.captions, caption],
      currentCaption: caption,
    })),
  setError: (error) => set({ error }),
  reset: () => set(initialState),
}));
