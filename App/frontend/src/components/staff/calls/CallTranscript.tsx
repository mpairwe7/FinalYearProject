import React, { useEffect, useRef, useState } from 'react';
import { CallTurn } from '@/services/callsApi';

interface CallTranscriptProps {
  turns: CallTurn[];
  interimCaption?: { speaker: string; text: string } | null;
}

function renderTextWithConfidenceMarks(text: string, lowConfWords?: Array<{ word: string; prob: number }>) {
  if (!lowConfWords || lowConfWords.length === 0) {
    return text;
  }

  // Build a map of lowercase word -> prob
  const confMap = new Map<string, number>();
  for (const item of lowConfWords) {
    if (item.word) {
      confMap.set(item.word.toLowerCase(), item.prob);
    }
  }

  // Tokenize by whitespace while preserving punctuation
  const tokens = text.split(/(\s+)/);
  return tokens.map((token, i) => {
    const clean = token.toLowerCase().replace(/^[^\w]+|[^\w]+$/g, '');
    if (clean && confMap.has(clean)) {
      const prob = confMap.get(clean) || 0;
      const pct = Math.round(prob * 100);
      return (
        <mark
          key={i}
          className="st-mark-low-conf"
          title={`heard with ${pct}% confidence`}
        >
          {token}
        </mark>
      );
    }
    return token;
  });
}

export function CallTranscript({ turns, interimCaption }: CallTranscriptProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);

  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  };

  useEffect(() => {
    if (isAtBottom) {
      scrollToBottom();
    }
  }, [turns, interimCaption, isAtBottom]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const atBottom = scrollHeight - (scrollTop + clientHeight) < 40;
    setIsAtBottom(atBottom);
  };

  return (
    <div style={{ position: 'relative', display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="st-transcript-scroll"
        style={{ flex: 1, overflowY: 'auto', padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.875rem' }}
      >
        {turns.length === 0 && !interimCaption && (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary, #6b7280)', fontStyle: 'italic' }}>
            No turns recorded yet on this call.
          </div>
        )}

        {turns.map((turn) => {
          if (turn.kind === 'language') {
            // A language lock or switch: a marker between turns, not a bubble.
            return (
              <div key={turn.id || turn.seq} className="st-language-note" role="note">
                {turn.text}
              </div>
            );
          }
          const isCaller = turn.speaker === 'caller';
          const isAssistant = turn.speaker === 'assistant';
          const isOfficer = turn.speaker === 'officer';
          const isClarify = turn.kind === 'clarify' || turn.kind === 'confirm';

          const bubbleClass = isCaller
            ? 'st-bubble--caller'
            : isOfficer
            ? 'st-bubble--officer'
            : isAssistant
            ? 'st-bubble--assistant'
            : 'st-bubble--system';

          return (
            <div
              key={turn.id || turn.seq}
              className={`st-bubble-wrap ${isCaller ? 'st-bubble-wrap--caller' : ''}`}
            >
              <div className="st-bubble-header">
                <span className="st-bubble-author">
                  {isCaller ? 'Caller' : isOfficer ? 'Officer' : isAssistant ? 'AI Assistant' : 'System'}
                </span>
                {isClarify && <span className="st-chip st-chip--clarify">Clarification</span>}
                {turn.mean_word_prob !== null && isCaller && (
                  <span className="st-bubble-meta">
                    ASR conf: {Math.round((turn.mean_word_prob || 0) * 100)}%
                  </span>
                )}
              </div>
              <div className={`st-bubble ${bubbleClass}`}>
                {renderTextWithConfidenceMarks(turn.text, turn.low_conf_words)}
              </div>
            </div>
          );
        })}

        {/* Live Interim Caption */}
        {interimCaption && (
          <div className="st-bubble-wrap">
            <div className="st-bubble-header">
              <span className="st-bubble-author" style={{ opacity: 0.7 }}>
                {interimCaption.speaker} (speaking…)
              </span>
            </div>
            <div className="st-bubble st-bubble--interim">
              {interimCaption.text}
            </div>
          </div>
        )}
      </div>

      {!isAtBottom && (
        <button
          type="button"
          className="st-jump-bottom-btn"
          onClick={() => {
            scrollToBottom();
            setIsAtBottom(true);
          }}
        >
          Jump to latest ↓
        </button>
      )}
    </div>
  );
}
