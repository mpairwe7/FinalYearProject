/**
 * React hook managing the streaming voice WebSocket lifecycle.
 *
 * Wires the VoiceWebSocket client to the useVoiceStore, dispatching
 * incoming events to the appropriate store actions.
 */

import { useCallback, useEffect, useRef } from 'react';
import { VoiceWebSocket, type VoiceWSConfig, type VoiceWSEvent } from '../services/voiceWebSocket';
import { useVoiceStore } from '../store/useVoiceStore';

export function useVoiceWebSocket() {
  const wsRef = useRef<VoiceWebSocket | null>(null);
  const audioQueueRef = useRef<ArrayBuffer[]>([]);
  const playingRef = useRef(false);

  const wsState = useVoiceStore((s) => s.wsState);
  const setWsState = useVoiceStore((s) => s.setWsState);
  const setSessionId = useVoiceStore((s) => s.setSessionId);
  const setVoicePhase = useVoiceStore((s) => s.setVoicePhase);
  const updatePartialTranscript = useVoiceStore((s) => s.updatePartialTranscript);
  const setFinalTranscript = useVoiceStore((s) => s.setFinalTranscript);
  const appendReplyToken = useVoiceStore((s) => s.appendReplyToken);
  const setLastLatency = useVoiceStore((s) => s.setLastLatency);
  const resetTurn = useVoiceStore((s) => s.resetTurn);

  const audioCtxRef = useRef<AudioContext | null>(null);

  // ── Play queued audio chunks ────────────────────────────────────
  const playNextChunk = useCallback(async () => {
    if (playingRef.current || audioQueueRef.current.length === 0) return;
    playingRef.current = true;

    if (!audioCtxRef.current || audioCtxRef.current.state === 'closed') {
      audioCtxRef.current = new AudioContext();
    }
    const ctx = audioCtxRef.current;

    // Resume AudioContext if suspended (Chrome/Safari autoplay policy)
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume();
      } catch {
        // resume() can fail if no user gesture yet — skip
      }
    }

    while (audioQueueRef.current.length > 0) {
      const chunk = audioQueueRef.current.shift();
      if (!chunk || chunk.byteLength === 0) continue;

      try {
        // Server sends WAV/audio bytes — decode and play via AudioContext
        const audioBuffer = await ctx.decodeAudioData(chunk.slice(0));
        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(ctx.destination);

        await new Promise<void>((resolve) => {
          source.onended = () => resolve();
          source.start(0);
        });
      } catch {
        // Playback error (e.g. raw PCM not decodable) — skip chunk
      }
    }

    playingRef.current = false;
  }, []);

  // ── Event dispatcher ────────────────────────────────────────────
  const handleEvent = useCallback(
    (event: VoiceWSEvent) => {
      switch (event.type) {
        case 'session_ready':
          setWsState('connected');
          setSessionId(event.session_id as string);
          break;

        case 'vad_state':
          if (event.speaking) {
            setVoicePhase('listening');
          }
          break;

        case 'transcript_partial':
          updatePartialTranscript(event.text as string);
          break;

        case 'transcript_final':
          setFinalTranscript(event.text as string);
          if (event.text) {
            setVoicePhase('processing');
          }
          break;

        case 'reply_text':
          appendReplyToken(event.text as string);
          setVoicePhase('speaking');
          break;

        case 'audio_end':
          setVoicePhase('idle');
          break;

        case 'latency_report':
          setLastLatency({
            asr_ms: event.asr_ms as number,
            mt_ms: event.mt_ms as number,
            llm_ms: event.llm_ms as number,
            tts_first_chunk_ms: event.tts_first_chunk_ms as number,
            total_ms: event.total_ms as number,
          });
          break;

        case 'error':
          if (event.recoverable) {
            setWsState('connecting');
          } else {
            setVoicePhase('error');
            setWsState('error');
          }
          break;
      }
    },
    [setWsState, setSessionId, setVoicePhase, updatePartialTranscript, setFinalTranscript, appendReplyToken, setLastLatency],
  );

  const handleAudioChunk = useCallback(
    (chunk: ArrayBuffer) => {
      audioQueueRef.current.push(chunk);
      playNextChunk();
    },
    [playNextChunk],
  );

  // ── Public API ──────────────────────────────────────────────────

  const connect = useCallback(
    (config: VoiceWSConfig) => {
      if (wsRef.current) {
        wsRef.current.disconnect();
      }

      const ws = new VoiceWebSocket();
      wsRef.current = ws;

      ws.onEvent(handleEvent);
      ws.onAudioChunk(handleAudioChunk);

      setWsState('connecting');
      resetTurn();
      ws.connect(config);
    },
    [handleEvent, handleAudioChunk, setWsState, resetTurn],
  );

  const disconnect = useCallback(() => {
    wsRef.current?.disconnect();
    wsRef.current = null;
    setWsState('disconnected');
    setVoicePhase('idle');
    audioQueueRef.current = [];
    playingRef.current = false;
  }, [setWsState, setVoicePhase]);

  const sendAudioChunk = useCallback((pcm16: ArrayBuffer) => {
    wsRef.current?.sendAudioChunk(pcm16);
  }, []);

  const bargeIn = useCallback(() => {
    wsRef.current?.bargeIn();
    audioQueueRef.current = [];
    playingRef.current = false;
    setVoicePhase('idle');
    resetTurn();
  }, [setVoicePhase, resetTurn]);

  // ── Cleanup on unmount ──────────────────────────────────────────
  useEffect(() => {
    return () => {
      wsRef.current?.disconnect();
      wsRef.current = null;
    };
  }, []);

  return {
    connect,
    disconnect,
    sendAudioChunk,
    bargeIn,
    isConnected: wsState === 'connected',
  };
}
