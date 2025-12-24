"use client";

import React, { useEffect, useMemo, useRef, useState } from 'react';

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

interface ChatTurn {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

type SpeechState = 'idle' | 'listening' | 'unavailable' | 'error';

const starterPrompts = [
  'What services does URA provide?',
  'How do I submit a document online?',
  'Summarize the latest policy update from the PDF set.',
  'What are the contact options for support?'
];

export default function Page() {
  const [message, setMessage] = useState('');
  const [chat, setChat] = useState<ChatTurn[]>([
    {
      role: 'assistant',
      content: 'Hi! I can answer your questions about URA. Type or speak your question to begin.',
      timestamp: Date.now(),
    },
  ]);
  const [speechState, setSpeechState] = useState<SpeechState>('idle');
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
    setChat((prev) => [...prev, userTurn, pendingReply]);
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
      <div className="section-title">
        <div>
          <h1 style={{ margin: 0 }}>URA Chatbot</h1>
          <p className="small" style={{ marginTop: '0.35rem' }}>
            Human-centered UI with text and speech input. Frontend is Next.js on Bun; backend FastAPI.
          </p>
        </div>
        <div className="badge">Live Preview</div>
      </div>

      <div className="grid grid-2">
        <section className="card">
          <div className="section-title">
            <h2 style={{ margin: 0 }}>Conversation</h2>
            <span className="small">Context-aware responses (stubbed)</span>
          </div>
          <div className="message-list" aria-live="polite">
            {chat.map((turn) => (
              <article key={turn.timestamp + turn.role} className={`message ${turn.role}`}>
                <div className="small" style={{ marginBottom: '0.3rem', textTransform: 'capitalize' }}>
                  {turn.role}
                </div>
                <div>{turn.content}</div>
              </article>
            ))}
          </div>
          <div style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem' }}>
            <input
              className="input"
              placeholder="Ask a question or describe an issue..."
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <button className="button" onClick={sendMessage} aria-label="Send message">
              Send
            </button>
            <button
              className="button secondary"
              onClick={handleStartListening}
              disabled={speechState === 'unavailable'}
              aria-label="Voice input"
            >
              {speechState === 'listening' ? 'Stop' : 'Speak'}
            </button>
          </div>
          <div className="small" style={{ marginTop: '0.5rem' }}>
            Mic status: {speechStatusLabel}
          </div>
        </section>

        <aside className="card">
          <div className="section-title">
            <h3 style={{ margin: 0 }}>Quick prompts</h3>
            <span className="small">Tap to fill</span>
          </div>
          <div className="grid" style={{ gridTemplateColumns: '1fr', gap: '0.5rem' }}>
            {starterPrompts.map((p) => (
              <button
                key={p}
                className="button secondary"
                style={{ justifyContent: 'flex-start' }}
                onClick={() => setMessage(p)}
              >
                {p}
              </button>
            ))}
          </div>
          <div className="card" style={{ marginTop: '1rem', background: '#0b1224' }}>
            <h4 style={{ marginTop: 0, marginBottom: '0.35rem' }}>Design principles</h4>
            <ul style={{ paddingLeft: '1.1rem', margin: 0, color: 'var(--muted)', lineHeight: 1.5 }}>
              <li>Clear input affordances for text and speech.</li>
              <li>Readable contrast and generous spacing.</li>
              <li>Status hints for mic and response progress.</li>
            </ul>
          </div>
        </aside>
      </div>
    </main>
  );
}
