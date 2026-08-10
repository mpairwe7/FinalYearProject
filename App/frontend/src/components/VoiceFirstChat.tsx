"use client";

/**
 * VoiceFirstChat — Primary mobile voice-first interface (Phase 27).
 *
 * Full-screen voice chat designed as the **default mobile experience** for
 * low-literacy rural users.  Voice is the primary input/output; text is
 * secondary.
 *
 * Features:
 *  - Animated voice orb with state-driven colors
 *  - Real-time waveform visualizer
 *  - Barge-in (interrupt assistant while speaking)
 *  - Offline mode indicator with pending sync count
 *  - Voice + Vision mode toggle (camera + speech)
 *  - Sentence-chunked streaming TTS
 *  - Haptic feedback on interactions
 *
 * Styling note: renders outside the chatv2 scope (see app/page.tsx), so it
 * shares the app's real :root tokens and the .voice-orb-* language already
 * established by the Phase-23 VoiceChat modal, not chatv2's scoped tokens.
 */

import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useVoiceStore } from "@/store/useVoiceStore";
import { useChatStore } from "@/store/useChatStore";
import { authHeaders } from "@/lib/authSession";
import { LOCALE_OPTIONS, localeLabel } from "@/lib/locales";

/* ---------- Types ---------- */

type VoicePhase =
  | "idle"
  | "listening"
  | "processing"
  | "speaking"
  | "error"
  | "offline";

interface VoiceFirstChatProps {
  /** Close the voice-first overlay */
  onClose: () => void;
  /** Open voice+vision mode */
  onOpenVision?: () => void;
  /** Language code */
  locale?: string;
}

interface BrowserSpeechRecognitionAlternative {
  transcript?: string;
}

interface BrowserSpeechRecognitionResult {
  readonly isFinal?: boolean;
  readonly length?: number;
  readonly [index: number]: BrowserSpeechRecognitionAlternative | undefined;
}

interface BrowserSpeechRecognitionResultList {
  readonly length?: number;
  readonly [index: number]: BrowserSpeechRecognitionResult | undefined;
}

interface BrowserSpeechRecognitionEvent extends Event {
  readonly results?: BrowserSpeechRecognitionResultList;
}

interface BrowserSpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  start: () => void;
}

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: BrowserSpeechRecognitionConstructor;
  webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
};

/* ---------- Phase → visual config ---------- */

const PHASE_CONFIG: Record<VoicePhase, { color: string; label: string; pulse: boolean }> = {
  idle: { color: "#3b82f6", label: "Tap to speak", pulse: false },
  listening: { color: "#22c55e", label: "Listening...", pulse: true },
  processing: { color: "#f59e0b", label: "Thinking...", pulse: true },
  speaking: { color: "#8b5cf6", label: "Speaking...", pulse: true },
  error: { color: "#ef4444", label: "Error — tap to retry", pulse: false },
  offline: { color: "#6b7280", label: "Offline mode", pulse: false },
};

/* ---------- Helpers ---------- */

function haptic(pattern: number | number[] = 30) {
  if (typeof navigator !== "undefined" && navigator.vibrate) {
    navigator.vibrate(pattern);
  }
}

function getSpeechRecognitionConstructor(): BrowserSpeechRecognitionConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  const speechWindow = window as SpeechRecognitionWindow;
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
}

/* ---------- Sub-components ---------- */

/** Animated voice orb with state-driven ring effects — shares .voice-orb-*
 *  with the VoiceChat modal, sized up via .vfc-orb-lg for this hero context. */
const VoiceOrb = memo(function VoiceOrb({
  phase,
  onTap,
  waveformData,
}: {
  phase: VoicePhase;
  onTap: () => void;
  waveformData: number[];
}) {
  const config = PHASE_CONFIG[phase];

  return (
    <button
      onClick={onTap}
      className={`voice-orb vfc-orb-lg voice-orb-${phase}`}
      style={{ "--orb-color": config.color } as React.CSSProperties}
      aria-label={config.label}
    >
      <span className="voice-orb-ring voice-orb-ring-1" aria-hidden="true" />
      <span className="voice-orb-ring voice-orb-ring-2" aria-hidden="true" />
      <span className="voice-orb-inner">
        {phase === "listening" && waveformData.length > 0 ? (
          <span className="vfc-orb-wave" aria-hidden="true">
            {waveformData.slice(0, 12).map((v, i) => (
              <span key={i} style={{ height: `${Math.max(4, v * 48)}px` }} />
            ))}
          </span>
        ) : phase === "processing" ? (
          <span className="vfc-spinner" aria-hidden="true" />
        ) : (
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" />
          </svg>
        )}
      </span>
    </button>
  );
});

/** Offline mode banner */
const OfflineBanner = memo(function OfflineBanner({
  pendingCount,
}: {
  pendingCount: number;
}) {
  return (
    <div className="vfc-offline">
      <span className="vfc-offline-dot" aria-hidden="true" />
      <span>Offline mode</span>
      {pendingCount > 0 && <span className="vfc-offline-count">{pendingCount} pending</span>}
      <span className="vfc-offline-hint">Sync when online</span>
    </div>
  );
});

/** Latency indicator */
const LatencyBadge = memo(function LatencyBadge({
  latency,
}: {
  latency: { total_ms?: number } | null;
}) {
  if (!latency?.total_ms) return null;
  const ms = latency.total_ms;
  const tier = ms < 1200 ? "good" : ms < 2000 ? "ok" : "slow";
  return <span className={`vfc-latency vfc-latency-${tier}`}>{ms}ms</span>;
});

/* ---------- Main component ---------- */

function VoiceFirstChatInner({ onClose, onOpenVision, locale = "en" }: VoiceFirstChatProps) {
  const isOnline = useVoiceStore((state) => state.isOnline);
  const wsState = useVoiceStore((state) => state.wsState);
  const voicePhase = useVoiceStore((state) => state.voicePhase);
  const pendingSync = useVoiceStore((state) => state.pendingSync);
  const lastLatency = useVoiceStore((state) => state.lastLatency);
  const finalTranscript = useVoiceStore((state) => state.finalTranscript);
  const partialTranscript = useVoiceStore((state) => state.partialTranscript);
  const streamingReply = useVoiceStore((state) => state.streamingReply);
  const setVoicePhase = useVoiceStore((state) => state.setVoicePhase);
  const setFinalTranscript = useVoiceStore((state) => state.setFinalTranscript);
  const setStreamingReply = useVoiceStore((state) => state.setStreamingReply);
  const setLastLatency = useVoiceStore((state) => state.setLastLatency);
  const addPendingSync = useVoiceStore((state) => state.addPendingSync);
  const activeConversationId = useChatStore((state) => state.activeConversationId);
  const addTurns = useChatStore((state) => state.addTurns);
  const setLocale = useChatStore((state) => state.setLocale);

  const [waveformData, setWaveformData] = useState<number[]>([]);
  const [isRecording, setIsRecording] = useState(false);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number>(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const handleAudioRef = useRef<((audio: Uint8Array) => Promise<void>) | undefined>(undefined);

  const phase: VoicePhase = useMemo(() => {
    if (!isOnline && wsState !== "connected") return "offline";
    return voicePhase as VoicePhase;
  }, [isOnline, wsState, voicePhase]);

  const config = PHASE_CONFIG[phase] || PHASE_CONFIG.idle;
  const pendingCount = pendingSync?.length ?? 0;

  /* Waveform animation loop */
  const updateWaveform = useCallback(() => {
    if (!analyserRef.current) return;
    const data = new Uint8Array(analyserRef.current.frequencyBinCount);
    analyserRef.current.getByteFrequencyData(data);
    const bars = Array.from({ length: 12 }, (_, i) => {
      const idx = Math.floor((i / 12) * data.length);
      return data[idx] / 255;
    });
    setWaveformData(bars);
    animFrameRef.current = requestAnimationFrame(updateWaveform);
  }, []);

  /* Start recording */
  const startRecording = useCallback(async () => {
    haptic();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      streamRef.current = stream;

      // Set up waveform analyser
      const ctx = new AudioContext({ sampleRate: 16000 });
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      // Start waveform animation
      animFrameRef.current = requestAnimationFrame(updateWaveform);

      // MediaRecorder for batch mode
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.onstop = async () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const buffer = await blob.arrayBuffer();
        await handleAudioRef.current?.(new Uint8Array(buffer));
      };
      recorder.start();
      mediaRecorderRef.current = recorder;

      setIsRecording(true);
      setVoicePhase("listening");
    } catch (err) {
      console.error("Microphone access denied:", err);
      setVoicePhase("error");
    }
  }, [setVoicePhase, updateWaveform]);

  /* Stop recording */
  const stopRecording = useCallback(() => {
    haptic();
    cancelAnimationFrame(animFrameRef.current);
    setWaveformData([]);

    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    analyserRef.current = null;
    setIsRecording(false);
    setVoicePhase("processing");
  }, [setVoicePhase]);

  /* Handle completed audio */
  const handleAudioComplete = useCallback(
    async (audio: Uint8Array) => {
      try {
        const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
        const params = new URLSearchParams({
          language: locale,
          tts_enabled: "true",
        });
        if (activeConversationId) {
          params.set("conversation_id", activeConversationId);
        }

        const audioBody = audio.buffer.slice(
          audio.byteOffset,
          audio.byteOffset + audio.byteLength,
        ) as ArrayBuffer;

        const res = await fetch(`${API_BASE}/v1/voice/chat?${params}`, {
          method: "POST",
          headers: authHeaders({
            "Content-Type": "application/octet-stream",
            "X-Session-ID": activeConversationId ?? "",
            "X-Voice-Consent": "true",
          }),
          body: audioBody,
        });

        if (!res.ok) throw new Error(`Voice chat failed: ${res.status}`);

        const data = await res.json();

        // Add turns to chat
        if (data.transcript) {
          addTurns([
            {
              id: crypto.randomUUID(),
              role: "user",
              content: data.transcript,
              timestamp: Date.now(),
            },
          ]);
        }
        if (data.reply) {
          addTurns([
            {
              id: crypto.randomUUID(),
              role: "assistant",
              content: data.reply,
              timestamp: Date.now(),
              citations: data.citations,
              faithfulnessScore: data.faithfulness_score,
              retrievalMode: data.retrieval_mode,
            },
          ]);
        }

        setFinalTranscript(data.transcript || "");
        setStreamingReply(data.reply || "");

        // Update latency
        if (data.total_latency_s) {
          setLastLatency({
            total_ms: Math.round(data.total_latency_s * 1000),
            asr_ms: Math.round((data.asr_latency_s || 0) * 1000),
            mt_ms: Math.round((data.mt_latency_s || 0) * 1000),
            llm_ms: Math.round((data.llm_latency_s || 0) * 1000),
            tts_first_chunk_ms: Math.round((data.tts_latency_s || 0) * 1000),
          });
        }

        // Play audio response
        if (data.reply_audio_base64) {
          setVoicePhase("speaking");
          const { playAudioBase64 } = await import("@/services/voiceService");
          await playAudioBase64(data.reply_audio_base64);
        }

        setVoicePhase("idle");
      } catch (err) {
        console.error("Voice chat error:", err);

        // Offline fallback: use browser SpeechRecognition if available
        if (!navigator.onLine) {
          setVoicePhase("offline");
          try {
            const SpeechRecognition = getSpeechRecognitionConstructor();
            if (SpeechRecognition) {
              const recognition = new SpeechRecognition();
              recognition.lang =
                LOCALE_OPTIONS.find((o) => o.value === locale)?.speechLang ?? "en-US";
              recognition.continuous = false;
              recognition.interimResults = false;
              recognition.onresult = (e) => {
                const text = e.results?.[0]?.[0]?.transcript || "";
                if (text) {
                  addTurns([
                    { id: crypto.randomUUID(), role: "user", content: text, timestamp: Date.now(), offlineMode: true },
                  ]);
                  setFinalTranscript(text);

                  // Use browser TTS for the offline hint. Browsers ship no
                  // Luganda voices, so the hint deliberately stays English —
                  // and is labelled as English so the synthesizer picks a
                  // voice that matches the text instead of mangling it.
                  if ("speechSynthesis" in window) {
                    const utterance = new SpeechSynthesisUtterance(
                      "I'm currently offline. Your question has been saved and will be answered when you're back online."
                    );
                    utterance.lang = "en-US";
                    speechSynthesis.speak(utterance);
                  }
                }
                setVoicePhase("idle");
              };
              recognition.onerror = () => setVoicePhase("error");
              recognition.start();
            }
          } catch {
            // No browser speech API — just show error
          }

          addPendingSync({
            id: crypto.randomUUID(),
            text: "[voice message — queued for sync]",
            timestamp: Date.now(),
            language: locale,
          });
        } else {
          setVoicePhase("error");
        }
      }
    },
    [
      activeConversationId,
      addPendingSync,
      addTurns,
      locale,
      setFinalTranscript,
      setLastLatency,
      setStreamingReply,
      setVoicePhase,
    ],
  );

  // Keep ref in sync so startRecording's closure always calls the latest version
  useEffect(() => {
    handleAudioRef.current = handleAudioComplete;
  }, [handleAudioComplete]);

  /* Orb tap handler */
  const handleOrbTap = useCallback(() => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [isRecording, startRecording, stopRecording]);

  /* Barge-in handler */
  const handleBargeIn = useCallback(() => {
    haptic([30, 50, 30]);
    import("@/services/voiceService").then(({ stopPlayback }) => stopPlayback());
    setVoicePhase("idle");
    setStreamingReply("");
  }, [setStreamingReply, setVoicePhase]);

  /* Cleanup on unmount */
  useEffect(() => {
    return () => {
      cancelAnimationFrame(animFrameRef.current);
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  return (
    <div className="vfc-root" role="dialog" aria-modal="true" aria-label="Voice-first chat">
      {/* Top bar */}
      <div className="vfc-topbar">
        <button onClick={onClose} className="vfc-icon-btn" aria-label="Close voice chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="vfc-topbar-actions">
          <LatencyBadge latency={lastLatency} />
          {!isOnline && <OfflineBanner pendingCount={pendingCount} />}
        </div>

        {onOpenVision ? (
          <button onClick={() => { haptic(); onOpenVision(); }} className="vfc-icon-btn" aria-label="Open camera mode">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
              <circle cx="12" cy="13" r="3" />
            </svg>
          </button>
        ) : (
          <span className="vfc-spacer-icon" aria-hidden="true" />
        )}
      </div>

      {/* Transcript display */}
      <div className="vfc-stage">
        {finalTranscript && (
          <div className="vfc-card">
            <p className="vfc-card-label">You said:</p>
            <p className="vfc-card-text">{finalTranscript}</p>
          </div>
        )}

        {partialTranscript && phase === "listening" && (
          <div className="vfc-card vfc-card-partial">
            <p className="vfc-card-text">{partialTranscript}</p>
          </div>
        )}

        <VoiceOrb phase={phase} onTap={handleOrbTap} waveformData={waveformData} />

        <p className="vfc-phase-label">{config.label}</p>

        {streamingReply && (
          <div className="vfc-card vfc-card-reply">
            <p className="vfc-card-label">Assistant:</p>
            <p className="vfc-card-text">{streamingReply}</p>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="vfc-bottombar">
        {phase === "speaking" && (
          <button onClick={handleBargeIn} className="voice-barge-in-btn" aria-label="Stop and speak">
            Stop and speak
          </button>
        )}

        {/* Language cycle — steps through every locale the assistant supports;
            a full picker overlay would need a dedicated anchor point in this
            full-screen surface, unlike the composer toolbar. */}
        <button
          onClick={() => {
            haptic();
            const idx = LOCALE_OPTIONS.findIndex((o) => o.value === locale);
            const next = LOCALE_OPTIONS[(idx + 1) % LOCALE_OPTIONS.length];
            setLocale(next.value);
          }}
          className="vfc-lang-btn"
          aria-label={`Response language: ${localeLabel(locale)}. Tap to switch.`}
        >
          {localeLabel(locale)}
        </button>
      </div>
    </div>
  );
}

export const VoiceFirstChat = memo(VoiceFirstChatInner);
export default VoiceFirstChat;
