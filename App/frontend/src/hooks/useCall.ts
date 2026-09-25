import { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from '@/lib/i18n';
import {
  type CallLanguage,
  type CallLanguageSource,
  isCallLanguage,
  useCallStore,
} from '@/store/useCallStore';
import { CallSocket } from '@/services/callSocket';
import { PCMPlayer } from '@/services/pcmPlayer';
import { AudioRecorder } from '@/services/voiceService';
import { playDialTone, playHoldTone, playJoinChime } from '@/services/callTones';
import { resetAudioLevels, setInputLevel, setOutputLevel } from '@/services/audioLevelBus';

const LANGUAGE_SOURCES: readonly CallLanguageSource[] = ['default', 'auto', 'explicit', 'override'];

export function useCall() {
  const store = useCallStore();
  const t = useTranslation();
  const socketRef = useRef<CallSocket | null>(null);
  const playerRef = useRef<PCMPlayer | null>(null);
  const micCleanupRef = useRef<(() => void) | null>(null);
  const dialToneCleanupRef = useRef<(() => void) | null>(null);
  const holdToneCleanupRef = useRef<(() => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Keep a ref of isMuted so the audio callback gets the latest value without recreation
  const isMutedRef = useRef(store.isMuted);
  useEffect(() => {
    isMutedRef.current = store.isMuted;
  }, [store.isMuted]);

  const cleanupAudio = useCallback(() => {
    resetAudioLevels();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (dialToneCleanupRef.current) {
      dialToneCleanupRef.current();
      dialToneCleanupRef.current = null;
    }
    if (holdToneCleanupRef.current) {
      holdToneCleanupRef.current();
      holdToneCleanupRef.current = null;
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

  /** Pin the call to *language* for the rest of the call (the server confirms). */
  const setLanguage = useCallback(
    (language: CallLanguage) => {
      if (socketRef.current) {
        socketRef.current.setLanguage(language);
      }
      store.setLanguage(language, 'override');
    },
    [store],
  );

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
      player.onLevel((level) => setOutputLevel(level));
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
              {
                const offered = Array.isArray(msg.languages)
                  ? msg.languages.filter(isCallLanguage)
                  : [];
                const opening = isCallLanguage(msg.language) ? msg.language : 'en';
                store.setLanguageOptions(
                  Boolean(msg.language_detection) && offered.length > 1,
                  offered.length > 0 ? offered : [opening],
                  opening,
                );
              }
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
              store.setUserSpeaking(true);
              break;
            }
            case 'user_speaking': {
              store.setUserSpeaking(Boolean(msg.speaking));
              break;
            }
            case 'caption': {
              const spk = typeof msg.speaker === 'string' ? msg.speaker : 'assistant';
              store.addCaption({
                speaker: spk as 'caller' | 'assistant' | 'officer' | 'system',
                text: String(msg.text || ''),
                final: Boolean(msg.final ?? true),
                turn_id: typeof msg.turn_id === 'number' ? msg.turn_id : undefined,
              });
              break;
            }
            case 'status': {
              if (msg.status === 'transferring') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                store.setStatus('transferring');
                if (msg.ticket_ref) store.setTicketRef(String(msg.ticket_ref));
              } else if (msg.status === 'bridged') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                store.setStatus('officer');
                if (msg.officer_name) store.setOfficerName(String(msg.officer_name));
                // Play join chime
                if (audioCtxRef.current) {
                  playJoinChime(audioCtxRef.current);
                }
              } else if (msg.status === 'on_hold') {
                store.setStatus('on_hold');
                if (!holdToneCleanupRef.current && audioCtxRef.current) {
                  holdToneCleanupRef.current = playHoldTone(audioCtxRef.current);
                }
              } else if (msg.status === 'reconnecting') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                store.setStatus('reconnecting');
              } else if (msg.status === 'ai') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                store.setStatus('ai');
              } else if (msg.status === 'ended') {
                hangup();
              }
              break;
            }
            case 'language': {
              const language = msg.language;
              if (!isCallLanguage(language)) break;
              const source = LANGUAGE_SOURCES.find((src) => src === msg.source) ?? 'auto';
              const previous = useCallStore.getState().language;
              store.setLanguage(language, source);
              if (language !== previous) {
                store.addCaption({
                  speaker: 'system',
                  text: t('call.languageSwitched', { language: t(`call.lang.${language}`) }),
                  final: true,
                });
              }
              break;
            }
            case 'ended': {
              hangup();
              break;
            }
            case 'error': {
              store.setError(String(msg.detail || 'Call error occurred'));
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
      // `locale` stays the chat's language: a single-engine call is held in
      // it. A multilingual call opens in English whatever it says and treats
      // `preferred_locale` as a hint.
      socket.connect({
        locale,
        preferred_locale: locale,
        voice_consent_accepted: true,
        sample_rate: 16000,
      });

      // Start microphone streaming
      try {
        const recorder = new AudioRecorder();
        let silenceTimer: ReturnType<typeof setTimeout> | null = null;
        const cleanup = await recorder.startStreaming(
          (pcmChunk) => {
            if (!isMutedRef.current && socketRef.current) {
              socketRef.current.sendAudio(pcmChunk);
              // Client-side voice energy check to immediately trigger Gemini Live listening state
              if (pcmChunk.byteLength >= 2) {
                const int16 = new Int16Array(pcmChunk);
                let sum = 0;
                for (let i = 0; i < int16.length; i += 2) {
                  sum += int16[i] * int16[i];
                }
                const rms = Math.sqrt(sum / (int16.length / 2));
                // The same reading drives the orb. 6000 of 32767 is roughly
                // conversational speech at a laptop mic, so normal talking
                // fills the orb without a shout pinning it at 1.
                setInputLevel(rms / 6000);
                if (rms > 1200) {
                  store.setUserSpeaking(true);
                  if (silenceTimer) clearTimeout(silenceTimer);
                  silenceTimer = setTimeout(() => {
                    store.setUserSpeaking(false);
                  }, 1400);
                }
              }
            } else if (isMutedRef.current) {
              setInputLevel(0);
            }
          },
          { echoCancellation: true, noiseSuppression: true },
        );
        // Both the recorder teardown AND the pending silence timer, or a call
        // that ends mid-utterance leaves a timer to fire `setUserSpeaking`
        // against a store the next call has already reset.
        micCleanupRef.current = () => {
          if (silenceTimer) clearTimeout(silenceTimer);
          cleanup();
        };
      } catch (err: unknown) {
        store.setError((err as Error)?.message || 'Microphone access denied');
        hangup();
      }
    },
    [cleanupAudio, hangup, store, t],
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
    isUserSpeaking: store.isUserSpeaking,
    activeAiText: store.activeAiText,
    error: store.error,
    languageDetection: store.languageDetection,
    languages: store.languages,
    language: store.language,
    languageSource: store.languageSource,
    openCall: store.openCall,
    closeCall: store.closeCall,
    startCall,
    hangup,
    requestOfficer,
    toggleMute,
    setLanguage,
  };
}
