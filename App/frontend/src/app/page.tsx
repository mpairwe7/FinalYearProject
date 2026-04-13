"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore, ChatTurn, createTurn } from '../store/useChatStore';
import {
  initAnalytics,
  getAnalyticsSessionId,
  trackChatSent,
  trackChatReceived,
  trackVoiceUsed,
  trackStarterPromptUsed,
  trackErrorOccurred,
} from '../store/useAnalyticsStore';
import { useSpeechHealth, useTtsMutation } from '../hooks/useSpeech';
import {
  AudioRecorder,
  closePlaybackContext,
  playAudioBase64,
  stopPlayback,
  isPlaying,
  voiceChat,
} from '../services/voiceService';
import ChatMessage from '../components/ChatMessage';
import ChatInput from '../components/ChatInput';
import StarterPrompts from '../components/StarterPrompts';
import { SparklesIcon, HeadphonesIcon, MicIcon, BotIcon, LoadingDots } from '../components/Icons';

// ---------------------------------------------------------------------------
// Browser Speech Recognition types
// ---------------------------------------------------------------------------
interface SpeechRecognition extends EventTarget {
  lang: string; continuous: boolean; interimResults: boolean;
  onstart: (() => void) | null; onerror: ((e: Event) => void) | null;
  onend: (() => void) | null;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  start: () => void; stop: () => void; abort: () => void;
}
interface SpeechRecognitionEvent extends Event {
  results: { [i: number]: { 0: { transcript: string }; isFinal: boolean; length: number }; length: number };
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const LOCALE_OPTIONS = [
  { value: 'en', label: 'English', speechLang: 'en-US' },
  { value: 'lg', label: 'Luganda', speechLang: 'lg-UG' },
] as const;

// Stable check — only computed once
const HAS_MEDIA_RECORDER = typeof window !== 'undefined' && AudioRecorder.isSupported();

function findPrecedingUserQuery(chat: ChatTurn[], i: number): string {
  for (let j = i - 1; j >= 0; j--) if (chat[j].role === 'user') return chat[j].content;
  return '';
}

// ==========================================================================
// Page
// ==========================================================================
export default function Page() {
  // Zustand — granular selectors prevent unnecessary re-renders
  const message = useChatStore((s) => s.message);
  const setMessage = useChatStore((s) => s.setMessage);
  const chat = useChatStore((s) => s.chat);
  const speechState = useChatStore((s) => s.speechState);
  const setSpeechState = useChatStore((s) => s.setSpeechState);
  const locale = useChatStore((s) => s.locale);
  const setLocale = useChatStore((s) => s.setLocale);
  const addTurns = useChatStore((s) => s.addTurns);
  const updateLastTurn = useChatStore((s) => s.updateLastTurn);

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const lastUserQueryRef = useRef<string>('');

  // Voice state
  const [autoNarrate, setAutoNarrate] = useState(false);
  const [playingTurnId, setPlayingTurnId] = useState<string | null>(null);
  const [ttsLoading, setTtsLoading] = useState<string | null>(null);
  const [voiceMode, setVoiceMode] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const recorderRef = useRef<AudioRecorder | null>(null);

  // TanStack Query — cached speech health (auto-refreshes every 60s)
  const { data: speechHealth } = useSpeechHealth();
  const ttsMutation = useTtsMutation();

  // ---- Lifecycle ----

  useEffect(() => { initAnalytics(); }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (recorderRef.current?.isRecording) {
        recorderRef.current.cancel();
        recorderRef.current = null;
      }
      stopPlayback();
      closePlaybackContext();
    };
  }, []);

  // Browser Speech Recognition (re-created on locale change)
  useEffect(() => {
    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }
    const win = typeof window !== 'undefined' ? window as Window & { SpeechRecognition?: new () => SpeechRecognition; webkitSpeechRecognition?: new () => SpeechRecognition } : null;
    const Impl = win && (win.SpeechRecognition || win.webkitSpeechRecognition);
    if (!Impl) {
      if (!HAS_MEDIA_RECORDER) setSpeechState('unavailable');
      return;
    }
    const recog: SpeechRecognition = new Impl();
    recog.lang = LOCALE_OPTIONS.find((l) => l.value === locale)?.speechLang ?? 'en-US';
    recog.continuous = false;
    recog.interimResults = false;
    recog.onstart = () => setSpeechState('listening');
    recog.onerror = () => setSpeechState('error');
    recog.onend = () => setSpeechState('idle');
    recog.onresult = (event) => {
      const t = event.results?.[0]?.[0]?.transcript;
      if (t) setMessage(t);
    };
    recognitionRef.current = recog;
    return () => { recog.abort(); };
  }, [locale, setSpeechState, setMessage]);

  // Auto-narrate new assistant messages
  const lastChatLength = useRef(chat.length);
  const handleListenToReply = useCallback(async (turnId: string, text: string) => {
    if (playingTurnId === turnId) {
      stopPlayback();
      setPlayingTurnId(null);
      return;
    }
    stopPlayback();
    setTtsLoading(turnId);
    try {
      const result = await ttsMutation.mutateAsync({ text, language: locale });
      setTtsLoading(null);
      if (result.error || !result.audio_base64) return;
      setPlayingTurnId(turnId);
      await playAudioBase64(result.audio_base64);
    } catch {
      // TTS unavailable — degrade silently
    } finally {
      setTtsLoading(null);
      setPlayingTurnId((prev) => (prev === turnId ? null : prev));
    }
  }, [playingTurnId, locale, ttsMutation]);

  useEffect(() => {
    if (!autoNarrate || chat.length <= lastChatLength.current) {
      lastChatLength.current = chat.length;
      return;
    }
    lastChatLength.current = chat.length;
    const last = chat[chat.length - 1];
    if (last?.role === 'assistant' && last.content && last.id !== 'greeting-0' && !isLoading && !isPlaying()) {
      handleListenToReply(last.id, last.content);
    }
  }, [chat, autoNarrate, isLoading, handleListenToReply]);

  // ---- Text chat (SSE) ----

  const sendMessage = useCallback(async () => {
    const text = message.trim();
    if (!text || isLoading) return;
    addTurns([createTurn('user', text)]);
    setMessage('');
    setIsLoading(true);
    lastUserQueryRef.current = text;
    trackChatSent(text.length);
    const t0 = Date.now();
    const ac = new AbortController();
    const timeout = setTimeout(() => ac.abort(), 60_000);

    try {
      const res = await fetch(`${API_URL}/v1/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Session-ID': getAnalyticsSessionId() },
        body: JSON.stringify({ message: text, top_k: 4, locale }),
        signal: ac.signal,
      });
      if (!res.ok) {
        const sync = await fetch(`${API_URL}/v1/chat`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-ID': getAnalyticsSessionId() },
          body: JSON.stringify({ message: text, top_k: 4, locale }),
          signal: ac.signal,
        });
        if (!sync.ok) throw new Error(`API ${sync.status}`);
        const d = await sync.json();
        addTurns([createTurn('assistant', d.reply, {
          citations: d.citations ?? [], faithfulnessScore: d.faithfulness_score ?? null,
          retrievalMode: d.retrieval_mode ?? 'keyword',
          escalationRequired: d.escalation_required ?? false, escalationReason: d.escalation_reason ?? '',
        })]);
        trackChatReceived(Date.now() - t0, (d.sources?.length ?? 0) > 0);
        return;
      }
      addTurns([createTurn('assistant', '', {})]);
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No body');
      const dec = new TextDecoder();
      let buf = '', streamed = '', meta: Record<string, unknown> = {}, evt = 'token';
      let raf: number | null = null;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() || '';
          for (const ln of lines) {
            if (ln.startsWith('event: ')) { evt = ln.slice(7).trim(); continue; }
            if (!ln.startsWith('data: ')) continue;
            const data = ln.slice(6);
            if (evt === 'error') { updateLastTurn((t) => ({ ...t, content: 'Sorry, an error occurred. Please try again.' })); evt = 'token'; continue; }
            if (evt === 'done') { evt = 'token'; continue; }
            if (evt === 'metadata' || evt === 'grounding') {
              try { const p = JSON.parse(data); meta = { ...meta, ...p }; updateLastTurn((t) => ({ ...t, citations: p.citations ?? t.citations, faithfulnessScore: p.faithfulness_score ?? t.faithfulnessScore, retrievalMode: p.retrieval_mode ?? t.retrievalMode, escalationRequired: p.escalation_required ?? t.escalationRequired, escalationReason: p.escalation_reason ?? t.escalationReason })); } catch {}
              evt = 'token'; continue;
            }
            if (data) {
              streamed += data;
              if (raf === null) { const c = streamed; raf = requestAnimationFrame(() => { updateLastTurn((t) => ({ ...t, content: c })); raf = null; }); }
            }
            evt = 'token';
          }
        }
        dec.decode();
        if (raf !== null) cancelAnimationFrame(raf);
        updateLastTurn((t) => ({ ...t, content: streamed }));
      } finally { reader.releaseLock(); }
      trackChatReceived(Date.now() - t0, (Array.isArray(meta.sources) && meta.sources.length > 0));
    } catch {
      const cur = useChatStore.getState().chat;
      const last = cur[cur.length - 1];
      if (last?.role === 'assistant' && last?.content === '') {
        updateLastTurn((t) => ({ ...t, content: 'Sorry, I could not reach the URA knowledge base. Please try again shortly.' }));
      } else {
        addTurns([createTurn('assistant', 'Sorry, I could not reach the URA knowledge base. Please try again shortly.')]);
      }
      trackErrorOccurred('chat_fetch_failed');
    } finally {
      clearTimeout(timeout);
      setIsLoading(false);
    }
  }, [message, isLoading, locale, addTurns, setMessage, updateLastTurn]);

  // ---- Voice input ----

  const handleMicClick = useCallback(async () => {
    if (isTransitioning) return;
    if (voiceMode && HAS_MEDIA_RECORDER) {
      setIsTransitioning(true);
      try {
        if (isRecording) {
          // Stop recording
          const rec = recorderRef.current;
          if (!rec) { setIsTransitioning(false); return; }
          recorderRef.current = null;
          setIsRecording(false);
          setSpeechState('idle');
          const pcm16 = await rec.stop();
          if (pcm16.byteLength === 0) return;
          setIsLoading(true);
          const t0 = Date.now();
          trackChatSent(0);
          try {
            const r = await voiceChat(pcm16, { language: locale, ttsEnabled: autoNarrate, sessionId: getAnalyticsSessionId() });
            if (r.error && !r.transcript) { addTurns([createTurn('assistant', `Voice error: ${r.error}`)]); trackErrorOccurred('voice_chat_failed'); return; }
            if (r.transcript) { addTurns([createTurn('user', r.transcript)]); lastUserQueryRef.current = r.transcript; }
            if (r.reply) {
              addTurns([createTurn('assistant', r.reply, { citations: r.citations ?? [], faithfulnessScore: r.faithfulness_score ?? null, retrievalMode: r.retrieval_mode ?? 'keyword' })]);
              trackChatReceived(Date.now() - t0, (r.sources?.length ?? 0) > 0);
              if (r.reply_audio_base64) {
                const tid = useChatStore.getState().chat[useChatStore.getState().chat.length - 1]?.id;
                if (tid) { setPlayingTurnId(tid); try { await playAudioBase64(r.reply_audio_base64); } finally { setPlayingTurnId((p) => p === tid ? null : p); } }
              }
            }
          } catch { addTurns([createTurn('assistant', 'Sorry, I could not process your voice. Please try again or type.')]); trackErrorOccurred('voice_recording_failed'); } finally { setIsLoading(false); }
        } else {
          // Start recording
          try {
            const rec = new AudioRecorder();
            recorderRef.current = rec;
            await rec.start();
            setIsRecording(true);
            setSpeechState('listening');
            trackVoiceUsed();
          } catch { setSpeechState('error'); }
        }
      } finally { setIsTransitioning(false); }
      return;
    }
    // Browser Speech API
    if (!recognitionRef.current) return;
    if (speechState === 'listening') { recognitionRef.current.stop(); return; }
    trackVoiceUsed();
    recognitionRef.current.start();
  }, [isTransitioning, voiceMode, isRecording, locale, autoNarrate, speechState, addTurns, setSpeechState]);

  const handleStarterPrompt = useCallback((prompt: string) => {
    setMessage(prompt);
    trackStarterPromptUsed(prompt);
  }, [setMessage]);

  // ---- Derived state ----

  const speechStatusLabel = useMemo(() => {
    if (isRecording) return 'Recording...';
    if (speechState === 'listening') return 'Listening...';
    if (speechState === 'unavailable') return 'Speech unavailable';
    if (speechState === 'error') return 'Speech error';
    return voiceMode ? 'Tap mic to record' : 'Tap mic to speak';
  }, [speechState, voiceMode, isRecording]);

  const healthLabel = useMemo(() => {
    if (!speechHealth) return 'Checking...';
    return speechHealth.status === 'ready' ? 'Voice ready' : 'Voice unavailable';
  }, [speechHealth]);

  const serverReady = speechHealth?.status === 'ready';

  // Memoize user query lookup per turn for ChatMessage
  const userQueries = useMemo(() => {
    const map: Record<string, string> = {};
    for (let i = 0; i < chat.length; i++) {
      if (chat[i].role === 'assistant') {
        map[chat[i].id] = findPrecedingUserQuery(chat, i) || lastUserQueryRef.current;
      }
    }
    return map;
  }, [chat]);

  // ---- Render ----

  return (
    <main>
      <section className="hero">
        <div>
          <div className="badge"><SparklesIcon /> Live assistant</div>
          <h1 className="hero-title">URA Chatbot</h1>
          <p className="hero-sub">
            Natural chat with speech and text. Ask about URA services, tax
            policy, or process workflows — every answer is grounded in the
            URA knowledge base with live citations.
          </p>
          <div role="radiogroup" aria-label="Language selection" className="locale-switch">
            {LOCALE_OPTIONS.map((opt) => (
              <button key={opt.value} role="radio" aria-checked={locale === opt.value}
                onClick={() => setLocale(opt.value)} className="locale-btn">{opt.label}</button>
            ))}
          </div>
        </div>
        <div className="hero-controls">
          <div className={`pill ${speechHealth?.status === 'ready' ? 'pill-ok' : 'pill-warn'}`} aria-live="polite">
            <HeadphonesIcon /> {healthLabel}
          </div>
          <label className="voice-toggle" title="Voice mode uses server-side ASR + TTS for full bilingual speech">
            <input type="checkbox" checked={voiceMode} onChange={(e) => setVoiceMode(e.target.checked)} disabled={!serverReady && !HAS_MEDIA_RECORDER} />
            <span className="voice-toggle-label">Voice mode</span>
          </label>
          <label className="voice-toggle" title="Automatically read assistant replies aloud">
            <input type="checkbox" checked={autoNarrate} onChange={(e) => setAutoNarrate(e.target.checked)} />
            <span className="voice-toggle-label">Auto-narrate</span>
          </label>
        </div>
      </section>

      <div className="grid grid-2">
        <section className="card chat-shell" aria-label="Chat conversation">
          <header className="section-title">
            <div>
              <h2>Conversation</h2>
              <div className="small">Context-aware, grounded responses{voiceMode && ' + voice'}</div>
            </div>
            <div className={`status ${isRecording ? 'status-recording' : ''}`}>
              <MicIcon /> {speechStatusLabel}
            </div>
          </header>

          <div className="message-list" aria-live="polite">
            {chat.map((turn) => (
              <ChatMessage
                key={turn.id}
                turn={turn}
                userQuery={userQueries[turn.id] || ''}
                locale={locale}
                playingTurnId={playingTurnId}
                ttsLoading={ttsLoading}
                isTransitioning={isTransitioning}
                onListen={handleListenToReply}
              />
            ))}
            {isLoading && (() => {
              const last = chat[chat.length - 1];
              if (last?.role === 'assistant' && last.content !== '') return null;
              return (
                <article className="message-row">
                  <div className="avatar assistant" aria-hidden="true"><BotIcon /></div>
                  <div className="bubble assistant"><span className="bubble-role">assistant</span><LoadingDots /></div>
                </article>
              );
            })()}
            <div ref={messagesEndRef} />
          </div>

          <ChatInput
            message={message}
            isLoading={isLoading}
            isRecording={isRecording}
            isTransitioning={isTransitioning}
            speechUnavailable={speechState === 'unavailable' && !HAS_MEDIA_RECORDER}
            speechState={speechState}
            voiceMode={voiceMode}
            onMessageChange={setMessage}
            onSend={sendMessage}
            onMicClick={handleMicClick}
          />
        </section>

        <StarterPrompts onSelect={handleStarterPrompt} />
      </div>
    </main>
  );
}
