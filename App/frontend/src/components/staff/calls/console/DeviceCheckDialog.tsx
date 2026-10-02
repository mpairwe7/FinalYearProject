'use client';

import React, { useEffect, useRef, useState } from 'react';
import { playJoinChime } from '@/services/callTones';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

/**
 * Shift-start device check (plan §3.6): the microphone once, a live input
 * meter, a test sound, and desktop alerts — all asked for here, never on load.
 */
export function DeviceCheckDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const setMicReady = useCallConsoleStore((s) => s.setMicReady);
  const [status, setStatus] = useState<'idle' | 'asking' | 'ready' | 'denied'>('idle');
  const [notifications, setNotifications] = useState<string>(() =>
    typeof Notification === 'undefined' ? 'unsupported' : Notification.permission,
  );
  const meterRef = useRef<HTMLSpanElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) dialogRef.current?.focus();
    return () => {
      cleanupRef.current?.();
      cleanupRef.current = null;
    };
  }, [open]);

  if (!open) return null;

  const testMic = async () => {
    setStatus('asking');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      const ctx = new AudioContext();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      let frame = 0;
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
        if (meterRef.current) meterRef.current.style.setProperty('--cc-meter', String(Math.min(1, peak / 64)));
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
      cleanupRef.current = () => {
        cancelAnimationFrame(frame);
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close();
      };
      setStatus('ready');
      setMicReady(true);
    } catch {
      setStatus('denied');
      setMicReady(false);
    }
  };

  const testSound = () => {
    try {
      const ctx = new AudioContext();
      playJoinChime(ctx);
      setTimeout(() => void ctx.close(), 800);
    } catch {
      /* no audio output */
    }
  };

  const allowNotifications = async () => {
    if (typeof Notification === 'undefined') return;
    setNotifications(await Notification.requestPermission());
  };

  const close = () => {
    cleanupRef.current?.();
    cleanupRef.current = null;
    onClose();
  };

  return (
    <div className="cc-dialog-scrim" onClick={close}>
      <div
        ref={dialogRef}
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cc-device-title"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.key === 'Escape' && close()}
      >
        <h2 id="cc-device-title" className="cc-dialog-title">
          Ready to take calls
        </h2>
        <div className="cc-dialog-row">
          <button type="button" className="cc-btn" onClick={testMic} disabled={status === 'asking'}>
            {status === 'ready' ? 'Microphone on' : 'Test microphone'}
          </button>
          <span className="cc-meter" ref={meterRef} aria-hidden="true" />
          <span className="cc-dialog-note" role="status">
            {status === 'ready' && 'Speak — the bar should move.'}
            {status === 'denied' && 'Microphone blocked. Allow it in the browser’s site settings.'}
          </span>
        </div>
        <div className="cc-dialog-row">
          <button type="button" className="cc-btn" onClick={testSound}>
            Play test sound
          </button>
        </div>
        <div className="cc-dialog-row">
          <button
            type="button"
            className="cc-btn"
            onClick={allowNotifications}
            disabled={notifications !== 'default'}
          >
            {notifications === 'granted'
              ? 'Desktop alerts on'
              : notifications === 'denied'
                ? 'Desktop alerts blocked'
                : notifications === 'unsupported'
                  ? 'Desktop alerts unavailable'
                  : 'Allow desktop alerts'}
          </button>
        </div>
        <div className="cc-dialog-actions">
          <button type="button" className="cc-btn cc-btn--primary" onClick={close}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
