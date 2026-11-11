import { useCallback, useEffect, useRef } from 'react';
import { useTranslation } from '@/lib/i18n';
import {
  type CallControlAction,
  type CallLanguage,
  type CallLanguageSource,
  type CallStatus,
  type CallTurnPhase,
  isCallLanguage,
  useCallStore,
} from '@/store/useCallStore';
import { CallSocket } from '@/services/callSocket';
import { getAuthToken } from '@/lib/authSession';
import { getChatSessionId } from '@/lib/chatSession';
import { accountApi } from '@/services/accountApi';
import { useChatStore } from '@/store/useChatStore';
import {
  LiveKitCallSession,
  type LiveKitCallCredentials,
  SPEAKING_LEVEL,
} from '@/services/liveKitCallSession';
import { PCMPlayer } from '@/services/pcmPlayer';
import { AudioRecorder } from '@/services/voiceService';
import { playDialTone, playHoldTone, playJoinChime } from '@/services/callTones';
import { resetAudioLevels, setInputLevel, setOutputLevel } from '@/services/audioLevelBus';

const LANGUAGE_SOURCES: readonly CallLanguageSource[] = ['default', 'auto', 'explicit', 'override'];

type CallErrorKey =
  | 'call.error.unavailable'
  | 'call.error.busy'
  | 'call.error.signin'
  | 'call.error.dropped'
  | 'call.error.noConnection'
  | 'call.error.generic';

/** The message for an `error` frame's `code`. The server's English `detail` is never shown. */
function errorKeyForCode(code: unknown): CallErrorKey {
  switch (code) {
    case 'unavailable':
      return 'call.error.unavailable';
    case 'busy':
      return 'call.error.busy';
    case 'signin':
      return 'call.error.signin';
    default:
      return 'call.error.generic';
  }
}

/**
 * Why the call's socket closed, for a close the caller did not ask for and
 * the server did not announce. Every code used to read "Thank you for calling
 * URA", with no way to call again (G605).
 */
function errorKeyForClose(code: number, connected: boolean): CallErrorKey {
  switch (code) {
    case 1001:
      return 'call.error.unavailable';
    case 1013:
      return 'call.error.busy';
    case 4401:
      return 'call.error.signin';
    case 1003:
    case 4403:
      return 'call.error.generic';
    default:
      return connected ? 'call.error.dropped' : 'call.error.noConnection';
  }
}

export function useCall() {
  const store = useCallStore();
  const t = useTranslation();
  const socketRef = useRef<CallSocket | null>(null);
  const liveKitSessionRef = useRef<LiveKitCallSession | null>(null);
  const playerRef = useRef<PCMPlayer | null>(null);
  const micCleanupRef = useRef<(() => void) | null>(null);
  const micFailedRef = useRef(false);
  const startLegacyAudioRef = useRef<(() => Promise<boolean>) | null>(null);
  const dialToneCleanupRef = useRef<(() => void) | null>(null);
  const dialToneCtxRef = useRef<AudioContext | null>(null);
  const holdToneCleanupRef = useRef<(() => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  /**
   * Which call the hook is on. Bumped whenever call audio is torn down, so
   * work a call started (a microphone prompt still open) can tell, when it
   * finishes, that its call is over: hanging up during the prompt used to
   * leave the microphone open, or stream it into the next call (G601).
   */
  const callGenRef = useRef(0);
  /** The server answered with `call_ready`. */
  const connectedRef = useRef(false);
  /** The server said the call ended; the close that follows is not a failure. */
  const endedByServerRef = useRef(false);
  /** The latest mute change asked of LiveKit; an older one's result is stale. */
  const muteRequestRef = useRef(0);
  /**
   * A microphone or mute operation's message is marked as its own, and cleared
   * when that operation recovers or the call ends normally. An error from
   * anything else (the socket, the server) replaces the mark, so it is left
   * alone even when it reads the same.
   */
  const setMicError = useCallback((message: string) => store.setError(message, 'mic'), [store]);
  const clearMicError = useCallback(() => {
    if (useCallStore.getState().errorSource === 'mic') store.setError(null);
  }, [store]);

  const getToneContext = useCallback((): AudioContext | null => {
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      return audioCtxRef.current;
    }
    try {
      audioCtxRef.current = new AudioContext();
      void audioCtxRef.current.resume().catch(() => {});
      return audioCtxRef.current;
    } catch {
      return null;
    }
  }, []);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusBeforeReconnectRef = useRef<CallStatus>('ai');
  const speakingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep a ref of isMuted so the audio callback gets the latest value without recreation
  const isMutedRef = useRef(store.isMuted);
  useEffect(() => {
    isMutedRef.current = store.isMuted;
  }, [store.isMuted]);

  const cleanupAudio = useCallback(() => {
    callGenRef.current += 1;
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
    micFailedRef.current = false;
    startLegacyAudioRef.current = null;
    if (liveKitSessionRef.current) {
      void liveKitSessionRef.current.close();
      liveKitSessionRef.current = null;
    }
    if (speakingTimerRef.current) {
      clearTimeout(speakingTimerRef.current);
      speakingTimerRef.current = null;
    }
    if (playerRef.current) {
      playerRef.current.close();
      playerRef.current = null;
    }
    if (dialToneCtxRef.current && dialToneCtxRef.current.state !== 'closed') {
      dialToneCtxRef.current.close().catch(() => {});
    }
    dialToneCtxRef.current = null;
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
    // Listening and typing without a microphone, or a mute that did not
    // take, is not why the call ended. (A real failure set its own error.)
    clearMicError();
    cleanupAudio();
    store.setAudioBlocked(false);
    store.setStatus('ended');
  }, [cleanupAudio, clearMicError, store]);

  /** Start call audio the browser held back; wired to a tap on the call screen. */
  const resumeAudio = useCallback(() => {
    void liveKitSessionRef.current?.resumeAudio();
    void playerRef.current?.resume();
  }, []);

  const requestOfficer = useCallback(() => {
    if (socketRef.current) {
      socketRef.current.requestOfficer();
    }
    store.setStatus('transferring');
  }, [store]);

  /** While waiting: an officer calls back instead; the server ends the wait and says so. */
  const requestCallback = useCallback(() => {
    socketRef.current?.requestCallback();
  }, []);

  /**
   * Turn the LiveKit microphone on or off. A microphone the browser refuses
   * leaves the call connected and muted, with the listen-and-type message:
   * it used to hang up the whole call (G603), and a failed un-mute left the
   * screen saying the microphone was live (G609).
   */
  const applyLiveKitMute = useCallback(
    async (session: LiveKitCallSession, muted: boolean): Promise<boolean> => {
      const request = ++muteRequestRef.current;
      // A result that a newer request has overtaken is not what the screen shows.
      const stale = () => liveKitSessionRef.current !== session || muteRequestRef.current !== request;
      try {
        await session.setMuted(muted);
      } catch {
        if (stale()) return false;
        if (muted) {
          // Muting failed: the microphone may still be live, and the screen says so.
          store.setMuted(false);
          setMicError(t('call.error.generic'));
        } else {
          micFailedRef.current = true;
          store.setMuted(true);
          void session.setMuted(true).catch(() => {});
          setMicError(t('call.micFallback'));
        }
        return false;
      }
      if (stale()) return true;
      micFailedRef.current = muted ? micFailedRef.current : false;
      clearMicError();
      return true;
    },
    [clearMicError, setMicError, store, t],
  );

  const toggleMute = useCallback(() => {
    const session = liveKitSessionRef.current;
    if (
      useCallStore.getState().isMuted &&
      micFailedRef.current &&
      !session &&
      !micCleanupRef.current &&
      startLegacyAudioRef.current
    ) {
      void startLegacyAudioRef.current().then((ok) => {
        if (ok) {
          clearMicError();
          store.setMuted(false);
        }
      });
      return;
    }
    const next = !useCallStore.getState().isMuted;
    store.setMuted(next);
    if (session) void applyLiveKitMute(session, next);
  }, [applyLiveKitMute, clearMicError, store]);

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

  const sendControl = useCallback((action: CallControlAction) => {
    playerRef.current?.flush();
    socketRef.current?.sendControl(action);
  }, []);

  const sendTextTurn = useCallback((text: string): boolean => {
    const trimmed = text.trim();
    if (!trimmed) return false;
    playerRef.current?.flush();
    return socketRef.current?.sendTextTurn(trimmed) ?? false;
  }, []);

  const interruptCall = useCallback(() => {
    playerRef.current?.flush();
    socketRef.current?.interrupt();
    store.setCallRuntimeState({ turnPhase: 'listening' });
  }, [store]);

  const startCall = useCallback(
    async (locale = 'en') => {
      cleanupAudio();
      const callGen = callGenRef.current;
      const isCurrentCall = () => callGenRef.current === callGen;
      connectedRef.current = false;
      endedByServerRef.current = false;
      store.setStatus('dialing');
      store.setError(null);
      store.setAudioBlocked(false);
      // A redial from the ended screen starts afresh too (G609).
      store.setMuted(false);
      store.setOfficerName(null);
      store.setTicketRef(null);
      if (getAuthToken()) {
        void accountApi.grantConsents(['voice_recording']).catch(() => {});
      }

      // Play dialing ring tone and pre-initialize PCM playback AudioContext
      // synchronously inside the user's click gesture so cross-origin iframes
      // (HF Space) and mobile browsers do not suspend assistant speech playback.
      try {
        const toneCtx = new AudioContext();
        dialToneCtxRef.current = toneCtx;
        dialToneCleanupRef.current = playDialTone(toneCtx);
      } catch {}

      try {
        const player = new PCMPlayer(16000);
        playerRef.current = player;
        player.onLevel((level) => setOutputLevel(level));
        if (typeof player.onBlockedChange === 'function') {
          player.onBlockedChange((blocked) => store.setAudioBlocked(blocked));
        }
        void player.init().catch(() => {});
      } catch {}

      const startLegacyAudio = async (): Promise<boolean> => {
        if (liveKitSessionRef.current) return true;
        if (!playerRef.current) {
          const player = new PCMPlayer(16000);
          playerRef.current = player;
          player.onLevel((level) => setOutputLevel(level));
          if (typeof player.onBlockedChange === 'function') {
            player.onBlockedChange((blocked) => store.setAudioBlocked(blocked));
          }
          void player.init().catch(() => {});
        } else if (typeof playerRef.current.resume === 'function') {
          void playerRef.current.resume();
        }
        try {
          const recorder = new AudioRecorder();
          let silenceTimer: ReturnType<typeof setTimeout> | null = null;
          const cleanup = await recorder.startStreaming(
            (pcmChunk) => {
              // This call's own socket only: a late microphone from an earlier
              // call must never stream into the next one.
              if (!isMutedRef.current && isCurrentCall() && socketRef.current === socket) {
                socket.sendAudio(pcmChunk);
                if (pcmChunk.byteLength >= 2) {
                  const int16 = new Int16Array(pcmChunk);
                  let sum = 0;
                  for (let i = 0; i < int16.length; i += 2) sum += int16[i] * int16[i];
                  const rms = Math.sqrt(sum / (int16.length / 2));
                  setInputLevel(rms / 6000);
                  if (rms > 1200) {
                    store.setUserSpeaking(true);
                    if (silenceTimer) clearTimeout(silenceTimer);
                    silenceTimer = setTimeout(() => store.setUserSpeaking(false), 1400);
                  }
                }
              } else if (isMutedRef.current) {
                setInputLevel(0);
              }
            },
            { echoCancellation: true, noiseSuppression: true },
          );
          if (!isCurrentCall()) {
            // The call ended while the permission prompt was open.
            if (silenceTimer) clearTimeout(silenceTimer);
            cleanup();
            return false;
          }
          micFailedRef.current = false;
          micCleanupRef.current = () => {
            if (silenceTimer) clearTimeout(silenceTimer);
            cleanup();
          };
          // Re-resume playback context in case getUserMedia switched OS audio profile
          if (typeof playerRef.current?.resume === 'function') {
            void playerRef.current.resume();
          }
          return true;
        } catch {
          // Refused after the call ended: nothing to say on the ended screen.
          if (!isCurrentCall()) return false;
          // Keep the call active so the caller still hears the AI receptionist
          // and can use the in-call text input ("Type instead") or officer handoff.
          micFailedRef.current = true;
          store.setMuted(true);
          setMicError(t('call.micFallback'));
          return false;
        }
      };
      startLegacyAudioRef.current = startLegacyAudio;

      // Create WebSocket
      const socket = new CallSocket({
        onAudio: (pcmChunk) => {
          playerRef.current?.push(pcmChunk);
        },
        onMessage: (msg) => {
          if (!msg || typeof msg !== 'object') return;

          switch (msg.type) {
            case 'call_ready': {
              // Stop dial tone and close its AudioContext before opening call audio
              if (dialToneCleanupRef.current) {
                dialToneCleanupRef.current();
                dialToneCleanupRef.current = null;
              }
              if (dialToneCtxRef.current && dialToneCtxRef.current.state !== 'closed') {
                dialToneCtxRef.current.close().catch(() => {});
              }
              dialToneCtxRef.current = null;
              statusBeforeReconnectRef.current = 'ai';
              connectedRef.current = true;
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
              {
                const media = msg.media as LiveKitCallCredentials | undefined;
                if (media?.transport === 'livekit') {
                  if (playerRef.current) {
                    playerRef.current.close();
                    playerRef.current = null;
                  }
                  const session: LiveKitCallSession = new LiveKitCallSession({
                    onMessage: (message) => socket.dispatchMessage(message),
                    onReconnecting: () => {
                      const status = useCallStore.getState().status;
                      if (status !== 'reconnecting') statusBeforeReconnectRef.current = status;
                      store.setStatus('reconnecting');
                    },
                    onReconnected: () => {
                      if (useCallStore.getState().status === 'reconnecting') {
                        store.setStatus(statusBeforeReconnectRef.current);
                      }
                    },
                    onDisconnected: () => {
                      if (liveKitSessionRef.current === session) {
                        store.setError(t('call.error.dropped'));
                        hangup();
                      }
                    },
                    onAudioBlockedChange: (blocked) => store.setAudioBlocked(blocked),
                    // The orb and the "you are speaking" state, which the PCM
                    // path computes from raw samples, come from WebRTC here.
                    onLevels: ({ input, output }) => {
                      setInputLevel(input);
                      setOutputLevel(output);
                      if (input > SPEAKING_LEVEL) {
                        store.setUserSpeaking(true);
                        if (speakingTimerRef.current) clearTimeout(speakingTimerRef.current);
                        speakingTimerRef.current = setTimeout(() => {
                          speakingTimerRef.current = null;
                          store.setUserSpeaking(false);
                        }, 1400);
                      }
                    },
                  });
                  liveKitSessionRef.current = session;
                  // Connecting and turning the microphone on fail differently:
                  // no media is the end of the call; a refused microphone is not.
                  void session.connect(media, false).then(
                    () => {
                      if (liveKitSessionRef.current === session) {
                        void applyLiveKitMute(session, isMutedRef.current);
                      }
                    },
                    () => {
                      if (liveKitSessionRef.current !== session) return;
                      store.setError(t('call.error.noConnection'));
                      hangup();
                    },
                  );
                } else {
                  void startLegacyAudio();
                }
              }
              break;
            }
            case 'interrupt': {
              playerRef.current?.flush();
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
                statusBeforeReconnectRef.current = 'transferring';
                store.setStatus('transferring');
                if (msg.ticket_ref) store.setTicketRef(String(msg.ticket_ref));
              } else if (msg.status === 'bridged') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                statusBeforeReconnectRef.current = 'officer';
                store.setStatus('officer');
                if (msg.officer_name) store.setOfficerName(String(msg.officer_name));
                // Play join chime
                const chimeCtx = getToneContext();
                if (chimeCtx) {
                  playJoinChime(chimeCtx);
                }
              } else if (msg.status === 'on_hold') {
                statusBeforeReconnectRef.current = 'on_hold';
                store.setStatus('on_hold');
                if (!holdToneCleanupRef.current) {
                  const holdCtx = getToneContext();
                  if (holdCtx) {
                    holdToneCleanupRef.current = playHoldTone(holdCtx);
                  }
                }
              } else if (msg.status === 'reconnecting') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                const status = useCallStore.getState().status;
                if (status !== 'reconnecting') statusBeforeReconnectRef.current = status;
                store.setStatus('reconnecting');
              } else if (msg.status === 'ai') {
                if (holdToneCleanupRef.current) {
                  holdToneCleanupRef.current();
                  holdToneCleanupRef.current = null;
                }
                statusBeforeReconnectRef.current = 'ai';
                store.setStatus('ai');
              } else if (msg.status === 'ended') {
                endedByServerRef.current = true;
                hangup();
              }
              break;
            }
            case 'queue': {
              const position = msg.position;
              store.setQueuePosition(typeof position === 'number' && position > 0 ? position : null);
              break;
            }
            case 'call_state': {
              const rawPhase = msg.phase;
              const turnPhase: CallTurnPhase | undefined =
                rawPhase === 'thinking' || rawPhase === 'speaking' || rawPhase === 'listening'
                  ? rawPhase
                  : undefined;
              store.setCallRuntimeState({
                turnPhase,
                pendingOffer: typeof msg.pending_offer === 'string' ? msg.pending_offer : undefined,
                speechRateSlow: typeof msg.speech_rate_slow === 'boolean' ? msg.speech_rate_slow : undefined,
                hasMoreDetail: typeof msg.has_more_detail === 'boolean' ? msg.has_more_detail : undefined,
                hasLastAnswer: typeof msg.has_last_answer === 'boolean' ? msg.has_last_answer : undefined,
              });
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
              endedByServerRef.current = true;
              hangup();
              break;
            }
            case 'error': {
              store.setError(t(errorKeyForCode(msg.code)));
              break;
            }
            case 'duration_warning': {
              // A minute before the call's time limit (G602).
              store.addCaption({ speaker: 'system', text: t('call.durationWarning'), final: true });
              break;
            }
          }
        },
        onError: () => {
          // The close that follows says why; this only covers a socket that
          // could not even be created.
          if (!useCallStore.getState().error) {
            store.setError(t(connectedRef.current ? 'call.error.dropped' : 'call.error.noConnection'));
          }
        },
        onClose: (code) => {
          cleanupAudio();
          store.setStatus('ended');
          if (endedByServerRef.current || (code === 1000 && connectedRef.current)) return;
          store.setError(t(errorKeyForClose(code, connectedRef.current)));
        },
      });

      socketRef.current = socket;
      // `locale` stays the chat's language: a single-engine call is held in
      // it. A multilingual call opens in English whatever it says and treats
      // `preferred_locale` as a hint.
      // The chat this call was started from: the server carries its task into
      // the call when this browser holds the chat (G122).
      const parentConversationId = useChatStore.getState().activeConversationId;
      socket.connect({
        locale,
        preferred_locale: locale,
        voice_consent_accepted: true,
        sample_rate: 16000,
        ...(parentConversationId
          ? { parent_conversation_id: parentConversationId, chat_session: getChatSessionId() }
          : {}),
      });
    },
    [applyLiveKitMute, cleanupAudio, getToneContext, hangup, setMicError, store, t],
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
    requestCallback,
    queuePosition: store.queuePosition,
    turnPhase: store.turnPhase,
    pendingOffer: store.pendingOffer,
    speechRateSlow: store.speechRateSlow,
    hasMoreDetail: store.hasMoreDetail,
    hasLastAnswer: store.hasLastAnswer,
    toggleMute,
    setLanguage,
    sendControl,
    sendTextTurn,
    interruptCall,
    audioBlocked: store.audioBlocked,
    resumeAudio,
  };
}
