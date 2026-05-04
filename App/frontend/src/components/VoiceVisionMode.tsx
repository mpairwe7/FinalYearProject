"use client";

/**
 * VoiceVisionMode — Voice + Camera mode (Phase 27).
 *
 * Enables users to speak while the camera is active for document/receipt
 * scanning.  Designed for tax document processing: users can photograph a
 * tax form or receipt and ask questions about it verbally.
 *
 * Architecture:
 *   Camera feed → capture image
 *   Microphone  → capture audio
 *   POST /v1/voice/vision/chat → ASR + OCR + LLM + TTS
 *
 * Feature-flagged behind FLAG_VOICE_VISION.
 */

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useChatStore } from "@/store/useChatStore";
import { useVoiceStore } from "@/store/useVoiceStore";
import { authHeaders } from "@/lib/authSession";

interface VoiceVisionModeProps {
  onClose: () => void;
  locale?: string;
}

type CaptureState = "preview" | "recording" | "processing" | "result";

function VoiceVisionModeInner({ onClose, locale = "en" }: VoiceVisionModeProps) {
  const activeConversationId = useChatStore((state) => state.activeConversationId);
  const addTurns = useChatStore((state) => state.addTurns);
  const setCameraActive = useVoiceStore((state) => state.setCameraActive);

  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);

  const [captureState, setCaptureState] = useState<CaptureState>("preview");
  const [capturedImage, setCapturedImage] = useState<Blob | null>(null);
  const [isRecording, setIsRecording] = useState(false);
  const [ocrText, setOcrText] = useState("");
  const [reply, setReply] = useState("");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");
  const [latencyMs, setLatencyMs] = useState(0);
  const [capturedImageUrl, setCapturedImageUrl] = useState<string | null>(null);
  const imageUrlRef = useRef<string | null>(null);
  const sendRequestRef = useRef<((audioBlob: Blob) => Promise<void>) | undefined>(undefined);

  const setCapturedImageBlob = useCallback((blob: Blob | null) => {
    if (imageUrlRef.current) {
      URL.revokeObjectURL(imageUrlRef.current);
      imageUrlRef.current = null;
    }
    setCapturedImage(blob);
    if (blob) {
      const url = URL.createObjectURL(blob);
      imageUrlRef.current = url;
      setCapturedImageUrl(url);
    } else {
      setCapturedImageUrl(null);
    }
  }, []);

  /* Start camera */
  useEffect(() => {
    let mounted = true;

    async function startCamera() {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: "environment", // Back camera for documents
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        });
        if (mounted && videoRef.current) {
          videoRef.current.srcObject = stream;
          streamRef.current = stream;
          setCameraActive(true);
        }
      } catch (err) {
        console.error("Camera access denied:", err);
        setError("Camera access denied. Please allow camera permissions.");
      }
    }

    startCamera();

    return () => {
      mounted = false;
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
      if (imageUrlRef.current) {
        URL.revokeObjectURL(imageUrlRef.current);
        imageUrlRef.current = null;
      }
      setCameraActive(false);
    };
  }, [setCameraActive]);

  /* Capture photo from video feed */
  const capturePhoto = useCallback(() => {
    if (!videoRef.current || !canvasRef.current) return;

    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(video, 0, 0);

    canvas.toBlob(
      (blob) => {
        if (blob) {
          setCapturedImageBlob(blob);
          setCaptureState("recording");
          // Haptic feedback
          navigator.vibrate?.(50);
        }
      },
      "image/jpeg",
      0.85,
    );
  }, [setCapturedImageBlob]);

  /* Start voice recording */
  const startVoiceRecording = useCallback(async () => {
    try {
      const audioStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      const recorder = new MediaRecorder(audioStream, { mimeType: "audio/webm" });
      const chunks: BlobPart[] = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };

      recorder.onstop = async () => {
        audioStream.getTracks().forEach((t) => t.stop());
        const audioBlob = new Blob(chunks, { type: "audio/webm" });
        await sendRequestRef.current?.(audioBlob);
      };

      recorder.start();
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
      navigator.vibrate?.(30);
    } catch (err) {
      console.error("Microphone access denied:", err);
      setError("Microphone access denied.");
    }
  }, []);

  /* Stop voice recording */
  const stopVoiceRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    setIsRecording(false);
    setCaptureState("processing");
    navigator.vibrate?.(30);
  }, []);

  /* Send voice + vision request to backend */
  const sendVoiceVisionRequest = useCallback(
    async (audioBlob: Blob) => {
      if (!capturedImage) return;

      const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
      const formData = new FormData();
      formData.append("audio", audioBlob, "recording.webm");
      formData.append("image", capturedImage, "document.jpg");
      formData.append("language", locale);
      formData.append("tts_enabled", "true");
      formData.append("ocr_enabled", "true");

      if (activeConversationId) {
        formData.append("conversation_id", activeConversationId);
      }

      try {
        const res = await fetch(`${API_BASE}/v1/voice/vision/chat`, {
          method: "POST",
          headers: authHeaders({ "X-Voice-Consent": "true" }),
          body: formData,
        });

        if (!res.ok) {
          throw new Error(`Vision chat failed: ${res.status}`);
        }

        const data = await res.json();

        setTranscript(data.transcript || "");
        setOcrText(data.ocr_text || "");
        setReply(data.reply || "");
        setLatencyMs(Math.round((data.total_latency_s || 0) * 1000));

        // Add to chat history
        const userMessage = data.transcript
          ? `${data.transcript}${data.ocr_text ? `\n[Scanned document: ${data.ocr_text.substring(0, 100)}...]` : ""}`
          : "[Document scan]";

        addTurns([
          {
            id: crypto.randomUUID(),
            role: "user",
            content: userMessage,
            timestamp: Date.now(),
          },
        ]);

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

        // Play audio response
        if (data.reply_audio_base64) {
          const { playAudioBase64 } = await import("@/services/voiceService");
          await playAudioBase64(data.reply_audio_base64);
        }

        setCaptureState("result");
      } catch (err) {
        console.error("Voice vision error:", err);
        setError(err instanceof Error ? err.message : "Request failed");
        setCaptureState("result");
      }
    },
    [activeConversationId, addTurns, capturedImage, locale],
  );

  // Keep ref in sync so startVoiceRecording's closure always calls the latest version
  useEffect(() => {
    sendRequestRef.current = sendVoiceVisionRequest;
  }, [sendVoiceVisionRequest]);

  /* Reset to camera preview */
  const resetToPreview = useCallback(() => {
    setCapturedImageBlob(null);
    setCaptureState("preview");
    setOcrText("");
    setReply("");
    setTranscript("");
    setError("");
    setLatencyMs(0);
  }, [setCapturedImageBlob]);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 pt-4 safe-area-top z-10">
        <button
          onClick={onClose}
          className="p-2 rounded-full bg-black/50 text-white hover:bg-black/70"
          aria-label="Close vision mode"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="flex items-center gap-2">
          {latencyMs > 0 && (
            <span className="px-2 py-1 bg-black/50 text-white text-xs rounded-full">
              {latencyMs}ms
            </span>
          )}
          <span className="px-3 py-1 bg-violet-500/80 text-white text-sm rounded-full font-medium">
            Voice + Vision
          </span>
        </div>

        <div className="w-9" /> {/* Spacer for alignment */}
      </div>

      {/* Camera feed / captured image */}
      <div className="flex-1 relative">
        {captureState === "preview" ? (
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover"
          />
        ) : capturedImageUrl ? (
          // Blob object URLs cannot be optimized by next/image.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={capturedImageUrl}
            alt="Captured document"
            className="w-full h-full object-contain bg-gray-900"
          />
        ) : null}

        {/* Hidden canvas for capture */}
        <canvas ref={canvasRef} className="hidden" />

        {/* Document scanning guide overlay (preview mode) */}
        {captureState === "preview" && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="w-72 h-96 border-2 border-white/40 rounded-lg">
              <span className="absolute top-2 left-1/2 -translate-x-1/2 text-white/60 text-sm bg-black/50 px-3 py-1 rounded-full">
                Align document here
              </span>
            </div>
          </div>
        )}

        {/* Processing overlay */}
        {captureState === "processing" && (
          <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
            <div className="flex flex-col items-center gap-4">
              <div className="w-12 h-12 border-4 border-white/30 border-t-white rounded-full animate-spin" />
              <p className="text-white text-lg">Processing document + voice...</p>
            </div>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="bg-gray-900 px-4 py-4 safe-area-bottom">
        {captureState === "preview" && (
          <div className="flex items-center justify-center">
            <button
              onClick={capturePhoto}
              className="w-16 h-16 rounded-full bg-white border-4 border-gray-600 active:scale-95 transition-transform"
              aria-label="Capture document"
            />
          </div>
        )}

        {captureState === "recording" && (
          <div className="flex flex-col items-center gap-3">
            <p className="text-gray-300 text-sm">
              Photo captured. Now ask your question by voice:
            </p>
            <div className="flex items-center gap-4">
              <button
                onClick={resetToPreview}
                className="px-4 py-2 bg-gray-700 text-white rounded-full text-sm"
                aria-label="Retake photo"
              >
                Retake
              </button>
              {!isRecording ? (
                <button
                  onClick={startVoiceRecording}
                  className="w-14 h-14 rounded-full bg-green-500 flex items-center justify-center active:scale-95 transition-transform"
                  aria-label="Start recording"
                >
                  <svg className="w-7 h-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 01-14 0v-2" />
                  </svg>
                </button>
              ) : (
                <button
                  onClick={stopVoiceRecording}
                  className="w-14 h-14 rounded-full bg-red-500 flex items-center justify-center animate-pulse active:scale-95 transition-transform"
                  aria-label="Stop recording"
                >
                  <svg className="w-7 h-7 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        )}

        {captureState === "result" && (
          <div className="flex flex-col gap-3 max-h-60 overflow-y-auto">
            {error && (
              <p className="text-red-400 text-sm">{error}</p>
            )}
            {transcript && (
              <div className="p-3 bg-blue-500/10 rounded-xl">
                <p className="text-xs text-gray-400 mb-1">You said:</p>
                <p className="text-white text-sm">{transcript}</p>
              </div>
            )}
            {ocrText && (
              <div className="p-3 bg-amber-500/10 rounded-xl">
                <p className="text-xs text-gray-400 mb-1">Document text:</p>
                <p className="text-amber-200 text-sm">{ocrText}</p>
              </div>
            )}
            {reply && (
              <div className="p-3 bg-violet-500/10 rounded-xl">
                <p className="text-xs text-gray-400 mb-1">Assistant:</p>
                <p className="text-white text-sm">{reply}</p>
              </div>
            )}
            <button
              onClick={resetToPreview}
              className="w-full py-3 bg-blue-500 text-white rounded-xl font-medium"
              aria-label="Scan another document"
            >
              Scan another document
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export const VoiceVisionMode = memo(VoiceVisionModeInner);
export default VoiceVisionMode;
