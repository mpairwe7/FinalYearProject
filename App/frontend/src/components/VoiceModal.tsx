"use client";

import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import { MicIcon, CloseIcon } from "./Icons";

/**
 * Full-screen voice recording modal — Grok-inspired design.
 *
 * Shows animated pulsing rings during recording, live transcription
 * word-by-word, and large Cancel / Send controls.
 */

interface VoiceModalProps {
  open: boolean;
  isRecording: boolean;
  isProcessing: boolean;
  transcript: string;
  locale: string;
  onCancel: () => void;
  onApprove: () => void;
  onStartRecording: () => void;
  onStopRecording: () => void;
}

function PulseRings({ active }: { active: boolean }) {
  return (
    <div className="voice-rings" aria-hidden="true">
      <div className={`voice-ring voice-ring-1 ${active ? "voice-ring-active" : ""}`} />
      <div className={`voice-ring voice-ring-2 ${active ? "voice-ring-active" : ""}`} />
      <div className={`voice-ring voice-ring-3 ${active ? "voice-ring-active" : ""}`} />
      <div className="voice-ring-center">
        <MicIcon />
      </div>
    </div>
  );
}

/** Simple waveform visualisation using Web Audio AnalyserNode. */
function Waveform({ stream }: { stream: MediaStream | null }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (!stream || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d");
    if (!ctx) return;

    const audioCtx = new AudioContext();
    const src = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    src.connect(analyser);

    const bufLen = analyser.frequencyBinCount;
    const data = new Uint8Array(bufLen);
    const w = canvasRef.current.width;
    const h = canvasRef.current.height;

    const draw = () => {
      analyser.getByteTimeDomainData(data);
      ctx.clearRect(0, 0, w, h);

      ctx.beginPath();
      const sliceW = w / bufLen;
      let x = 0;
      for (let i = 0; i < bufLen; i++) {
        const v = data[i] / 128.0;
        const y = (v * h) / 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
        x += sliceW;
      }
      ctx.strokeStyle = "rgba(0, 168, 143, 0.75)";
      ctx.lineWidth = 2;
      ctx.stroke();

      rafRef.current = requestAnimationFrame(draw);
    };
    draw();

    return () => {
      cancelAnimationFrame(rafRef.current);
      src.disconnect();
      audioCtx.close();
    };
  }, [stream]);

  return (
    <canvas
      ref={canvasRef}
      width={280}
      height={60}
      className="voice-waveform"
      aria-hidden="true"
    />
  );
}

function VoiceModalInner({
  open,
  isRecording,
  isProcessing,
  transcript,
  locale,
  onCancel,
  onApprove,
  onStartRecording,
  onStopRecording,
}: VoiceModalProps) {
  const [stream, setStream] = useState<MediaStream | null>(null);

  useEffect(() => {
    if (isRecording) {
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then(setStream)
        .catch(() => setStream(null));
    } else {
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
        setStream(null);
      }
    }
    return () => {
      stream?.getTracks().forEach((t) => t.stop());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isRecording]);

  const handleMicTap = useCallback(() => {
    if (isRecording) onStopRecording();
    else onStartRecording();
  }, [isRecording, onStartRecording, onStopRecording]);

  if (!open) return null;

  return (
    <div className="voice-modal-overlay" role="dialog" aria-modal="true" aria-label="Voice input">
      <div className="voice-modal">
        {/* Close */}
        <button className="voice-modal-close" onClick={onCancel} aria-label="Cancel voice input">
          <CloseIcon />
        </button>

        {/* Status */}
        <div className="voice-modal-status">
          {isProcessing
            ? "Processing..."
            : isRecording
              ? "Listening..."
              : "Tap the microphone to speak"}
        </div>

        {/* Pulse rings + waveform */}
        <button
          className={`voice-modal-mic ${isRecording ? "voice-modal-mic-active" : ""}`}
          onClick={handleMicTap}
          disabled={isProcessing}
          aria-label={isRecording ? "Stop recording" : "Start recording"}
        >
          <PulseRings active={isRecording} />
        </button>

        {isRecording && stream && <Waveform stream={stream} />}

        {/* Language indicator */}
        <div className="voice-modal-lang">
          {locale === "lg" ? "Luganda" : "English"}
        </div>

        {/* Live transcript */}
        {transcript && (
          <div className="voice-modal-transcript" aria-live="polite">
            <span className="voice-modal-transcript-label">Transcript</span>
            <p className="voice-modal-transcript-text">{transcript}</p>
          </div>
        )}

        {/* Action buttons */}
        {transcript && !isRecording && (
          <div className="voice-modal-actions">
            <button className="voice-modal-btn voice-modal-btn-cancel" onClick={onCancel}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              Cancel
            </button>
            <button className="voice-modal-btn voice-modal-btn-send" onClick={onApprove}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Send
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const VoiceModal = memo(VoiceModalInner);
export default VoiceModal;
