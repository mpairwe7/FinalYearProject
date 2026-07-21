"use client";

import React, { memo, useCallback, useEffect, useRef } from "react";
import {
  MicIcon,
  CloseIcon,
  BargeInIcon,
  WifiOffIcon,
  SyncIcon,
  StopIcon,
} from "./Icons";
import { useVoiceStore, type VoicePhase } from "../store/useVoiceStore";
import { useVoiceWebSocket } from "../hooks/useVoiceWebSocket";
import { AudioRecorder } from "../services/voiceService";

/**
 * Full-screen voice-first interface for mobile users.
 *
 * Renders as an overlay when activated — designed for low-literacy
 * rural users on 2G/3G networks.  Features:
 *
 * - Animated voice orb showing state (idle/listening/processing/speaking)
 * - Live waveform visualizer during recording
 * - Real-time partial transcript
 * - Barge-in button to interrupt assistant mid-response
 * - Offline indicator with sync status
 */

interface VoiceChatProps {
  open: boolean;
  locale: string;
  conversationId?: string;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Orb state colours
// ---------------------------------------------------------------------------

const PHASE_STYLES: Record<VoicePhase, { color: string; label: string }> = {
  idle: { color: "var(--surface-3, #2a2a3d)", label: "Tap to speak" },
  listening: { color: "var(--ura-teal, #00a88f)", label: "Listening..." },
  processing: { color: "var(--ura-gold, #fbb034)", label: "Thinking..." },
  speaking: { color: "var(--grad-primary, #6366f1)", label: "Speaking..." },
  error: { color: "#ef4444", label: "Error — tap to retry" },
  offline: { color: "#6b7280", label: "Offline mode" },
};

// ---------------------------------------------------------------------------
// Waveform bars (CSS-animated, GPU-friendly for low-end Android)
// ---------------------------------------------------------------------------

function WaveformBars({ active }: { active: boolean }) {
  return (
    <div
      className="voice-waveform-bars"
      aria-hidden="true"
      style={{ opacity: active ? 1 : 0.2, transition: "opacity 0.3s" }}
    >
      {Array.from({ length: 12 }).map((_, i) => (
        <div
          key={i}
          className="voice-waveform-bar"
          style={{
            animationDelay: `${i * 0.05}s`,
            animationPlayState: active ? "running" : "paused",
          }}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Offline indicator
// ---------------------------------------------------------------------------

function OfflineIndicator() {
  const isOnline = useVoiceStore((s) => s.isOnline);
  const pendingCount = useVoiceStore((s) => s.pendingSync.length);

  if (isOnline && pendingCount === 0) return null;

  return (
    <div className="voice-offline-badge" role="status">
      {!isOnline ? (
        <>
          <WifiOffIcon /> <span>Offline</span>
        </>
      ) : (
        <>
          <SyncIcon /> <span>Syncing ({pendingCount})</span>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// VoiceChat component
// ---------------------------------------------------------------------------

function VoiceChatInner({ open, locale, conversationId, onClose }: VoiceChatProps) {
  const voicePhase = useVoiceStore((s) => s.voicePhase);
  const partialTranscript = useVoiceStore((s) => s.partialTranscript);
  const finalTranscript = useVoiceStore((s) => s.finalTranscript);
  const streamingReply = useVoiceStore((s) => s.streamingReply);
  const lastLatency = useVoiceStore((s) => s.lastLatency);
  const setVoicePhase = useVoiceStore((s) => s.setVoicePhase);
  const resetTurn = useVoiceStore((s) => s.resetTurn);

  const { connect, disconnect, sendAudioChunk, bargeIn } = useVoiceWebSocket();
  const recorderRef = useRef<AudioRecorder | null>(null);

  const stopStreaming = useCallback(() => {
    if (recorderRef.current) {
      recorderRef.current.cancel();
      recorderRef.current = null;
    }
  }, []);

  // ── Connect WebSocket when panel opens ─────────────────────────
  useEffect(() => {
    if (open) {
      const voiceId = useVoiceStore.getState().voiceId;
      connect({
        language: locale,
        conversation_id: conversationId,
        sample_rate: 16000,
        vad_sensitivity: "medium",
        tts_enabled: true,
        voice: voiceId || undefined,
        voice_consent_accepted: true,
      });
    }
    return () => {
      disconnect();
      stopStreaming();
    };
  }, [open, locale, conversationId, connect, disconnect, stopStreaming]);

  // ── Orb tap handler ────────────────────────────────────────────
  const handleOrbTap = useCallback(async () => {
    if (voicePhase === "idle" || voicePhase === "error") {
      resetTurn();
      // For now, use the batch recording flow:
      // Start recording, then on second tap stop and let server-side VAD handle it
      if (!recorderRef.current) {
        try {
          const recorder = new AudioRecorder();
          recorderRef.current = recorder;
          await recorder.start();
          setVoicePhase("listening");
          // Haptic feedback
          if (navigator.vibrate) navigator.vibrate(50);
        } catch {
          setVoicePhase("error");
        }
      }
    } else if (voicePhase === "listening") {
      // Stop recording and send audio
      if (recorderRef.current) {
        try {
          const pcm = await recorderRef.current.stop();
          recorderRef.current = null;
          setVoicePhase("processing");
          if (navigator.vibrate) navigator.vibrate(50);
          // Send the complete utterance via WebSocket
          sendAudioChunk(pcm);
        } catch {
          setVoicePhase("error");
        }
      }
    } else if (voicePhase === "speaking") {
      // Barge-in
      bargeIn();
      if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
    }
  }, [voicePhase, setVoicePhase, resetTurn, sendAudioChunk, bargeIn]);

  // ── Render ──────────────────────────────────────────────────────
  if (!open) return null;

  const phaseStyle = PHASE_STYLES[voicePhase];
  const transcript = finalTranscript || partialTranscript;

  return (
    <div
      className="voice-chat-container"
      role="dialog"
      aria-label="Voice chat"
      aria-modal="true"
      onKeyDown={(e) => {
        // Keyboard operability: Space toggles the mic from anywhere in the
        // dialog (unless a control already owns the key), Escape closes.
        if (e.key === "Escape") {
          onClose();
          return;
        }
        const target = e.target as HTMLElement;
        if (e.code === "Space" && target.tagName !== "BUTTON" && target.tagName !== "INPUT") {
          e.preventDefault();
          void handleOrbTap();
        }
      }}
    >
      {/* Header */}
      <div className="voice-chat-header">
        <OfflineIndicator />
        <button
          className="voice-chat-close"
          onClick={onClose}
          aria-label="Close voice chat"
        >
          <CloseIcon />
        </button>
      </div>

      {/* Central orb */}
      <div className="voice-chat-body">
        <button
          className={`voice-orb voice-orb-${voicePhase}`}
          onClick={handleOrbTap}
          aria-label={phaseStyle.label}
          style={{ "--orb-color": phaseStyle.color } as React.CSSProperties}
        >
          <div className="voice-orb-ring voice-orb-ring-1" />
          <div className="voice-orb-ring voice-orb-ring-2" />
          <div className="voice-orb-inner">
            {voicePhase === "speaking" ? <StopIcon /> : <MicIcon />}
          </div>
        </button>

        <p className="voice-phase-label" aria-live="polite">
          {phaseStyle.label}
        </p>

        {/* Waveform */}
        <WaveformBars active={voicePhase === "listening"} />

        {/* Transcript strip */}
        {transcript && (
          <div className="voice-transcript" aria-live="polite">
            <p className="voice-transcript-text">{transcript}</p>
          </div>
        )}

        {/* Streaming reply */}
        {streamingReply && (
          <div className="voice-reply-bubble" aria-live="polite">
            <p className="voice-reply-text">{streamingReply}</p>
          </div>
        )}

        {/* Latency indicator */}
        {lastLatency && (
          <p
            className="voice-latency"
            aria-label={`Reply completed in ${Math.round(lastLatency.total_ms)} milliseconds`}
          >
            {lastLatency.total_ms}ms
          </p>
        )}
      </div>

      {/* Bottom bar */}
      <div className="voice-chat-footer">
        {voicePhase === "speaking" && (
          <button
            className="voice-barge-in-btn"
            onClick={() => {
              bargeIn();
              if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
            }}
            aria-label="Stop and speak"
          >
            <BargeInIcon />
            <span>Stop and speak</span>
          </button>
        )}
      </div>
    </div>
  );
}

export const VoiceChat = memo(VoiceChatInner);
