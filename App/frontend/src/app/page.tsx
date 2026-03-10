"use client";

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useChatStore, ChatTurn, Citation, SpeechState, createTurn } from '../store/useChatStore';
import {
  initAnalytics,
  getAnalyticsSessionId,
  trackChatSent,
  trackChatReceived,
  trackVoiceUsed,
  trackStarterPromptUsed,
  trackErrorOccurred,
} from '../store/useAnalyticsStore';
import FeedbackButtons from '../components/FeedbackButtons';

// Minimal speech recognition types for browsers that expose them; keeps TS happy in Next.
type SpeechRecognitionConstructor = new () => SpeechRecognition;

interface SpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onstart: (() => void) | null;
  onerror: ((event: Event) => void) | null;
  onend: (() => void) | null;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

interface SpeechRecognitionResult {
  0: SpeechRecognitionAlternative;
  isFinal: boolean;
  length: number;
}

interface SpeechRecognitionAlternative {
  transcript: string;
}

interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionResultList {
  [index: number]: SpeechRecognitionResult;
  length: number;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

const starterPrompts = [
  'What services does URA provide?',
  'How do I register for a TIN?',
  'What is the current VAT rate in Uganda?',
  'How do I file my annual tax returns?'
];

const MicIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Z" />
    <path d="M19 12a7 7 0 0 1-14 0" />
    <path d="M12 19v3" />
  </svg>
);

const SendIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M4 12 3 4l18 8-18 8 1-8h9" />
  </svg>
);

const SparklesIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M12 3v3" />
    <path d="M12 18v3" />
    <path d="m5.64 5.64 2.12 2.12" />
    <path d="m16.24 16.24 2.12 2.12" />
    <path d="M3 12h3" />
    <path d="M18 12h3" />
    <path d="m5.64 18.36 2.12-2.12" />
    <path d="m16.24 7.76 2.12-2.12" />
    <path d="m9 12 3 3 3-3-3-3-3 3Z" />
  </svg>
);

const HeadphonesIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M3 18v-6a9 9 0 0 1 18 0v6" />
    <path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3" />
    <path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3" />
  </svg>
);

const UserIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M12 12a5 5 0 1 0-5-5 5 5 0 0 0 5 5Z" />
    <path d="M3 21a9 9 0 0 1 18 0" />
  </svg>
);

const BotIcon = () => (
  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="5" y="7" width="14" height="10" rx="2" />
    <path d="M12 7V3" />
    <path d="M8 10h.01" />
    <path d="M16 10h.01" />
    <path d="M9 16h6" />
  </svg>
);

const LoadingDots = () => (
  <span className="loading-dots" aria-label="Loading response">
    <span className="dot" />
    <span className="dot" />
    <span className="dot" />
  </span>
);

// Track the user query that precedes each assistant response for feedback context
function findPrecedingUserQuery(chat: ChatTurn[], assistantIndex: number): string {
  for (let i = assistantIndex - 1; i >= 0; i--) {
    if (chat[i].role === 'user') return chat[i].content;
  }
  return '';
}

const LOCALE_OPTIONS = [
  { value: 'en', label: 'English', speechLang: 'en-US' },
  { value: 'lg', label: 'Luganda', speechLang: 'lg-UG' },
] as const;

export default function Page() {
  const { message, setMessage, chat, speechState, setSpeechState, addTurns } = useChatStore();
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [locale, setLocale] = useState<string>('en');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const lastUserQueryRef = useRef<string>('');

  // Initialise analytics on mount
  useEffect(() => {
    initAnalytics();
  }, []);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  useEffect(() => {
    const SpeechRecognitionImpl =
      typeof window !== 'undefined' &&
      ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);

    if (!SpeechRecognitionImpl) {
      setSpeechState('unavailable');
      return;
    }

    const recog: SpeechRecognition = new SpeechRecognitionImpl();
    const speechLang = LOCALE_OPTIONS.find(l => l.value === locale)?.speechLang ?? 'en-US';
    recog.lang = speechLang;
    recog.continuous = false;
    recog.interimResults = false;

    recog.onstart = () => setSpeechState('listening');
    recog.onerror = () => setSpeechState('error');
    recog.onend = () => setSpeechState('idle');
    recog.onresult = (event: SpeechRecognitionEvent) => {
      const transcript = event.results?.[0]?.[0]?.transcript;
      if (transcript) setMessage(transcript);
    };

    recognitionRef.current = recog;
    return () => recog.abort();
  }, [locale]);

  const sendMessage = async () => {
    const text = message.trim();
    if (!text || isLoading) return;

    const userTurn = createTurn('user', text);
    addTurns([userTurn]);
    setMessage('');
    setIsLoading(true);
    lastUserQueryRef.current = text;

    // Track analytics
    trackChatSent(text.length);
    const sendTime = Date.now();

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);

    try {
      const response = await fetch(`${API_URL}/v1/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': getAnalyticsSessionId(),
        },
        body: JSON.stringify({ message: text, top_k: 4, locale }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const data = await response.json();
      const responseTimeMs = Date.now() - sendTime;

      const assistantTurn = createTurn('assistant', data.reply, {
        citations: data.citations ?? [],
        faithfulnessScore: data.faithfulness_score ?? null,
        retrievalMode: data.retrieval_mode ?? 'keyword',
        escalationRequired: data.escalation_required ?? false,
        escalationReason: data.escalation_reason ?? '',
      });
      addTurns([assistantTurn]);

      // Track response analytics
      trackChatReceived(responseTimeMs, (data.sources?.length ?? 0) > 0);
    } catch (err) {
      const errorTurn = createTurn(
        'assistant',
        'Sorry, I could not reach the URA knowledge base right now. Please try again shortly.',
      );
      addTurns([errorTurn]);
      trackErrorOccurred('chat_fetch_failed');
    } finally {
      clearTimeout(timeout);
      setIsLoading(false);
    }
  };

  const handleStartListening = () => {
    if (!recognitionRef.current) return;
    if (speechState === 'listening') {
      recognitionRef.current.stop();
      return;
    }
    trackVoiceUsed();
    recognitionRef.current.start();
  };

  const handleStarterPrompt = (prompt: string) => {
    setMessage(prompt);
    trackStarterPromptUsed(prompt);
  };

  const speechStatusLabel = useMemo(() => {
    switch (speechState) {
      case 'listening':
        return 'Listening';
      case 'unavailable':
        return 'Speech unavailable';
      case 'error':
        return 'Speech error';
      default:
        return 'Tap mic to speak';
    }
  }, [speechState]);

  return (
    <main>
      <div className="hero">
        <div>
          <div className="badge">
            <SparklesIcon />
            Live assistant
          </div>
          <h1 className="hero-title">URA Chatbot</h1>
          <div role="radiogroup" aria-label="Language" style={{ display: 'flex', justifyContent: 'center', gap: '0.5rem', margin: '0.5rem 0' }}>
            {LOCALE_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setLocale(opt.value)}
                aria-pressed={locale === opt.value}
                style={{
                  padding: '0.25rem 0.75rem',
                  borderRadius: '1rem',
                  border: locale === opt.value ? '2px solid var(--accent, #2563eb)' : '1px solid #ccc',
                  background: locale === opt.value ? 'var(--accent, #2563eb)' : 'transparent',
                  color: locale === opt.value ? '#fff' : 'inherit',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="hero-sub">
            Natural chat with speech and text. Ask about URA services, policies, or upload workflows and get concise answers.
          </p>
        </div>
        <div className="pill">
          <HeadphonesIcon />
          Voice ready
        </div>
      </div>

      <div className="grid grid-2">
        <section className="card chat-shell" aria-label="Chat conversation">
          <div className="section-title">
            <div>
              <h2 style={{ margin: 0 }}>Conversation</h2>
              <div className="small">Context-aware responses powered by URA knowledge base</div>
            </div>
            <div className="status">
              <MicIcon /> {speechStatusLabel}
            </div>
          </div>

          <div className="message-list" aria-live="polite">
            {chat.map((turn: ChatTurn, index: number) => (
              <article key={turn.id} className="message-row">
                <div className={`avatar ${turn.role === 'user' ? 'user' : 'assistant'}`} aria-hidden="true">
                  {turn.role === 'user' ? <UserIcon /> : <BotIcon />}
                </div>
                <div className={`bubble ${turn.role}`}>
                  <div className="small" style={{ marginBottom: '0.25rem', textTransform: 'capitalize' }}>
                    {turn.role}
                  </div>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{turn.content}</div>
                  {turn.role === 'assistant' && turn.id !== 'greeting-0' && turn.escalationRequired && (
                    <div className="escalation-banner" role="alert" style={{
                      marginTop: '0.5rem', padding: '0.4rem 0.75rem', borderRadius: '0.5rem',
                      background: 'rgba(234,179,8,0.12)', border: '1px solid rgba(234,179,8,0.3)',
                      fontSize: '0.8rem', color: 'var(--muted)',
                    }}>
                      Human review recommended{turn.escalationReason ? `: ${turn.escalationReason}` : ''}
                    </div>
                  )}
                  {turn.role === 'assistant' && turn.citations && turn.citations.length > 0 && (
                    <details className="citations-detail" style={{ marginTop: '0.5rem', fontSize: '0.8rem' }}>
                      <summary style={{ cursor: 'pointer', color: 'var(--muted)' }}>
                        Sources ({turn.citations.length})
                        {turn.faithfulnessScore != null && (
                          <span style={{
                            marginLeft: '0.5rem',
                            color: turn.faithfulnessScore >= 0.6 ? 'var(--success, #22c55e)' : 'var(--warning, #eab308)',
                          }}>
                            {turn.faithfulnessScore >= 0.6 ? 'Well grounded' : 'Verify with URA'}
                          </span>
                        )}
                      </summary>
                      <ol style={{ paddingLeft: '1.2rem', margin: '0.3rem 0 0', color: 'var(--muted)' }}>
                        {turn.citations.map((c: Citation) => (
                          <li key={c.ref} style={{ marginBottom: '0.2rem' }}>
                            <strong>{c.source}</strong>
                            {c.page ? ` p.${c.page}` : ''}
                            {c.section ? ` — ${c.section}` : ''}
                            {c.passage ? <div style={{ fontSize: '0.75rem', opacity: 0.8 }}>{c.passage.slice(0, 150)}...</div> : null}
                          </li>
                        ))}
                      </ol>
                    </details>
                  )}
                  {turn.role === 'assistant' && turn.id !== 'greeting-0' && (
                    <FeedbackButtons
                      messageId={turn.id}
                      userQuery={findPrecedingUserQuery(chat, index) || lastUserQueryRef.current}
                      botReply={turn.content}
                    />
                  )}
                </div>
              </article>
            ))}
            {isLoading && (
              <article className="message-row">
                <div className="avatar assistant" aria-hidden="true">
                  <BotIcon />
                </div>
                <div className="bubble assistant">
                  <div className="small" style={{ marginBottom: '0.25rem' }}>Assistant</div>
                  <LoadingDots />
                </div>
              </article>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div className="input-row">
            <input
              className="input"
              aria-label="Type your message"
              placeholder="Ask anything about URA..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
              disabled={isLoading}
            />
            <button
              className="button secondary"
              onClick={handleStartListening}
              disabled={speechState === 'unavailable' || isLoading}
              aria-label="Voice input"
            >
              <MicIcon /> {speechState === 'listening' ? 'Stop' : 'Speak'}
            </button>
            <button
              className="button"
              onClick={sendMessage}
              disabled={isLoading || !message.trim()}
              aria-label="Send message"
            >
              <SendIcon /> Send
            </button>
          </div>
          <div className="small" style={{ marginTop: '0.35rem' }}>
            Press Enter to send, or tap the mic to dictate.
          </div>
        </section>

        <aside className="card">
          <div className="section-title">
            <div>
              <h3 style={{ margin: 0 }}>Quick prompts</h3>
              <span className="small">Tap to fill and edit</span>
            </div>
            <div className="pill">
              <SendIcon /> Suggestions
            </div>
          </div>
          <div className="chip-grid">
            {starterPrompts.map((p) => (
              <button key={p} className="chip" onClick={() => handleStarterPrompt(p)}>
                <SparklesIcon /> {p}
              </button>
            ))}
          </div>
          <div className="card panel-note">
            <h4 style={{ marginTop: 0, marginBottom: '0.35rem' }}>Design principles</h4>
            <ul style={{ paddingLeft: '1.1rem', margin: 0, color: 'var(--muted)', lineHeight: 1.55 }}>
              <li>Clear affordances for text and speech.</li>
              <li>Readable contrast, gentle glassmorphism, subtle motion.</li>
              <li>Status hints for mic readiness and sending.</li>
            </ul>
          </div>
        </aside>
      </div>
    </main>
  );
}
