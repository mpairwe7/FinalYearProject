"use client";

import React, { memo, useCallback, useEffect, useRef, useState } from "react";
import { CameraIcon, CloseIcon } from "./Icons";

/**
 * Camera capture component for voice + vision mode (Phase 4).
 *
 * Uses the rear-facing camera to capture document images while the
 * user speaks.  Designed for tax document scanning (receipts, TINs,
 * registration certificates).
 */

interface CameraCaptureProps {
  active: boolean;
  onCapture: (imageBase64: string) => void;
  onClose: () => void;
}

function CameraCaptureInner({ active, onCapture, onClose }: CameraCaptureProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  const stopStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  // ── Start camera ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    let resetTimer: ReturnType<typeof setTimeout> | null = null;

    const resetUiStateSoon = () => {
      resetTimer = setTimeout(() => {
        if (!cancelled) {
          setReady(false);
          setError(null);
        }
      }, 0);
    };

    if (!active) {
      stopStream();
      resetUiStateSoon();
      return () => {
        cancelled = true;
        if (resetTimer) clearTimeout(resetTimer);
      };
    }

    void navigator.mediaDevices
      .getUserMedia({
        video: {
          facingMode: "environment", // Rear camera for document scanning
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
      })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        setReady(false);
        setError(null);
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.onloadedmetadata = () => setReady(true);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Camera access denied. Check your browser permissions.");
        }
      });

    return () => {
      cancelled = true;
      if (resetTimer) clearTimeout(resetTimer);
      stopStream();
    };
  }, [active, stopStream]);

  // ── Capture frame ───────────────────────────────────────────────
  const captureFrame = useCallback(() => {
    if (!videoRef.current || !canvasRef.current || !ready || !streamRef.current) return;

    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.drawImage(video, 0, 0);
    // JPEG at 85% quality — good balance of size and readability
    const base64 = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
    onCapture(base64);
  }, [ready, onCapture]);

  const canCapture = ready;

  // ── Render ──────────────────────────────────────────────────────
  if (!active) return null;

  return (
    <div className="camera-capture-container" role="dialog" aria-label="Document camera">
      <div className="camera-capture-header">
        <span>Scan Document</span>
        <button onClick={onClose} aria-label="Close camera">
          <CloseIcon />
        </button>
      </div>

      {error ? (
        <div className="camera-capture-error" role="alert">
          {error}
        </div>
      ) : (
        <>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="camera-capture-video"
          />
          <canvas ref={canvasRef} style={{ display: "none" }} />
        </>
      )}

      <div className="camera-capture-footer">
        <button
          className="camera-capture-btn"
          onClick={captureFrame}
          disabled={!canCapture}
          aria-label="Capture document"
        >
          <CameraIcon />
          <span>Capture</span>
        </button>
      </div>
    </div>
  );
}

export const CameraCapture = memo(CameraCaptureInner);
