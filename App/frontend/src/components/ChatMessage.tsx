import React, { lazy, memo, Suspense, useCallback, useState } from 'react';
import { ChatAttachment, ChatTurn, Citation } from '../store/useChatStore';
import { URA_CONTACTS, sourceUrl, telDigits } from '../lib/uraContacts';
import { formatDocType } from '../lib/attachments';
import { localeLabel } from '../lib/locales';
import { getAnalyticsSessionId } from '../store/useAnalyticsStore';
import { authHeaders } from '../lib/authSession';
import FeedbackButtons from './FeedbackButtons';
import { SparklesIcon, SpeakerIcon, StopIcon, UserIcon, BotIcon, LoadingDots, CopyIcon, CheckIcon, FileIcon, DownloadIcon } from './Icons';

const Markdown = lazy(() => import('./Markdown'));

/** Copy an assistant reply to the clipboard with a brief confirmation. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard unavailable (insecure context / denied) — no-op */
    }
  }, [text]);
  return (
    <button
      type="button"
      className={`copy-btn ${copied ? 'copied' : ''}`}
      onClick={onCopy}
      aria-label={copied ? 'Reply copied' : 'Copy reply'}
    >
      {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
    </button>
  );
}

/** Download the branded PDF analysis report for an attached document. */
function ReportDownloadButton({ attachment }: { attachment: ChatAttachment }) {
  const [state, setState] = useState<'idle' | 'busy' | 'error'>('idle');
  const onDownload = useCallback(async () => {
    setState('busy');
    try {
      const res = await fetch(`/api/v1/documents/${attachment.id}/report`, {
        headers: authHeaders({ 'X-Session-ID': getAnalyticsSessionId() }),
      });
      if (!res.ok) throw new Error(`report ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `ura_analysis_${attachment.name.replace(/\.[^.]+$/, '')}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setState('idle');
    } catch {
      // Report expired (documents are held in memory with a TTL) or offline.
      setState('error');
      setTimeout(() => setState('idle'), 2500);
    }
  }, [attachment.id, attachment.name]);
  return (
    <button
      type="button"
      className="attachment-report-btn"
      onClick={onDownload}
      disabled={state === 'busy'}
      aria-label={
        state === 'error'
          ? 'Analysis report unavailable (expired)'
          : `Download analysis report for ${attachment.name}`
      }
      title="Download PDF analysis report"
    >
      {state === 'busy' ? <LoadingDots /> : state === 'error' ? 'Expired' : <><DownloadIcon /> Report</>}
    </button>
  );
}

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

        {!isAssistant && turn.attachments && turn.attachments.length > 0 && (
          <div className="msg-attachments">
            {turn.attachments.map((a) => (
              <div className="attachment-chip attachment-chip-sent" key={a.id}>
                <FileIcon />
                <span className="attachment-name" title={a.name}>{a.name}</span>
                <span className="attachment-meta">{formatDocType(a.docType)}</span>
                <ReportDownloadButton attachment={a} />
              </div>
            ))}
          </div>
        )}

        {isAssistant && !isGreeting && turn.escalationRequired && (
          <div className="escalation-banner" role="alert">
            <span aria-hidden="true">!</span> Human review recommended
            {turn.escalationReason ? ` — ${turn.escalationReason}` : ''}
            <div className="escalation-contacts">
              Contact URA:{' '}
              {URA_CONTACTS.tollFree.map((n, idx) => (
                <React.Fragment key={n}>
                  {idx > 0 && ' / '}
                  <a className="md-link" href={`tel:${telDigits(n)}`}>{n}</a>
                </React.Fragment>
              ))}
              {' · WhatsApp '}
              <a className="md-link" href={`tel:${telDigits(URA_CONTACTS.whatsapp)}`}>
                {URA_CONTACTS.whatsapp}
              </a>
              {' · '}
              <a className="md-link" href={URA_CONTACTS.website} target="_blank" rel="noopener noreferrer">
                ura.go.ug
              </a>
            </div>
          </div>
        )}

        {/* chatv2: grounding sits outside the citations block — a low-confidence
            answer often has no citations, and that is exactly when the reader
            most needs the warning. */}
        {isAssistant && !isGreeting && turn.content && turn.faithfulnessScore != null && (
          <div className="grounding-row">
            <span
              className={`grounding-badge ${turn.faithfulnessScore >= 0.6 ? 'grounding-ok' : 'grounding-warn'}`}
            >
              {turn.faithfulnessScore >= 0.6 ? 'Well grounded' : 'Verify with URA'}
            </span>
          </div>
        )}

        {isAssistant && turn.citations && turn.citations.length > 0 && (
          <details className="citations">
            <summary>
              <SparklesIcon /> Sources ({turn.citations.length})
            </summary>
            <ol>
              {turn.citations.map((c: Citation) => {
                const href = sourceUrl(c.source);
                return (
                  <li key={c.ref}>
                    {href ? (
                      <a
                        className="cite-source-link"
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        title="Find official URA documents at ura.go.ug"
                      >
                        <strong>{c.source}</strong>
                      </a>
                    ) : (
                      <strong>{c.source}</strong>
                    )}
                    {c.page ? ` p.${c.page}` : ''}
                    {c.section ? ` ${c.section}` : ''}
                    {c.passage && (
                      <div className="cite-passage">
                        {c.passage.slice(0, 180)}{c.passage.length > 180 ? '...' : ''}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </details>
        )}

        {isAssistant && !isGreeting && turn.content && (
          <div className="bubble-actions">
            <button
              className={`listen-btn ${playingTurnId === turn.id ? 'listen-btn-active' : ''}`}
              onClick={() => onListen(turn.id, turn.content)}
              disabled={ttsLoading === turn.id || isTransitioning}
              aria-label={playingTurnId === turn.id ? 'Stop listening' : `Listen in ${localeLabel(locale)}`}
            >
              {ttsLoading === turn.id ? <LoadingDots /> : playingTurnId === turn.id ? <><StopIcon /> Stop</> : <><SpeakerIcon /> Listen</>}
            </button>
            <CopyButton text={turn.content} />
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

function attachmentSignature(attachments: ChatAttachment[] | undefined): string {
  return (attachments ?? []).map((a) => `${a.id}${a.docType ?? ''}`).join('|');
}

const ChatMessage = memo(ChatMessageInner, (prev, next) => {
  return (
    prev.turn.id === next.turn.id &&
    prev.turn.content === next.turn.content &&
    prev.turn.faithfulnessScore === next.turn.faithfulnessScore &&
    prev.turn.retrievalMode === next.turn.retrievalMode &&
    prev.turn.escalationRequired === next.turn.escalationRequired &&
    prev.turn.escalationReason === next.turn.escalationReason &&
    attachmentSignature(prev.turn.attachments) === attachmentSignature(next.turn.attachments) &&
    citationSignature(prev.turn.citations) === citationSignature(next.turn.citations) &&
    prev.userQuery === next.userQuery &&
    prev.playingTurnId === next.playingTurnId &&
    prev.ttsLoading === next.ttsLoading &&
    prev.locale === next.locale &&
    prev.isTransitioning === next.isTransitioning
  );
});

export default ChatMessage;
