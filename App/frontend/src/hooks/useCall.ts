import { useCallback, useEffect, useRef } from 'react';
import { useCallStore } from '@/store/useCallStore';
import { CallSocket } from '@/services/callSocket';
import { PCMPlayer } from '@/services/pcmPlayer';
import { AudioRecorder } from '@/services/voiceService';
import { playDialTone, playJoinChime } from '@/services/callTones';

export function useCall() {
  const store = useCallStore();
  const socketRef = useRef<CallSocket | null>(null);
  const playerRef = useRef<PCMPlayer | null>(null);
  const micCleanupRef = useRef<(() => void) | null>(null);
  const dialToneCleanupRef = useRef<(() => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Keep a ref of isMuted so the audio callback gets the latest value without recreation
  const isMutedRef = useRef(store.isMuted);
  useEffect(() => {
    isMutedRef.current = store.isMuted;
  }, [store.isMuted]);

  const cleanupAudio = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (dialToneCleanupRef.current) {
      dialToneCleanupRef.current();
      dialToneCleanupRef.current = null;
    }
    if (micCleanupRef.current) {
      micCleanupRef.current();
      micCleanupRef.current = null;
    }
    if (playerRef.current) {
      playerRef.current.close();
      playerRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
  }, []);

  const hangup = useCallback(() => {
    if (socketRef.current) {
      socketRef.current.hangup();
      socketRef.current = null;
    }
    cleanupAudio();
    store.setStatus('ended');
  }, [cleanupAudio, store]);

  const requestOfficer = useCallback(() => {
    if (socketRef.current) {
      socketRef.current.requestOfficer();
    }
    store.setStatus('transferring');
  }, [store]);

  const toggleMute = useCallback(() => {
    store.setMuted((prev) => !prev);
  }, [store]);

  const startCall = useCallback(
    async (locale = 'en') => {
      cleanupAudio();
      store.setStatus('dialing');
      store.setError(null);

      // Play dialing ring tone
      try {
        const toneCtx = new AudioContext();
        audioCtxRef.current = toneCtx;
        dialToneCleanupRef.current = playDialTone(toneCtx);
      } catch {}

      // Create PCM16 player
      const player = new PCMPlayer(16000);
      playerRef.current = player;
      player.init().catch(() => {});

      // Create WebSocket
      const socket = new CallSocket({
        onAudio: (pcmChunk) => {
          player.push(pcmChunk);
        },
        onMessage: (msg) => {
          if (!msg || typeof msg !== 'object') return;

          switch (msg.type) {
            case 'call_ready': {
              // Stop dial tone and enter active AI call
              if (dialToneCleanupRef.current) {
                dialToneCleanupRef.current();
                dialToneCleanupRef.current = null;
              }
              store.setStatus('ai');
              // Start call timer
              if (!timerRef.current) {
                timerRef.current = setInterval(() => {
                  store.setDuration((d) => d + 1);
                }, 1000);
              }
              break;
            }
            case 'interrupt': {
              player.flush();
              break;
            }
            case 'caption': {
              store.addCaption({
                speaker: msg.speaker || 'assistant',
                text: msg.text || '',
                final: msg.final ?? true,
                turn_id: msg.turn_id,
              });
              break;
            }
            case 'status': {
              if (msg.status === 'transferring') {
                store.setStatus('transferring');
                if (msg.ticket_ref) store.setTicketRef(msg.ticket_ref);
              } else if (msg.status === 'bridged') {
                store.setStatus('officer');
                if (msg.officer_name) store.setOfficerName(msg.officer_name);
                // Play join chime
                if (audioCtxRef.current) {
                  playJoinChime(audioCtxRef.current);
                }
              } else if (msg.status === 'ai') {
                store.setStatus('ai');
              } else if (msg.status === 'ended') {
                hangup();
              }
              break;
            }
            case 'ended': {
              hangup();
              break;
            }
            case 'error': {
              store.setError(msg.detail || 'Call error occurred');
              break;
            }
          }
        },
        onError: (err) => {
          store.setError(err);
        },
        onClose: () => {
          cleanupAudio();
          store.setStatus('ended');
        },
      });

      socketRef.current = socket;
      socket.connect({
        locale,
        voice_consent_accepted: true,
        sample_rate: 16000,
      });

      // Start microphone streaming
      try {
        const recorder = new AudioRecorder();
        const cleanup = await recorder.startStreaming(
          (pcmChunk) => {
            if (!isMutedRef.current && socketRef.current) {
              socketRef.current.sendAudio(pcmChunk);
            }
          },
          { echoCancellation: true, noiseSuppression: true },
        );
        micCleanupRef.current = cleanup;
      } catch (err: unknown) {
        store.setError((err as Error)?.message || 'Microphone access denied');
        hangup();
      }
    },
    [cleanupAudio, hangup, store],
  );

  useEffect(() => {
    return () => {
      cleanupAudio();
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, [cleanupAudio]);

  return {
    isOpen: store.isOpen,
    status: store.status,
    duration: store.duration,
    isMuted: store.isMuted,
    officerName: store.officerName,
    ticketRef: store.ticketRef,
    captions: store.captions,
    currentCaption: store.currentCaption,
    error: store.error,
    openCall: store.openCall,
    closeCall: store.closeCall,
    startCall,
    hangup,
    requestOfficer,
    toggleMute,
  };
}
