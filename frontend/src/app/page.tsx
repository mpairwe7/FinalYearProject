"use client";

import React, { useEffect, useMemo, useRef } from 'react';
import { useChatStore, ChatTurn, SpeechState } from '../store/useChatStore';

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

const starterPrompts = [
  'What services does URA provide?',
  'How do I submit a document online?',
  'Summarize the latest policy update from the PDF set.',
  'What are the contact options for support?'
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

export default function Page() {
  const { message, setMessage, chat, speechState, setSpeechState, addTurns } = useChatStore();
  const recognitionRef = useRef<SpeechRecognition | null>(null);

  useEffect(() => {
    const SpeechRecognitionImpl =
      typeof window !== 'undefined' &&
      ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);

    if (!SpeechRecognitionImpl) {
      setSpeechState('unavailable');
      return;
    }

    const recog: SpeechRecognition = new SpeechRecognitionImpl();
    recog.lang = 'en-US';
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
  }, []);

  const sendMessage = () => {
    const text = message.trim();
    if (!text) return;
    const userTurn: ChatTurn = { role: 'user', content: text, timestamp: Date.now() };
    const pendingReply: ChatTurn = {
      role: 'assistant',
      content: 'Thinking... (stubbed response)',
      timestamp: Date.now(),
    };
    addTurns([userTurn, pendingReply]);
    setMessage('');
  };

  const handleStartListening = () => {
    if (!recognitionRef.current) return;
    if (speechState === 'listening') {
      recognitionRef.current.stop();
      return;
    }
    recognitionRef.current.start();
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
              <div className="small">Context-aware responses (stubbed)</div>
            </div>
            <div className="status">
              <MicIcon /> {speechStatusLabel}
            </div>
          </div>

          <div className="message-list" aria-live="polite">
            {chat.map((turn: ChatTurn) => (
              <article key={turn.timestamp + turn.role} className="message-row">
                <div className={`avatar ${turn.role === 'user' ? 'user' : 'assistant'}`} aria-hidden="true">
                  {turn.role === 'user' ? <UserIcon /> : <BotIcon />}
                </div>
                <div className={`bubble ${turn.role}`}>
                  <div className="small" style={{ marginBottom: '0.25rem', textTransform: 'capitalize' }}>
                    {turn.role}
                  </div>
                  <div>{turn.content}</div>
                </div>
              </article>
            ))}
          </div>

          <div className="input-row">
            <input
              className="input"
              placeholder="Ask anything about URA..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <button
              className="button secondary"
              onClick={handleStartListening}
              disabled={speechState === 'unavailable'}
              aria-label="Voice input"
            >
              <MicIcon /> {speechState === 'listening' ? 'Stop' : 'Speak'}
            </button>
            <button className="button" onClick={sendMessage} aria-label="Send message">
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
              <button key={p} className="chip" onClick={() => setMessage(p)}>
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
