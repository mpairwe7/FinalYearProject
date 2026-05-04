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

const PHASE_CONFIG: Record<
  VoicePhase,
  { color: string; label: string; pulse: boolean; bgClass: string }
> = {
  idle: {
    color: "#3b82f6",
    label: "Tap to speak",
    pulse: false,
    bgClass: "bg-blue-500/10",
  },
  listening: {
    color: "#22c55e",
    label: "Listening...",
    pulse: true,
    bgClass: "bg-green-500/10",
  },
  processing: {
    color: "#f59e0b",
    label: "Thinking...",
    pulse: true,
    bgClass: "bg-amber-500/10",
  },
  speaking: {
    color: "#8b5cf6",
    label: "Speaking...",
    pulse: true,
    bgClass: "bg-violet-500/10",
  },
  error: {
    color: "#ef4444",
    label: "Error — tap to retry",
    pulse: false,
    bgClass: "bg-red-500/10",
  },
  offline: {
    color: "#6b7280",
    label: "Offline mode",
    pulse: false,
    bgClass: "bg-gray-500/10",
  },
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

/** Animated voice orb with state-driven ring effects */
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
      className="relative flex items-center justify-center w-44 h-44 rounded-full focus:outline-none"
      aria-label={config.label}
    >
      {/* Outer pulse rings */}
      {config.pulse && (
        <>
          <span
            className="absolute inset-0 rounded-full animate-ping opacity-20"
            style={{ backgroundColor: config.color }}
          />
          <span
            className="absolute inset-2 rounded-full animate-pulse opacity-30"
            style={{ backgroundColor: config.color }}
          />
        </>
      )}

      {/* Main orb */}
      <span
        className="relative z-10 flex items-center justify-center w-32 h-32 rounded-full shadow-2xl transition-colors duration-300"
        style={{ backgroundColor: config.color }}
      >
        {/* Waveform bars inside orb during listening */}
        {phase === "listening" && waveformData.length > 0 ? (
          <span className="flex items-end gap-0.5 h-12">
            {waveformData.slice(0, 12).map((v, i) => (
              <span
                key={i}
                className="w-1.5 bg-white/90 rounded-full transition-all duration-75"
                style={{ height: `${Math.max(4, v * 48)}px` }}
              />
            ))}
          </span>
        ) : phase === "processing" ? (
          <span className="w-10 h-10 border-4 border-white/30 border-t-white rounded-full animate-spin" />
        ) : (
          /* Microphone icon */
          <svg
            className="w-12 h-12 text-white"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8"
            />
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
    <div className="flex items-center gap-2 px-4 py-2 bg-gray-800/90 text-gray-300 rounded-full text-sm">
      <span className="w-2 h-2 bg-amber-400 rounded-full" />
      <span>Offline mode</span>
      {pendingCount > 0 && (
        <span className="px-2 py-0.5 bg-amber-500/20 text-amber-300 rounded-full text-xs">
          {pendingCount} pending
        </span>
      )}
      <span className="text-xs text-gray-500">Sync when online</span>
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
  const color = ms < 1200 ? "text-green-400" : ms < 2000 ? "text-amber-400" : "text-red-400";
  return <span className={`text-xs ${color}`}>{ms}ms</span>;
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
              recognition.lang = locale === "lg" ? "lg-UG" : "en-US";
              recognition.continuous = false;
              recognition.interimResults = false;
              recognition.onresult = (e) => {
                const text = e.results?.[0]?.[0]?.transcript || "";
                if (text) {
                  addTurns([
                    { id: crypto.randomUUID(), role: "user", content: text, timestamp: Date.now(), offlineMode: true },
                  ]);
                  setFinalTranscript(text);

                  // Use browser TTS for offline reply hint
                  if ("speechSynthesis" in window) {
                    const utterance = new SpeechSynthesisUtterance(
                      "I'm currently offline. Your question has been saved and will be answered when you're back online."
                    );
                    utterance.lang = locale === "lg" ? "en-UG" : "en-US";
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
    <div
      className={`fixed inset-0 z-50 flex flex-col items-center justify-between ${config.bgClass} bg-gray-950 transition-colors duration-500`}
    >
      {/* Top bar */}
      <div className="flex items-center justify-between w-full px-4 pt-4 safe-area-top">
        <button
          onClick={onClose}
          className="p-2 rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors"
          aria-label="Close voice chat"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="flex items-center gap-3">
          <LatencyBadge latency={lastLatency} />
          {!isOnline && <OfflineBanner pendingCount={pendingCount} />}
        </div>

        {onOpenVision && (
          <button
            onClick={() => {
              haptic();
              onOpenVision();
            }}
            className="p-2 rounded-full bg-white/10 text-white hover:bg-white/20 transition-colors"
            aria-label="Open camera mode"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
              <circle cx="12" cy="13" r="3" />
            </svg>
          </button>
        )}
      </div>

      {/* Transcript display */}
      <div className="flex-1 flex flex-col items-center justify-center gap-6 px-8 w-full max-w-lg">
        {/* Final transcript */}
        {finalTranscript && (
          <div className="w-full p-4 bg-white/5 rounded-2xl">
            <p className="text-sm text-gray-400 mb-1">You said:</p>
            <p className="text-white text-lg">{finalTranscript}</p>
          </div>
        )}

        {/* Partial transcript during recording */}
        {partialTranscript && phase === "listening" && (
          <div className="w-full p-3 bg-green-500/10 rounded-xl">
            <p className="text-green-300 text-base italic">{partialTranscript}</p>
          </div>
        )}

        {/* Voice orb */}
        <VoiceOrb phase={phase} onTap={handleOrbTap} waveformData={waveformData} />

        <p className="text-lg text-gray-300 font-medium">{config.label}</p>

        {/* Streaming reply */}
        {streamingReply && (
          <div className="w-full p-4 bg-violet-500/10 rounded-2xl max-h-48 overflow-y-auto">
            <p className="text-sm text-gray-400 mb-1">Assistant:</p>
            <p className="text-white">{streamingReply}</p>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="flex items-center gap-4 pb-8 safe-area-bottom">
        {/* Barge-in button (visible during speaking) */}
        {phase === "speaking" && (
          <button
            onClick={handleBargeIn}
            className="px-6 py-3 bg-red-500/80 text-white rounded-full font-medium hover:bg-red-500 transition-colors"
          >
            Stop speaking
          </button>
        )}

        {/* Language toggle */}
        <button
          onClick={() => {
            haptic();
            setLocale(locale === "en" ? "lg" : "en");
          }}
          className="px-4 py-2 bg-white/10 text-white rounded-full text-sm hover:bg-white/20 transition-colors"
          aria-label={`Switch language to ${locale === "en" ? "Luganda" : "English"}`}
        >
          {locale === "en" ? "English" : "Luganda"}
        </button>
      </div>
    </div>
  );
}

export const VoiceFirstChat = memo(VoiceFirstChatInner);
export default VoiceFirstChat;
