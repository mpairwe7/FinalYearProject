/**
 * Zustand store for voice-first mode state.
 *
 * Separated from useChatStore to keep concerns isolated:
 * - Voice session state (WebSocket, VAD, recording)
 * - Offline queue + sync status
 * - Voice settings (accent, silence timeout, auto-barge-in)
 *
 * Only settings + pendingSync are persisted to localStorage.
 * Transient state (wsState, voicePhase, partialTranscript) is not persisted.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type WsState = 'disconnected' | 'connecting' | 'connected' | 'error';
export type VoicePhase = 'idle' | 'listening' | 'processing' | 'speaking' | 'error' | 'offline';

export interface OfflineMessage {
  id: string;
  text: string;
  timestamp: number;
  language: string;
}

export interface LatencyReport {
  asr_ms: number;
  mt_ms: number;
  llm_ms: number;
  tts_first_chunk_ms: number;
  total_ms: number;
}

interface VoiceState {
  // Connection
  wsState: WsState;
  sessionId: string | null;

  // Voice session
  voicePhase: VoicePhase;
  partialTranscript: string;
  finalTranscript: string;
  streamingReply: string;
  lastLatency: LatencyReport | null;

  // Offline
  isOnline: boolean;
  pendingSync: OfflineMessage[];

  // Settings (persisted)
  autoBargeIn: boolean;
  silenceTimeout: number;
  voiceId: string;
  accentProfile: string;

  // Camera
  cameraActive: boolean;

  // Offline RAG state (Phase 25)
  offlineMode: boolean;
  offlineBundleVersion: string;
  lastSyncAt: string | null;
  syncInProgress: boolean;

  // Voice-first mode (Phase 27)
  voiceFirstEnabled: boolean;
  voiceVisionEnabled: boolean;
}

interface VoiceActions {
  // Connection
  setWsState: (state: WsState) => void;
  setSessionId: (id: string | null) => void;

  // Voice phases
  setVoicePhase: (phase: VoicePhase) => void;
  updatePartialTranscript: (text: string) => void;
  setFinalTranscript: (text: string) => void;
  appendReplyToken: (text: string) => void;
  setStreamingReply: (text: string) => void;
  setLastLatency: (report: LatencyReport | null) => void;
  resetTurn: () => void;

  // Offline
  setOnline: (online: boolean) => void;
  addPendingSync: (msg: OfflineMessage) => void;
  flushPendingSync: () => void;

  // Settings
  setAutoBargeIn: (enabled: boolean) => void;
  setSilenceTimeout: (ms: number) => void;
  setVoiceId: (id: string) => void;
  setAccentProfile: (profile: string) => void;

  // Camera
  setCameraActive: (active: boolean) => void;

  // Offline RAG (Phase 25)
  setOfflineMode: (enabled: boolean) => void;
  setOfflineBundleVersion: (version: string) => void;
  setLastSyncAt: (at: string | null) => void;
  setSyncInProgress: (inProgress: boolean) => void;

  // Voice-first mode (Phase 27)
  setVoiceFirstEnabled: (enabled: boolean) => void;
  setVoiceVisionEnabled: (enabled: boolean) => void;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useVoiceStore = create<VoiceState & VoiceActions>()(
  persist(
    (set) => ({
      // ── Connection ──
      wsState: 'disconnected',
      sessionId: null,
      setWsState: (wsState) => set({ wsState }),
      setSessionId: (sessionId) => set({ sessionId }),

      // ── Voice session ──
      voicePhase: 'idle',
      partialTranscript: '',
      finalTranscript: '',
      streamingReply: '',
      lastLatency: null,

      setVoicePhase: (voicePhase) => set({ voicePhase }),
      updatePartialTranscript: (text) => set({ partialTranscript: text }),
      setFinalTranscript: (text) => set({ finalTranscript: text, partialTranscript: '' }),
      appendReplyToken: (text) =>
        set((s) => ({ streamingReply: s.streamingReply + text })),
      setStreamingReply: (streamingReply) => set({ streamingReply }),
      setLastLatency: (lastLatency) => set({ lastLatency }),
      resetTurn: () =>
        set({
          partialTranscript: '',
          finalTranscript: '',
          streamingReply: '',
          lastLatency: null,
        }),

      // ── Offline ──
      isOnline: true,
      pendingSync: [],
      setOnline: (isOnline) => set({ isOnline }),
      addPendingSync: (msg) =>
        set((s) => ({ pendingSync: [...s.pendingSync, msg].slice(-50) })),
      flushPendingSync: () => set({ pendingSync: [] }),

      // ── Settings ──
      autoBargeIn: false,
      silenceTimeout: 2000,
      voiceId: '',
      accentProfile: '',
      setAutoBargeIn: (autoBargeIn) => set({ autoBargeIn }),
      setSilenceTimeout: (silenceTimeout) => set({ silenceTimeout }),
      setVoiceId: (voiceId) => set({ voiceId }),
      setAccentProfile: (accentProfile) => set({ accentProfile }),

      // ── Camera ──
      cameraActive: false,
      setCameraActive: (cameraActive) => set({ cameraActive }),

      // ── Offline RAG (Phase 25) ──
      offlineMode: false,
      offlineBundleVersion: '',
      lastSyncAt: null,
      syncInProgress: false,
      setOfflineMode: (offlineMode) => set({ offlineMode }),
      setOfflineBundleVersion: (offlineBundleVersion) => set({ offlineBundleVersion }),
      setLastSyncAt: (lastSyncAt) => set({ lastSyncAt }),
      setSyncInProgress: (syncInProgress) => set({ syncInProgress }),

      // ── Voice-first mode (Phase 27) ──
      voiceFirstEnabled: false,
      voiceVisionEnabled: false,
      setVoiceFirstEnabled: (voiceFirstEnabled) => set({ voiceFirstEnabled }),
      setVoiceVisionEnabled: (voiceVisionEnabled) => set({ voiceVisionEnabled }),
    }),
    {
      name: 'ura-voice-store',
      version: 1,
      skipHydration: true,
      // Only persist settings + offline queue
      partialize: (state) => ({
        pendingSync: state.pendingSync,
        autoBargeIn: state.autoBargeIn,
        silenceTimeout: state.silenceTimeout,
        voiceId: state.voiceId,
        accentProfile: state.accentProfile,
        offlineMode: state.offlineMode,
        offlineBundleVersion: state.offlineBundleVersion,
        lastSyncAt: state.lastSyncAt,
        voiceFirstEnabled: state.voiceFirstEnabled,
        voiceVisionEnabled: state.voiceVisionEnabled,
      }),
    },
  ),
);
