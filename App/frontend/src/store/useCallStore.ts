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
  /** `false` while the recognizer is still revising this utterance. */
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
  isUserSpeaking: boolean;
  activeAiText: string;
  error: string | null;

  openCall: () => void;
  closeCall: () => void;
  setStatus: (status: CallStatus) => void;
  setDuration: (duration: number | ((prev: number) => number)) => void;
  setMuted: (isMuted: boolean | ((prev: boolean) => boolean)) => void;
  setOfficerName: (name: string | null) => void;
  setTicketRef: (ref: string | null) => void;
  setUserSpeaking: (speaking: boolean) => void;
  setActiveAiText: (text: string) => void;
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
  isUserSpeaking: false,
  activeAiText: '',
  error: null,
};

export const useCallStore = create<CallStoreState>((set) => ({
  ...initialState,

  openCall: () =>
    set({
      isOpen: true,
      status: 'consent',
      error: null,
      duration: 0,
      captions: [],
      currentCaption: null,
      isUserSpeaking: false,
      activeAiText: '',
    }),
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
  setUserSpeaking: (isUserSpeaking) =>
    set((state) => ({
      isUserSpeaking,
      activeAiText: isUserSpeaking ? '' : state.activeAiText,
    })),
  setActiveAiText: (activeAiText) => set({ activeAiText }),
  addCaption: (caption) =>
    set((state) => {
      const captions = [...state.captions];
      const last = captions[captions.length - 1];
      // `final: false` is a hypothesis for the utterance still being spoken, so
      // it overwrites the previous hypothesis for that speaker instead of
      // stacking another bubble — and the final transcript then lands in the
      // same place. Everything already final stays in the transcript for the
      // rest of the call.
      if (last && last.final === false && last.speaker === caption.speaker) {
        captions[captions.length - 1] = caption;
      } else {
        captions.push(caption);
      }
      const isAssistant = caption.speaker === 'assistant' || caption.speaker === 'officer';
      return {
        captions,
        currentCaption: caption,
        activeAiText: isAssistant ? caption.text : state.activeAiText,
        isUserSpeaking: caption.speaker === 'caller',
      };
    }),
  setError: (error) => set({ error }),
  reset: () => set(initialState),
}));
