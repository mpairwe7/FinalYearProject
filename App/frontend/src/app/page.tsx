"use client";

import Image from 'next/image';
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
import { VoiceChat } from '../components/VoiceChat';
import { VoiceFirstChat } from '../components/VoiceFirstChat';
import { VoiceVisionMode } from '../components/VoiceVisionMode';
import ChatHeader from '../components/ChatHeader';
import { BotIcon } from '../components/Icons';
import { useIdentity } from '../hooks/useIdentity';

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

  // Settings dialog. `settingsTab` lets a caller open straight onto a section
  // (the sidebar's account row opens Account, everything else opens General).
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
  const [voiceChatMode, setVoiceChatMode] = useState(false);
  const [voiceFirstMode, setVoiceFirstMode] = useState(false);
  const [voiceVisionMode, setVoiceVisionMode] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const recorderRef = useRef<AudioRecorder | null>(null);

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

  // Browser Speech Recognition (re-created on locale change)
  useEffect(() => {
    if (recognitionRef.current) {
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
        voice: useVoiceStore.getState().voiceId || undefined,
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
      if (last?.role === 'assistant' && last?.content === '') {
        updateLastTurn((t) => ({ ...t, content: 'Sorry, I could not reach the URA knowledge base. Please try again shortly.' }));
      } else {
        addTurns([createTurn('assistant', 'Sorry, I could not reach the URA knowledge base. Please try again shortly.')]);
      }
      trackErrorOccurred('chat_fetch_failed');
    } finally {
      clearTimeout(timeout);
      setIsLoading(false);
      saveCurrentSession();
    }
  }, [message, isLoading, locale, activeConversationId, pendingAttachments, addTurns, ensureActiveConversationId, setMessage, updateLastTurn, saveCurrentSession]);

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
              voice: useVoiceStore.getState().voiceId || undefined,
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
    // Browser Speech API
    if (!recognitionRef.current) return;
    if (speechState === 'listening') { recognitionRef.current.stop(); return; }
    trackVoiceUsed();
    recognitionRef.current.start();
  }, [isTransitioning, voiceMode, hasMediaRecorder, isRecording, locale, activeConversationId, autoNarrate, addTurns, ensureActiveConversationId, saveCurrentSession, speechState, setSpeechState, handleListenToReply]);

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

  // ---- Derived state ----

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
    onVoiceModeChange: (on: boolean) => {
      setVoiceMode(on);
      setAutoNarrate(on);
    },
    voiceModeDisabled: !serverReady && !hasMediaRecorder,
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
        onOpenSettings={(tab) => {
          setSidebarOpen(false);
          openSettings(tab ?? 'general');
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
        voiceChatOpen={voiceChatMode}
        onToggleVoiceChat={() => setVoiceChatMode((v) => !v)}
        onOpenSettings={() => openSettings()}
        blogUrl={BLOG_URL}
        healthOk={speechHealth?.status === 'ready'}
        healthLabel={healthLabel}
        locale={locale}
        localeOptions={LOCALE_OPTIONS}
        onLocaleChange={setLocale}
      />

      <main className="app-content">
        {!hasStartedChat ? (
          /* ── Landing state — input-first hierarchy (chatv2) ── */
          <div className="landing">
            <div className="ldv2-block">
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

              <div className="landing-composer">
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

              {/* Auth entry points, stated as what they add rather than as a
                  gate: nothing above this line needs an account. */}
              {identityStatus === 'signed-in' ? (
                <p className="landing-auth landing-auth-signed">
                  Signed in as <strong>{identityName}</strong>.{' '}
                  <button type="button" className="landing-auth-link" onClick={() => openSettings('account')}>
                    Account &amp; settings
                  </button>
                </p>
              ) : (
                <p className="landing-auth">
                  <a className="landing-auth-link" href="/signin">Sign in</a>
                  {' or '}
                  <a className="landing-auth-link" href="/signup">create an account</a>
                  {' to save conversations and keep a tax profile — or just start asking.'}
                </p>
              )}
            </div>
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
        onAutoNarrateChange={setAutoNarrate}
        speechReady={Boolean(serverReady)}
        blogUrl={BLOG_URL}
      />

      </div>{/* end .app-main-col */}
    </div>{/* end .app-shell.chatv2 */}

    {/* Overlays render OUTSIDE the .chatv2 scope so they keep the legacy
        :root token values (the voice overlay is styled by globals.css and is
        out of the redesign's scope). */}

    {/* Voice-first overlay (Phase 23) */}
    <VoiceChat
      open={voiceChatMode}
      locale={locale}
      conversationId={activeConversationId ?? undefined}
      onClose={() => setVoiceChatMode(false)}
    />

    {/* Voice-first primary interface (Phase 27) */}
    {voiceFirstMode && (
      <VoiceFirstChat
        locale={locale}
        onClose={() => setVoiceFirstMode(false)}
        onOpenVision={() => {
          setVoiceFirstMode(false);
          setVoiceVisionMode(true);
        }}
      />
    )}

    {/* Voice + Vision mode (Phase 27) */}
    {voiceVisionMode && (
      <VoiceVisionMode
        locale={locale}
        onClose={() => setVoiceVisionMode(false)}
      />
    )}
    </>
  );
}
