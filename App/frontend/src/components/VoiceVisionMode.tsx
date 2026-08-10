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
 *
 * Styling note: modeled on the working .camera-capture-* classes
 * (CameraCapture.tsx) — same full-screen-camera shell shape — plus the
 * voice-recording states this mode layers on top.
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
    <div className="vv-root" role="dialog" aria-modal="true" aria-label="Voice and vision mode">
      {/* Top bar */}
      <div className="vv-topbar">
        <button onClick={onClose} className="vfc-icon-btn" aria-label="Close vision mode">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <div className="vv-topbar-meta">
          {latencyMs > 0 && <span className="vv-chip">{latencyMs}ms</span>}
          <span className="vv-chip vv-chip-mode">Voice + Vision</span>
        </div>

        <span className="vv-spacer" aria-hidden="true" />
      </div>

      {/* Camera feed / captured image */}
      <div className="vv-viewport">
        {captureState === "preview" ? (
          <video ref={videoRef} autoPlay playsInline muted className="vv-video" />
        ) : capturedImageUrl ? (
          // Blob object URLs cannot be optimized by next/image.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={capturedImageUrl} alt="Captured document" className="vv-captured-img" />
        ) : null}

        {/* Hidden canvas for capture */}
        <canvas ref={canvasRef} className="vv-canvas" />

        {/* Document scanning guide overlay (preview mode) */}
        {captureState === "preview" && (
          <div className="vv-guide">
            <div className="vv-guide-frame">
              <span className="vv-guide-label">Align document here</span>
            </div>
          </div>
        )}

        {/* Processing overlay */}
        {captureState === "processing" && (
          <div className="vv-processing">
            <div className="vv-processing-inner">
              <span className="vfc-spinner" aria-hidden="true" />
              <p className="vv-processing-text">Processing document + voice...</p>
            </div>
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="vv-bottombar">
        {captureState === "preview" && (
          <div className="vv-shutter-row">
            <button onClick={capturePhoto} className="vv-shutter" aria-label="Capture document" />
          </div>
        )}

        {captureState === "recording" && (
          <div className="vv-record-row">
            <p className="vv-record-hint">Photo captured. Now ask your question by voice:</p>
            <div className="vv-record-actions">
              <button onClick={resetToPreview} className="vv-retake-btn" aria-label="Retake photo">
                Retake
              </button>
              {!isRecording ? (
                <button onClick={startVoiceRecording} className="vv-mic-btn" aria-label="Start recording">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 10v2a7 7 0 01-14 0v-2" />
                  </svg>
                </button>
              ) : (
                <button onClick={stopVoiceRecording} className="vv-stop-btn" aria-label="Stop recording">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        )}

        {captureState === "result" && (
          <div className="vv-result">
            {error && <p className="vv-result-error">{error}</p>}
            {transcript && (
              <div className="vv-result-card vv-result-card-transcript">
                <p className="vv-result-label">You said:</p>
                <p className="vv-result-text">{transcript}</p>
              </div>
            )}
            {ocrText && (
              <div className="vv-result-card vv-result-card-ocr">
                <p className="vv-result-label">Document text:</p>
                <p className="vv-result-text">{ocrText}</p>
              </div>
            )}
            {reply && (
              <div className="vv-result-card vv-result-card-reply">
                <p className="vv-result-label">Assistant:</p>
                <p className="vv-result-text">{reply}</p>
              </div>
            )}
            <button onClick={resetToPreview} className="vv-rescan-btn" aria-label="Scan another document">
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
