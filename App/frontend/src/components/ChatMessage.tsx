import React, { lazy, memo, Suspense } from 'react';
import { ChatTurn, Citation } from '../store/useChatStore';
import FeedbackButtons from './FeedbackButtons';
import { SparklesIcon, SpeakerIcon, StopIcon, UserIcon, BotIcon, LoadingDots } from './Icons';

const Markdown = lazy(() => import('./Markdown'));

interface ChatMessageProps {
  turn: ChatTurn;
  userQuery: string;
  locale: string;
  playingTurnId: string | null;
  ttsLoading: string | null;
  isTransitioning: boolean;
  onListen: (turnId: string, text: string) => void;
}

function ChatMessageInner({
  turn,
  userQuery,
  locale,
  playingTurnId,
  ttsLoading,
  isTransitioning,
  onListen,
}: ChatMessageProps) {
  const isAssistant = turn.role === 'assistant';
  const isGreeting = turn.id === 'greeting-0';

  return (
    <article className={`message-row message-row-${turn.role}`}>
      <div className={`avatar ${turn.role}`} aria-hidden="true">
        {turn.role === 'user' ? <UserIcon /> : <BotIcon />}
      </div>
      <div className={`bubble ${turn.role}`}>
        <span className="sr-only">{turn.role === 'user' ? 'You said' : 'Assistant replied'}</span>
        <div className="msg-content">
          {isAssistant ? (
            <Suspense fallback={turn.content}><Markdown content={turn.content} /></Suspense>
          ) : (
            turn.content
          )}
        </div>

        {isAssistant && !isGreeting && turn.escalationRequired && (
          <div className="escalation-banner" role="alert">
            <span aria-hidden="true">!</span> Human review recommended
            {turn.escalationReason ? ` — ${turn.escalationReason}` : ''}
          </div>
        )}

        {isAssistant && turn.citations && turn.citations.length > 0 && (
          <details className="citations">
            <summary>
              <SparklesIcon /> Sources ({turn.citations.length})
              {turn.faithfulnessScore != null && (
                <span className={turn.faithfulnessScore >= 0.6 ? 'grounding-ok' : 'grounding-warn'}>
                  {turn.faithfulnessScore >= 0.6 ? ' Well grounded' : ' Verify with URA'}
                </span>
              )}
            </summary>
            <ol>
              {turn.citations.map((c: Citation) => (
                <li key={c.ref}>
                  <strong>{c.source}</strong>
                  {c.page ? ` p.${c.page}` : ''}
                  {c.section ? ` ${c.section}` : ''}
                  {c.passage && (
                    <div className="cite-passage">
                      {c.passage.slice(0, 180)}{c.passage.length > 180 ? '...' : ''}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </details>
        )}

        {isAssistant && !isGreeting && turn.content && (
          <div className="bubble-actions">
            <button
              className={`listen-btn ${playingTurnId === turn.id ? 'listen-btn-active' : ''}`}
              onClick={() => onListen(turn.id, turn.content)}
              disabled={ttsLoading === turn.id || isTransitioning}
              aria-label={playingTurnId === turn.id ? 'Stop listening' : `Listen in ${locale === 'lg' ? 'Luganda' : 'English'}`}
            >
              {ttsLoading === turn.id ? <LoadingDots /> : playingTurnId === turn.id ? <><StopIcon /> Stop</> : <><SpeakerIcon /> Listen</>}
            </button>
            <FeedbackButtons messageId={turn.id} userQuery={userQuery} botReply={turn.content} />
          </div>
        )}
        {isAssistant && isGreeting && (
          <div className="bubble-actions">
            <FeedbackButtons messageId={turn.id} userQuery="" botReply={turn.content} />
          </div>
        )}
      </div>
    </article>
  );
}

function citationSignature(citations: Citation[] | undefined): string {
  return (citations ?? [])
    .map((c) => [c.ref, c.source, c.page ?? '', c.section ?? '', c.passage ?? ''].join('\u001f'))
    .join('\u001e');
}

const ChatMessage = memo(ChatMessageInner, (prev, next) => {
  return (
    prev.turn.id === next.turn.id &&
    prev.turn.content === next.turn.content &&
    prev.turn.faithfulnessScore === next.turn.faithfulnessScore &&
    prev.turn.retrievalMode === next.turn.retrievalMode &&
    prev.turn.escalationRequired === next.turn.escalationRequired &&
    prev.turn.escalationReason === next.turn.escalationReason &&
    citationSignature(prev.turn.citations) === citationSignature(next.turn.citations) &&
    prev.userQuery === next.userQuery &&
    prev.playingTurnId === next.playingTurnId &&
    prev.ttsLoading === next.ttsLoading &&
    prev.locale === next.locale &&
    prev.isTransitioning === next.isTransitioning
  );
});

export default ChatMessage;
