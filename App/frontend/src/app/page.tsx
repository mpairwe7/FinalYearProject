"use client";

import Image from 'next/image';
import Link from 'next/link';
import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useChatStore, ChatTurn, createTurn, cleanResponse } from '../store/useChatStore';
import { useVoiceStore } from '../store/useVoiceStore';
import { LOCALE_OPTIONS } from '../lib/locales';
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
  transcribe,
  voiceChat,
} from '../services/voiceService';
import { authHeaders } from '../lib/authSession';
import {
  MAX_ATTACHMENTS,
  MAX_ATTACHMENT_BYTES,
  PendingAttachment,
} from '../lib/attachments';
import ChatMessage from '../components/ChatMessage';
import ChatInput from '../components/ChatInput';
import ConfirmDialog, { ConfirmRequest } from '../components/ConfirmDialog';
import ConversationRail from '../components/ConversationRail';
import SettingsDialog, { SettingsTab } from '../components/settings/SettingsDialog';
import ChatHeader from '../components/ChatHeader';
import { BotIcon } from '../components/Icons';
import { useIdentity } from '../hooks/useIdentity';

// ---------------------------------------------------------------------------
// Browser Speech Recognition types
// ---------------------------------------------------------------------------
interface SpeechRecognition extends EventTarget {
  lang: string; continuous: boolean; interimResults: boolean;
  onstart: (() => void) | null; onerror: ((e: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  start: () => void; stop: () => void; abort: () => void;
}
interface SpeechRecognitionEvent extends Event {
  /** Index of the first result changed by this event — everything before it is
   *  already accounted for, so only the tail is re-read. */
  resultIndex: number;
  results: { [i: number]: { 0: { transcript: string }; isFinal: boolean; length: number }; length: number };
}
interface SpeechRecognitionErrorEvent extends Event {
  error: string;
}

/** Join what was already in the composer with what has been dictated. */
function joinDictated(base: string, spoken: string): string {
  const b = base.trimEnd();
  const s = spoken.trimStart();
  if (!b) return s;
  if (!s) return b;
  return `${b} ${s}`;
}

// All API calls go through the Next.js rewrite proxy at /api/*
// so the browser stays same-origin (no CORS, CSP-safe).
const API_URL = '/api';

// Project blog (separate Vercel deployment). Set NEXT_PUBLIC_BLOG_URL in the
// frontend's Vercel project to the blog's real production URL; the value below
// is a placeholder fallback used until that env var is configured.
const BLOG_URL = process.env.NEXT_PUBLIC_BLOG_URL || 'https://blog-two-mu-45.vercel.app';

const STARTER_PROMPTS = [
  { label: 'What services does URA provide?', category: 'Getting started' },
  { label: 'How do I register for a TIN?', category: 'Registration' },
  { label: 'What is the current VAT rate in Uganda?', category: 'Rates' },
  { label: 'How do I file my annual tax returns?', category: 'Filing' },
] as const;

const getMediaRecorderSupportSnapshot = () => AudioRecorder.isSupported();
const getServerMediaRecorderSupportSnapshot = () => false;

function subscribeMediaRecorderSupport(onStoreChange: () => void) {
  if (typeof window === 'undefined') return () => {};
  const id = window.setTimeout(onStoreChange, 0);
  return () => window.clearTimeout(id);
}

function useMediaRecorderSupport() {
  return useSyncExternalStore(
    subscribeMediaRecorderSupport,
    getMediaRecorderSupportSnapshot,
    getServerMediaRecorderSupportSnapshot,
  );
}

function findPrecedingUserQuery(chat: ChatTurn[], i: number): string {
  for (let j = i - 1; j >= 0; j--) if (chat[j].role === 'user') return chat[j].content;
  return '';
}

// ==========================================================================
// Page
// ==========================================================================
export default function Page() {
  const hasMediaRecorder = useMediaRecorderSupport();

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
  const reset = useChatStore((s) => s.reset);
  // Session management
  const conversations = useChatStore((s) => s.conversations);
  const activeConversationId = useChatStore((s) => s.activeConversationId);
  const createNewSession = useChatStore((s) => s.createNewSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);
  const ensureActiveConversationId = useChatStore((s) => s.ensureActiveConversationId);
  const saveCurrentSession = useChatStore((s) => s.saveCurrentSession);

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  // Live-dictation bookkeeping — see the recognition effect below.
  const dictationBaseRef = useRef('');
  const dictationFinalRef = useRef('');
  const dictationActiveRef = useRef(false);
  // Restart-loop guard. `onend` restarting the recognizer is what keeps the mic
  // alive across a pause, but if the engine ends immediately every time — no
  // mic permission, device pulled, tab backgrounded — that same line is an
  // unthrottled loop hammering start() forever. Count only the ends that come
  // back too fast to be a real utterance; any session that lasted resets it.
  const dictationStartedAtRef = useRef(0);
  const dictationRapidEndsRef = useRef(0);
  // One line about the last dictation attempt, shown under the composer.
  const [dictationNotice, setDictationNotice] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
  const chatDockRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);
  const lastUserQueryRef = useRef<string>('');
  const [showScrollToLatest, setShowScrollToLatest] = useState(false);

  // Sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // chatv2 presentation state: destructive-action confirm + desktop rail collapse
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const [railCollapsed, setRailCollapsed] = useState(false);

  // Settings dialog. `settingsTab` lets a caller open straight onto a section —
  // the landing page's "Account & settings" link opens Account; every other
  // entry point (sidebar, header menu) opens General.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('general');
  const openSettings = useCallback((tab: SettingsTab = 'general') => {
    setSettingsTab(tab);
    setSettingsOpen(true);
  }, []);
  // Stable identity: the dialog's focus handling keys off its props, and a new
  // closure on every render would make it re-run.
  const closeSettings = useCallback(() => setSettingsOpen(false), []);

  // Signed-in state drives the landing call to action; the token is verified by
  // the backend, so this is not "someone has a token in localStorage".
  const { status: identityStatus, name: identityName } = useIdentity();

  // Document attachments awaiting the next chat turn
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);

  // Voice state
  const [autoNarrate, setAutoNarrate] = useState(false);
  const [playingTurnId, setPlayingTurnId] = useState<string | null>(null);
  const [ttsLoading, setTtsLoading] = useState<string | null>(null);
  const [voiceMode, setVoiceMode] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const recorderRef = useRef<AudioRecorder | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const userStoppedRef = useRef(false);

  // TanStack Query — cached speech health (auto-refreshes every 60s)
  const { data: speechHealth } = useSpeechHealth();
  const ttsMutation = useTtsMutation();
  const hasStartedChat = chat.length > 1;

  // ---- Lifecycle ----

  useEffect(() => { initAnalytics(); }, []);

  const updateScrollAffordance = useCallback(() => {
    const list = messageListRef.current;
    if (!list) {
      shouldStickToBottomRef.current = true;
      setShowScrollToLatest(false);
      return;
    }
    const distanceFromBottom = list.scrollHeight - list.scrollTop - list.clientHeight;
    const isNearBottom = distanceFromBottom < 140;
    shouldStickToBottomRef.current = isNearBottom;
    setShowScrollToLatest((prev) => {
      const next = distanceFromBottom > 220;
      return prev === next ? prev : next;
    });
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const list = messageListRef.current;
    if (!list) {
      messagesEndRef.current?.scrollIntoView({ behavior, block: 'end' });
      return;
    }
    window.requestAnimationFrame(() => {
      list.scrollTo({ top: list.scrollHeight, behavior });
      shouldStickToBottomRef.current = true;
      setShowScrollToLatest(false);
    });
  }, []);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) return;
    updateScrollAffordance();
    list.addEventListener('scroll', updateScrollAffordance, { passive: true });
    return () => list.removeEventListener('scroll', updateScrollAffordance);
  }, [hasStartedChat, updateScrollAffordance]);

  useEffect(() => {
    // Keep the document language honest for screen readers and hyphenation
    // as the user switches locales — every LOCALE_OPTIONS code is a valid
    // BCP-47 primary subtag on its own (nyn/ach have no 2-letter form).
    document.documentElement.lang = locale || 'en';
  }, [locale]);

  useEffect(() => {
    const root = document.documentElement;
    let raf = 0;

    const updateMobileViewportVars = () => {
      if (raf) window.cancelAnimationFrame(raf);
      raf = window.requestAnimationFrame(() => {
        const dockHeight = chatDockRef.current?.getBoundingClientRect().height || 88;
        const viewport = window.visualViewport;
        const keyboardInset = viewport
          ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)
          : 0;

        root.style.setProperty('--chat-dock-height', `${Math.ceil(dockHeight)}px`);
        root.style.setProperty('--keyboard-inset', `${Math.round(keyboardInset)}px`);
        root.style.setProperty('--app-viewport-height', `${Math.round(viewport?.height ?? window.innerHeight)}px`);
        raf = 0;
      });
    };

    updateMobileViewportVars();

    const resizeObserver = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(updateMobileViewportVars)
      : null;
    if (chatDockRef.current) resizeObserver?.observe(chatDockRef.current);

    window.addEventListener('resize', updateMobileViewportVars);
    window.visualViewport?.addEventListener('resize', updateMobileViewportVars);
    window.visualViewport?.addEventListener('scroll', updateMobileViewportVars);

    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      resizeObserver?.disconnect();
      window.removeEventListener('resize', updateMobileViewportVars);
      window.visualViewport?.removeEventListener('resize', updateMobileViewportVars);
      window.visualViewport?.removeEventListener('scroll', updateMobileViewportVars);
    };
  }, [hasStartedChat, isLoading, isRecording]);

  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSidebarOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [sidebarOpen]);

  useEffect(() => {
    const EDGE = 28;
    const THRESHOLD = 56;
    let startX = 0;
    let startY = 0;
    let tracking = false;

    const onStart = (event: TouchEvent) => {
      if (event.touches.length !== 1) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest('textarea, input, [contenteditable="true"]')) return;
      const x = event.touches[0].clientX;
      const y = event.touches[0].clientY;
      if (sidebarOpen || x <= EDGE) {
        tracking = true;
        startX = x;
        startY = y;
      }
    };
    const onMove = (event: TouchEvent) => {
      if (!tracking) return;
      const dx = event.touches[0].clientX - startX;
      const dy = event.touches[0].clientY - startY;
      if (Math.abs(dy) > 48 && Math.abs(dy) > Math.abs(dx)) {
        tracking = false;
        return;
      }
      if (!sidebarOpen && dx > THRESHOLD) {
        setSidebarOpen(true);
        tracking = false;
      } else if (sidebarOpen && dx < -THRESHOLD) {
        setSidebarOpen(false);
        tracking = false;
      }
    };
    const onEnd = () => {
      tracking = false;
    };

    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: true });
    window.addEventListener('touchend', onEnd);
    return () => {
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onEnd);
    };
  }, [sidebarOpen]);

  useEffect(() => {
    if (!hasStartedChat) return;
    const last = chat[chat.length - 1];
    if (shouldStickToBottomRef.current || last?.role === 'user' || isLoading) {
      scrollToBottom(last?.role === 'user' ? 'smooth' : 'auto');
    }
  }, [chat, hasStartedChat, isLoading, scrollToBottom]);

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

  /**
   * Browser Speech Recognition — live transcription, re-created on locale change.
   *
   * `interimResults` is the whole point: with it off (as it was) the composer
   * stayed empty for the length of an utterance and then filled in one jump, so
   * there was no way to tell dictation was working, or that it had misheard the
   * first sentence, until it was over. With it on, words land as they are
   * spoken and a mistake is visible while it is still worth restarting.
   *
   * `continuous` goes with it. Non-continuous recognition ends at the first
   * pause, which turns a two-sentence question into one sentence and a stopped
   * mic. The engines still end sessions on their own — Chrome does it after a
   * few seconds of silence regardless — so `onend` restarts while the user has
   * not tapped stop. That is what makes the mic stay live rather than dying
   * mid-thought.
   *
   * Three refs, because a re-render must not disturb an in-flight utterance:
   *   base   — what was in the composer when dictation started, so dictating
   *            into a half-typed question appends instead of erasing it
   *   final  — segments the engine has committed this session
   *   active — the user's intent, which is what decides whether `onend` is a
   *            silence timeout to recover from or a real stop
   */
  useEffect(() => {
    if (recognitionRef.current) {
      dictationActiveRef.current = false;
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }
    const win = typeof window !== 'undefined' ? window as Window & { SpeechRecognition?: new () => SpeechRecognition; webkitSpeechRecognition?: new () => SpeechRecognition } : null;
    const Impl = win && (win.SpeechRecognition || win.webkitSpeechRecognition);
    if (!Impl) {
      if (!hasMediaRecorder) setSpeechState('unavailable');
      return;
    }
    const recog: SpeechRecognition = new Impl();
    recog.lang = LOCALE_OPTIONS.find((l) => l.value === locale)?.speechLang ?? 'en-US';
    recog.continuous = true;
    recog.interimResults = true;
    recog.onstart = () => {
      dictationStartedAtRef.current = Date.now();
      setSpeechState('listening');
    };
    recog.onerror = (event) => {
      // "no-speech" is a pause, not a failure: the engine gives up on silence
      // and onend restarts it. Treating it as an error put the mic in a red
      // state for thinking. "aborted" is our own stop() or teardown.
      if (event.error === 'no-speech' || event.error === 'aborted') return;
      dictationActiveRef.current = false;
      setSpeechState('error');
    };
    recog.onend = () => {
      if (dictationActiveRef.current) {
        // A session that ran for a while and then ended is a silence timeout —
        // pick it straight back up. One that ends within a few hundred ms never
        // really started, so count it; three in a row means the engine cannot
        // run here and retrying is just a spin.
        const ranFor = Date.now() - dictationStartedAtRef.current;
        dictationRapidEndsRef.current = ranFor < 300 ? dictationRapidEndsRef.current + 1 : 0;
        if (dictationRapidEndsRef.current < 3) {
          try {
            recog.start();
            return;
          } catch {
            // start() throws if the previous session is still tearing down.
            dictationActiveRef.current = false;
          }
        } else {
          dictationActiveRef.current = false;
          dictationRapidEndsRef.current = 0;
          setSpeechState('error');
          return;
        }
      }
      dictationRapidEndsRef.current = 0;
      setSpeechState('idle');
    };
    recog.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const segment = event.results[i];
        const text = segment?.[0]?.transcript ?? '';
        if (segment?.isFinal) dictationFinalRef.current += text;
        else interim += text;
      }
      // Interim text is rendered too, then replaced as the engine commits it.
      setMessage(joinDictated(dictationBaseRef.current, dictationFinalRef.current + interim));
    };
    recognitionRef.current = recog;
    return () => {
      dictationActiveRef.current = false;
      recog.abort();
    };
  }, [locale, hasMediaRecorder, setSpeechState, setMessage]);

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
      const result = await ttsMutation.mutateAsync({
        text,
        language: locale,
        voice: useVoiceStore.getState().voiceByLocale[locale] || undefined,
      });
      setTtsLoading(null);
      if (result.error || !result.audio_base64) {
        if (result.error) trackErrorOccurred('tts_failed');
        return;
      }
      setPlayingTurnId(turnId);
      await playAudioBase64(result.audio_base64);
    } catch {
      // TTS unavailable — degrade to text, but record it
      trackErrorOccurred('tts_failed');
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

  // ---- Document attachments ----

  const uploadAttachment = useCallback(async (file: File, clientId: string) => {
    try {
      const form = new FormData();
      form.append('file', file);
      // No Content-Type header — the browser sets the multipart boundary.
      const res = await fetch(`${API_URL}/v1/documents/analyze`, {
        method: 'POST',
        headers: authHeaders({ 'X-Session-ID': getAnalyticsSessionId() }),
        body: form,
      });
      if (!res.ok) {
        let detail = `Upload failed (${res.status})`;
        try {
          const body = await res.json();
          if (typeof body.detail === 'string') detail = body.detail;
        } catch { /* non-JSON error body */ }
        throw new Error(detail);
      }
      const analysis = await res.json();
      setPendingAttachments((prev) => prev.map((a) => (
        a.clientId === clientId
          ? { ...a, status: 'ready', documentId: analysis.document_id, docType: analysis.doc_type }
          : a
      )));
    } catch (err) {
      trackErrorOccurred('document_upload_failed');
      setPendingAttachments((prev) => prev.map((a) => (
        a.clientId === clientId
          ? { ...a, status: 'error', error: err instanceof Error ? err.message : 'Upload failed' }
          : a
      )));
    }
  }, []);

  const attachFiles = useCallback((files: FileList) => {
    const room = MAX_ATTACHMENTS - pendingAttachments.length;
    if (room <= 0) return;
    const chips: PendingAttachment[] = [];
    for (const file of Array.from(files).slice(0, room)) {
      const clientId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
      const oversize = file.size > MAX_ATTACHMENT_BYTES;
      chips.push({
        clientId,
        name: file.name,
        sizeBytes: file.size,
        status: oversize ? 'error' : 'uploading',
        error: oversize ? 'Over the 10 MB limit' : undefined,
      });
      if (!oversize) void uploadAttachment(file, clientId);
    }
    if (chips.length) setPendingAttachments((prev) => [...prev, ...chips]);
  }, [pendingAttachments.length, uploadAttachment]);

  const removeAttachment = useCallback((clientId: string) => {
    setPendingAttachments((prev) => prev.filter((a) => a.clientId !== clientId));
  }, []);

  // ---- Text chat (SSE) ----

  const sendMessage = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? message).trim();
    if (!text || isLoading) return;
    // Enter can fire while an upload is analysing — wait for it to settle.
    if (pendingAttachments.some((a) => a.status === 'uploading')) return;
    // Sending ends the dictation session. Without this the recognizer keeps
    // running against a composer that has just been cleared, and its next
    // result — interim or final — refills the box with the question that was
    // already sent, on top of whatever the person started typing next.
    if (dictationActiveRef.current) {
      dictationActiveRef.current = false;
      dictationBaseRef.current = '';
      dictationFinalRef.current = '';
      recognitionRef.current?.stop();
    }
    const sentAttachments = pendingAttachments
      .filter((a) => a.status === 'ready' && a.documentId)
      .map((a) => ({ id: a.documentId as string, name: a.name, docType: a.docType }));
    const conversationId = activeConversationId ?? ensureActiveConversationId();
    shouldStickToBottomRef.current = true;
    setShowScrollToLatest(false);
    addTurns([
      createTurn('user', text, sentAttachments.length ? { attachments: sentAttachments } : undefined),
    ]);
    setMessage('');
    setPendingAttachments([]);
    setIsLoading(true);
    lastUserQueryRef.current = text;
    trackChatSent(text.length);
    const t0 = Date.now();
    const ac = new AbortController();
    streamAbortRef.current = ac;
    userStoppedRef.current = false;
    const timeout = setTimeout(() => ac.abort(), 120_000);
    const requestBody = JSON.stringify({
      message: text,
      conversation_id: conversationId,
      top_k: 4,
      locale,
      ...(sentAttachments.length ? { attachment_ids: sentAttachments.map((a) => a.id) } : {}),
    });
    const requestHeaders = authHeaders({
      'Content-Type': 'application/json',
      'X-Session-ID': getAnalyticsSessionId(),
    });

    const applySyncReply = async () => {
      const sync = await fetch(`${API_URL}/v1/chat`, {
        method: 'POST',
        headers: requestHeaders,
        body: requestBody,
        signal: ac.signal,
      });
      if (!sync.ok) throw new Error(`API ${sync.status}`);
      const d = await sync.json();
      if (d.conversation_id) sessionIdRef.current = d.conversation_id;
      const content = cleanResponse(d.reply ?? '');
      const meta = {
        citations: d.citations ?? [],
        faithfulnessScore: d.faithfulness_score ?? null,
        retrievalMode: d.retrieval_mode ?? 'keyword',
        escalationRequired: d.escalation_required ?? false,
        escalationReason: d.escalation_reason ?? '',
      };
      const cur = useChatStore.getState().chat;
      const last = cur[cur.length - 1];
      if (last?.role === 'assistant') {
        updateLastTurn((t) => ({ ...t, content, ...meta }));
      } else {
        addTurns([createTurn('assistant', content, meta)]);
      }
      trackChatReceived(Date.now() - t0, (d.sources?.length ?? 0) > 0);
    };

    try {
      const res = await fetch(`${API_URL}/v1/chat/stream`, {
        method: 'POST',
        headers: requestHeaders,
        body: requestBody,
        signal: ac.signal,
      });
      if (!res.ok) {
        await applySyncReply();
        return;
      }
      addTurns([createTurn('assistant', '', {})]);
      const reader = res.body?.getReader();
      if (!reader) throw new Error('No body');
      const dec = new TextDecoder();
      let buf = '', streamed = '', pending = '', meta: Record<string, unknown> = {}, evt = 'token';
      let raf: number | null = null;
      const flushPending = () => {
        if (!pending) {
          raf = null;
          return;
        }
        streamed += pending;
        pending = '';
        updateLastTurn((t) => ({ ...t, content: streamed }));
        raf = null;
      };
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
            if (evt === 'done') {
              const trimmed = data.trim();
              if (trimmed) {
                try {
                  const p = JSON.parse(trimmed);
                  meta = { ...meta, ...p };
                  if (p.conversation_id) sessionIdRef.current = p.conversation_id;
                  if (typeof p.reply === 'string' && p.reply.trim()) {
                    streamed = cleanResponse(p.reply);
                    pending = '';
                    updateLastTurn((t) => ({ ...t, content: streamed }));
                  }
                  updateLastTurn((t) => ({ ...t, citations: p.citations ?? t.citations, faithfulnessScore: p.faithfulness_score ?? t.faithfulnessScore, retrievalMode: p.retrieval_mode ?? t.retrievalMode, escalationRequired: p.escalation_required ?? t.escalationRequired, escalationReason: p.escalation_reason ?? t.escalationReason }));
                } catch {
                  pending += data;
                  if (raf === null) {
                    raf = requestAnimationFrame(flushPending);
                  }
                }
              }
              evt = 'token';
              continue;
            }
            if (evt === 'revision') {
              const revised = cleanResponse(data);
              streamed = revised;
              pending = '';
              updateLastTurn((t) => ({ ...t, content: revised }));
              evt = 'token';
              continue;
            }
            if (evt === 'metadata' || evt === 'grounding') {
              try { const p = JSON.parse(data); meta = { ...meta, ...p }; if (p.conversation_id) sessionIdRef.current = p.conversation_id; updateLastTurn((t) => ({ ...t, citations: p.citations ?? t.citations, faithfulnessScore: p.faithfulness_score ?? t.faithfulnessScore, retrievalMode: p.retrieval_mode ?? t.retrievalMode, escalationRequired: p.escalation_required ?? t.escalationRequired, escalationReason: p.escalation_reason ?? t.escalationReason })); } catch {}
              evt = 'token'; continue;
            }
            if (evt === 'agent_trace') {
              // Buffered retrieval/iteration/tool-call trace, emitted just before
              // `grounding` (see chat_stream in main.py). Nothing in this UI
              // visualizes it yet; without this case it fell through to the
              // token branch below and the raw JSON trace was appended straight
              // into the visible reply, right where the citation marker sits.
              evt = 'token'; continue;
            }
            if (data || evt === 'token') {
              pending += data || '\n';
              if (raf === null) {
                raf = requestAnimationFrame(flushPending);
              }
            }
            evt = 'token';
          }
        }
        dec.decode();
        if (raf !== null) {
          cancelAnimationFrame(raf);
          flushPending();
        } else if (pending) {
          flushPending();
        }
        const cleaned = cleanResponse(streamed);
        if (cleaned !== streamed) {
          updateLastTurn((t) => ({ ...t, content: cleaned }));
        }
      } finally { reader.releaseLock(); }
      if (!useChatStore.getState().chat.at(-1)?.content.trim()) {
        await applySyncReply();
        return;
      }
      trackChatReceived(Date.now() - t0, (Array.isArray(meta.sources) && meta.sources.length > 0));
    } catch {
      const cur = useChatStore.getState().chat;
      const last = cur[cur.length - 1];
      if (userStoppedRef.current) {
        if (last?.role === 'assistant' && !last.content.trim()) {
          updateLastTurn((t) => ({ ...t, content: 'Stopped.' }));
        }
      } else if (last?.role === 'assistant' && last?.content === '') {
        updateLastTurn((t) => ({ ...t, content: 'Sorry, I could not reach the URA knowledge base. Please try again shortly.' }));
      } else if (!userStoppedRef.current) {
        addTurns([createTurn('assistant', 'Sorry, I could not reach the URA knowledge base. Please try again shortly.')]);
      }
      if (!userStoppedRef.current) trackErrorOccurred('chat_fetch_failed');
    } finally {
      clearTimeout(timeout);
      streamAbortRef.current = null;
      userStoppedRef.current = false;
      setIsLoading(false);
      saveCurrentSession();
    }
  }, [message, isLoading, locale, activeConversationId, pendingAttachments, addTurns, ensureActiveConversationId, setMessage, updateLastTurn, saveCurrentSession]);

  const stopGeneration = useCallback(() => {
    userStoppedRef.current = true;
    streamAbortRef.current?.abort();
  }, []);

  // ---- Voice input ----

  const handleMicClick = useCallback(async () => {
    if (isTransitioning) return;
    if (voiceMode && hasMediaRecorder) {
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
          const conversationId = activeConversationId ?? ensureActiveConversationId();
          trackChatSent(0);
          try {
            const r = await voiceChat(pcm16, {
              language: locale,
              conversationId,
              ttsEnabled: autoNarrate,
              voice: useVoiceStore.getState().voiceByLocale[locale] || undefined,
              sessionId: getAnalyticsSessionId(),
            });
            if (r.error && !r.transcript) { addTurns([createTurn('assistant', `Voice error: ${r.error}`)]); trackErrorOccurred('voice_chat_failed'); return; }
            if (r.transcript) { addTurns([createTurn('user', r.transcript)]); lastUserQueryRef.current = r.transcript; }
            if (r.reply) {
              addTurns([createTurn('assistant', r.reply, { citations: r.citations ?? [], faithfulnessScore: r.faithfulness_score ?? null, retrievalMode: r.retrieval_mode ?? 'keyword' })]);
              trackChatReceived(Date.now() - t0, (r.sources?.length ?? 0) > 0);
              const tid = useChatStore.getState().chat[useChatStore.getState().chat.length - 1]?.id;
              if (r.reply_audio_base64) {
                if (tid) { setPlayingTurnId(tid); try { await playAudioBase64(r.reply_audio_base64); } finally { setPlayingTurnId((p) => p === tid ? null : p); } }
              } else if (autoNarrate && tid) {
                // Server skipped inline narration (time budget) — the text is
                // already on screen; fetch the audio as its own request.
                void handleListenToReply(tid, r.reply);
              }
            }
          } catch { addTurns([createTurn('assistant', 'Sorry, I could not process your voice. Please try again or type.')]); trackErrorOccurred('voice_recording_failed'); } finally { setIsLoading(false); saveCurrentSession(); }
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
    // Dictation. The browser Speech API is both the cheap path — no upload, no
    // server round-trip — and the only one that can transcribe *while* someone
    // is speaking, so it is tried first wherever it exists.
    if (recognitionRef.current) {
      if (speechState === 'listening') {
        // Clearing intent before stop() so onend does not read the stop as a
        // silence timeout and immediately restart the session.
        dictationActiveRef.current = false;
        recognitionRef.current.stop();
        return;
      }
      // Read the composer from the store rather than the closure: this handler
      // would otherwise have to depend on `message` and be rebuilt on every
      // keystroke, and a stale closure here would silently erase whatever was
      // typed between renders.
      setDictationNotice(null);
      dictationBaseRef.current = useChatStore.getState().message;
      dictationFinalRef.current = '';
      dictationActiveRef.current = true;
      dictationRapidEndsRef.current = 0;
      trackVoiceUsed();
      try {
        recognitionRef.current.start();
      } catch {
        // start() throws if a previous session has not finished tearing down.
        dictationActiveRef.current = false;
        setSpeechState('error');
      }
      return;
    }

    // No Speech API. Firefox has never shipped one and Chrome does not expose it
    // on every platform, so this is the common case, not the edge: roughly
    // "everyone not on Chrome or Safari". The button used to fall off the end of
    // this function and return silently — enabled, tappable, doing nothing —
    // because `speechUnavailable` only covers the case where MediaRecorder is
    // *also* missing. Record and let the server's ASR transcribe instead; the
    // endpoint is already there and voice mode has been using it all along.
    if (!hasMediaRecorder) return;
    setDictationNotice(null);
    setIsTransitioning(true);
    try {
      if (isRecording) {
        const rec = recorderRef.current;
        if (!rec) return;
        recorderRef.current = null;
        setIsRecording(false);
        setSpeechState('idle');
        const pcm16 = await rec.stop();
        if (pcm16.byteLength === 0) return;
        setSpeechState('processing');
        try {
          const r = await transcribe(pcm16, locale);
          // Append rather than replace: dictation is a way to fill the
          // composer, and someone who typed half a question then tapped the
          // mic means to finish it, not to lose it. Read from the store, not
          // the closure — this resolves after a network round-trip, by which
          // time a captured `message` is whatever it was when recording began.
          if (r.text) {
            setMessage(joinDictated(useChatStore.getState().message, r.text));
            setSpeechState('idle');
            return;
          }
          // Nothing came back. Two different things, and saying so is the
          // whole point: the backend now leaves `error` unset when a
          // transcriber ran and simply heard nothing, so silence stops being
          // reported as an outage. Either way the composer must not just sit
          // there empty with no explanation, which is what it used to do.
          setDictationNotice(
            r.error
              ? 'Speech recognition is unavailable right now — you can type instead.'
              : "Didn't catch that. Try again, or type your question.",
          );
          setSpeechState('idle');
          if (r.error) trackErrorOccurred('dictation_unavailable');
        } catch {
          setDictationNotice('Speech recognition is unavailable right now — you can type instead.');
          setSpeechState('error');
          trackErrorOccurred('dictation_transcribe_failed');
        }
      } else {
        try {
          const rec = new AudioRecorder();
          recorderRef.current = rec;
          await rec.start();
          setIsRecording(true);
          setSpeechState('listening');
          trackVoiceUsed();
        } catch {
          recorderRef.current = null;
          setSpeechState('error');
        }
      }
    } finally {
      setIsTransitioning(false);
    }
  }, [isTransitioning, voiceMode, hasMediaRecorder, isRecording, locale, activeConversationId, autoNarrate, addTurns, ensureActiveConversationId, saveCurrentSession, speechState, setSpeechState, setMessage, handleListenToReply]);

  const handleCancelRecording = useCallback(() => {
    if (recorderRef.current) {
      recorderRef.current.cancel();
      recorderRef.current = null;
    }
    setIsRecording(false);
    setSpeechState('idle');
    setIsTransitioning(false);
  }, [setSpeechState]);

  const handleStarterPrompt = useCallback((prompt: string) => {
    trackStarterPromptUsed(prompt);
    sendMessage(prompt);
  }, [sendMessage]);

  /**
   * Voice mode and narration move together, in both directions.
   *
   * Entering voice mode turns narration on — a voice mode that listens but
   * answers in silence is not what anyone means by it. The reverse also has to
   * hold: turning narration off in Settings while voice mode is on used to
   * leave `voiceMode=true, autoNarrate=false`, the exact pair this coupling
   * exists to prevent, because the composer owned the rule and the settings
   * toggle wrote past it. Both entry points now go through these.
   */
  const setVoiceModeWithNarration = useCallback((on: boolean) => {
    setVoiceMode(on);
    setAutoNarrate(on);
  }, []);

  const setNarration = useCallback((on: boolean) => {
    setAutoNarrate(on);
    // Silencing narration leaves voice mode listening with nothing to say back.
    if (!on) setVoiceMode(false);
  }, []);

  // ---- Derived state ----

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

  // ---- Shared composer props ----
  const composerProps = {
    message,
    isLoading,
    isRecording,
    isTransitioning,
    speechUnavailable: speechState === 'unavailable' && !hasMediaRecorder,
    speechState,
    voiceMode,
    onMessageChange: setMessage,
    onSend: sendMessage,
    onMicClick: handleMicClick,
    onCancelRecording: handleCancelRecording,
    onFocus: () => {
      shouldStickToBottomRef.current = true;
      window.setTimeout(() => scrollToBottom('smooth'), 80);
    },
    attachments: pendingAttachments,
    onAttachFiles: attachFiles,
    onRemoveAttachment: removeAttachment,
    // Voice mode is the composer's only conversation-level control. Language
    // is session-level and lives in the header instead — see <ChatHeader />.
    //
    // It carries narration with it. The separate "Narrate" checkbox is gone,
    // and a voice mode that listens but answers in silence is not the thing
    // anyone means by voice mode — so entering turns auto-narrate on and
    // leaving turns it off, which is what ticking both boxes used to do.
    onVoiceModeChange: setVoiceModeWithNarration,
    voiceModeDisabled: !serverReady && !hasMediaRecorder,
    dictationNotice,
    onStop: stopGeneration,
  };

  // ---- Render ----

  return (
    <>
    <div className="app-shell chatv2" data-sidebar={railCollapsed ? 'collapsed' : 'open'}>
      {/* ── Conversation sidebar ── */}
      <ConversationRail
        open={sidebarOpen}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onClose={() => setSidebarOpen(false)}
        onNewConversation={() => { createNewSession(); setSidebarOpen(false); }}
        onSelectConversation={(id) => { switchSession(id); setSidebarOpen(false); }}
        onDeleteConversation={(id) => {
          const conv = conversations.find((c) => c.id === id);
          setConfirmReq({
            title: 'Delete conversation?',
            message: `"${conv?.title ?? 'This conversation'}" and its ${conv?.turns.length ?? 0} messages will be permanently deleted.`,
            confirmLabel: 'Delete',
            danger: true,
            action: () => deleteSession(id),
          });
        }}
        onOpenSettings={() => {
          setSidebarOpen(false);
          openSettings('general');
        }}
      />

      {/* ── Main column (top bar + content) ── */}
      <div className="app-main-col">
      <ChatHeader
        hasStartedChat={hasStartedChat}
        onOpenSidebarMobile={() => setSidebarOpen(true)}
        onToggleRailCollapse={() => setRailCollapsed((v) => !v)}
        onNewChat={() => createNewSession()}
        onRequestClear={() =>
          setConfirmReq({
            title: 'Clear conversation?',
            message: 'All messages in the current conversation will be removed and you will return to the start screen.',
            confirmLabel: 'Clear',
            danger: true,
            action: () => reset(),
          })
        }
        onOpenSettings={() => openSettings()}
        blogUrl={BLOG_URL}
        locale={locale}
        localeOptions={LOCALE_OPTIONS}
        onLocaleChange={setLocale}
      />

      <main className="app-content">
        {!hasStartedChat ? (
          /* ── Landing state — input-first hierarchy (chatv2) ── */
          <div className="landing">
            <div className="landing-brand">
              <h1 className="ldv2-brand">
                <Image
                  src="/ura-assistant-logo.svg"
                  alt=""
                  aria-hidden="true"
                  width={34}
                  height={34}
                  priority
                />
                URA Tax Assistant
              </h1>
              <h2 className="ldv2-headline">How can I help with your taxes?</h2>
              <p className="landing-sub">
                Official AI-powered assistant for Uganda Revenue Authority
              </p>
            </div>

            <div ref={chatDockRef} className="landing-composer landing-dock chat-dock">
              <ChatInput {...composerProps} />
            </div>

            <div className="landing-prompts" role="group" aria-label="Suggested questions">
              {STARTER_PROMPTS.map((p) => (
                <button key={p.label} className="landing-chip" onClick={() => handleStarterPrompt(p.label)}>
                  <span className="ldv2-cat">{p.category}</span>
                  <span>{p.label}</span>
                </button>
              ))}
            </div>

            {identityStatus === 'signed-in' ? (
              <p className="landing-auth landing-auth-signed">
                Signed in as <strong>{identityName}</strong>.{' '}
                <button type="button" className="landing-auth-link" onClick={() => openSettings('account')}>
                  Account &amp; settings
                </button>
              </p>
            ) : (
              <p className="landing-auth">
                <Link className="landing-auth-link" href="/signin">Sign in</Link>
                {' or '}
                <Link className="landing-auth-link" href="/signup">create an account</Link>
                {' to save conversations and keep a tax profile — or just start asking.'}
              </p>
            )}
          </div>
        ) : (
          /* ── Chat state — full-width messages ── */
          <div className="chat-area" aria-label="Chat conversation">
            <div
              ref={messageListRef}
              className={`message-list${isLoading ? ' is-streaming' : ''}`}
              aria-live="polite"
            >
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
                  <article className="message-row message-row-assistant">
                    <div className="avatar assistant" aria-hidden="true"><BotIcon /></div>
                    <div className="bubble assistant">
                      <div className="stagev2" role="status">
                        <span className="stagev2-label">Searching the URA knowledge base…</span>
                        <div className="stagev2-skl" aria-hidden="true"><span /><span /></div>
                      </div>
                    </div>
                  </article>
                );
              })()}
              <div ref={messagesEndRef} className="messages-end-spacer" />
            </div>

            {showScrollToLatest && (
              <button
                type="button"
                className="scroll-to-latest"
                onClick={() => {
                  shouldStickToBottomRef.current = true;
                  scrollToBottom('smooth');
                }}
                aria-label="Scroll to latest response"
              >
                Latest
              </button>
            )}

            <div ref={chatDockRef} className="chat-dock">
              <ChatInput {...composerProps} />
            </div>
          </div>
        )}
      </main>

      {/* Destructive-action confirmation (chatv2) */}
      <ConfirmDialog
        confirm={confirmReq}
        onClose={(run) => {
          const req = confirmReq;
          setConfirmReq(null);
          if (run) req?.action();
        }}
      />

      {/* Settings. Inside the .chatv2 scope so it inherits the redesign tokens
          and the shared dialog styles. */}
      <SettingsDialog
        open={settingsOpen}
        onClose={closeSettings}
        tab={settingsTab}
        onTabChange={setSettingsTab}
        autoNarrate={autoNarrate}
        onAutoNarrateChange={setNarration}
        speechReady={Boolean(serverReady)}
        blogUrl={BLOG_URL}
      />

      </div>{/* end .app-main-col */}
    </div>{/* end .app-shell.chatv2 */}

    {/* The three voice overlays (VoiceChat, VoiceFirstChat, VoiceVisionMode)
        used to mount here. VoiceChat's only entry point was the header mic that
        this change removes; VoiceFirstChat and VoiceVisionMode had no entry
        point at all — no caller ever set their flags, so they had been
        unreachable since they were added. Keeping the state and the renders
        would have left three overlays that can never open and four pieces of
        page state that nothing can change. The components are still on disk. */}
    </>
  );
}
