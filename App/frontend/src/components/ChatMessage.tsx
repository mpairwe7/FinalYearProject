import React, { memo, useCallback, useMemo, useState } from 'react';
import { ChatAttachment, ChatTurn, Citation } from '../store/useChatStore';
import { URA_CONTACTS, citationHref, sourceLabel, telDigits } from '../lib/uraContacts';
import { formatDocType } from '../lib/attachments';
import { stripCitationMarkers } from '../lib/answerText';
import { localeLabel } from '../lib/locales';
import { useTranslation } from '../lib/i18n';
import { getAnalyticsSessionId } from '../store/useAnalyticsStore';
import { authHeaders } from '../lib/authSession';
import { detectDeadlineInMessage, downloadCalendarEvent, getGoogleCalendarUrl } from '../lib/calendarEvents';
import FeedbackButtons from './FeedbackButtons';
import HumanHandoff from './HumanHandoff';
import { SparklesIcon, SpeakerIcon, StopIcon, UserIcon, BotIcon, LoadingDots, CopyIcon, CheckIcon, FileIcon, DownloadIcon, EyeIcon } from './Icons';
import LoadingState from './LoadingState';
import Markdown from './Markdown';

/** Copy an assistant reply to the clipboard with a brief confirmation. */
function CopyButton({ text, noun = 'reply' }: { text: string; noun?: string }) {
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
      aria-label={copied ? `${noun.charAt(0).toUpperCase()}${noun.slice(1)} copied` : `Copy ${noun}`}
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
  /** Where an officer's reply comes back to. Null before the first turn. */
  conversationId: string | null;
  /** Set only on the turn currently being answered — see page.tsx. */
  phaseLabel?: string;
  phaseVariant?: string;
  phaseStartedAt?: number;
  onInspectAttachment?: (attachment: ChatAttachment) => void;
  onActionClick?: (action: string) => void;
}

/**
 * When to offer a person.
 *
 * Not on every answer — an offer that is always there is chrome, and on a good
 * answer it reads as the assistant hedging. These are the three states in
 * which the assistant has actually failed the taxpayer:
 *
 *   - it escalated the turn itself (the judge or the supervisor said so);
 *   - it abstained, which means it found nothing it was willing to stand
 *     behind; or
 *   - it answered but scored its own grounding low, which is the same warning
 *     the "Verify with URA" badge already shows.
 *
 * That last one is the case the reader is most likely to act on and the one
 * that previously ended in a phone number.
 */
function needsHuman(turn: ChatTurn): boolean {
  if (turn.escalationRequired) return true;
  if (turn.retrievalMode === 'abstained') return true;
  return turn.faithfulnessScore != null && turn.faithfulnessScore < 0.6;
}

function ChatMessageInner({
  turn,
  userQuery,
  locale,
  playingTurnId,
  ttsLoading,
  isTransitioning,
  onListen,
  conversationId,
  phaseLabel,
  phaseVariant,
  phaseStartedAt,
  onInspectAttachment,
  onActionClick,
}: ChatMessageProps) {
  const isAssistant = turn.role === 'assistant';
  const isGreeting = turn.id === 'greeting-0';
  const t = useTranslation();
  const deadlineEvent = useMemo(
    () => (isAssistant && !isGreeting ? detectDeadlineInMessage(turn.content) : null),
    [isAssistant, isGreeting, turn.content]
  );

  return (
    <article className={`message-row message-row-${turn.role}`}>
      <div className={`avatar ${turn.role}`} aria-hidden="true">
        {turn.role === 'user' ? <UserIcon /> : <BotIcon />}
      </div>
      <div className={`bubble ${turn.role}`}>
        <span className="sr-only">{turn.role === 'user' ? 'You said' : 'Assistant replied'}</span>
        {/* What the working indicator leaves behind. Above the answer, where
            the indicator itself was, so the turn ends where it was running. */}
        {isAssistant && phaseLabel && phaseStartedAt != null && (
          <LoadingState label={phaseLabel} variant={phaseVariant} startedAt={phaseStartedAt} />
        )}
        <div className="msg-content">
          {isAssistant ? (
            <Markdown content={isGreeting ? t('chat.greeting') : turn.content} />
          ) : (
            turn.content
          )}
        </div>

        {!isAssistant && turn.content && (
          <div className="bubble-actions bubble-actions-user">
            <CopyButton text={turn.content} noun="message" />
          </div>
        )}

        {!isAssistant && turn.attachments && turn.attachments.length > 0 && (
          <div className="msg-attachments">
            {turn.attachments.map((a) => (
              <div className="attachment-chip attachment-chip-sent" key={a.id}>
                <FileIcon />
                <span className="attachment-name" title={a.name}>{a.name}</span>
                <span className="attachment-meta">{formatDocType(a.docType, locale)}</span>
                {onInspectAttachment && (
                  <button
                    type="button"
                    className="attachment-report-btn"
                    onClick={() => onInspectAttachment(a)}
                    title="Inspect extracted fields & tax audit"
                    aria-label={`Inspect ${a.name}`}
                  >
                    <EyeIcon /> Inspect
                  </button>
                )}
                <ReportDownloadButton attachment={a} />
              </div>
            ))}
            {turn.attachments.map((a) =>
              a.analysis?.screenshot_guidance?.is_screenshot ? (
                <div className="portal-guidance-card" key={`guidance-${a.id}`} role="region" aria-label="URA Portal Guidance">
                  <div className="portal-guidance-badge-row">
                    <span className="portal-guidance-badge">
                      🌐 {a.analysis.screenshot_guidance.detected_portal}
                    </span>
                    {a.analysis.screenshot_guidance.detected_state ? (
                      <span className="portal-guidance-state">
                        {a.analysis.screenshot_guidance.detected_state}
                      </span>
                    ) : null}
                  </div>
                  {a.analysis.screenshot_guidance.issues_detected && a.analysis.screenshot_guidance.issues_detected.length > 0 ? (
                    <p className="portal-guidance-issue">
                      ⚠️ <strong>Identified:</strong> {a.analysis.screenshot_guidance.issues_detected.join(' ')}
                    </p>
                  ) : null}
                  {a.analysis.screenshot_guidance.steps && a.analysis.screenshot_guidance.steps.length > 0 ? (
                    <div className="portal-guidance-steps-wrap">
                      <span className="portal-guidance-steps-title">Recommended Resolution Steps:</span>
                      <ol className="portal-guidance-steps-list">
                        {a.analysis.screenshot_guidance.steps.map((step, idx) => (
                          <li key={idx}>{step}</li>
                        ))}
                      </ol>
                    </div>
                  ) : null}
                  <div className="portal-guidance-actions">
                    {a.analysis.screenshot_guidance.portal_url ? (
                      <a
                        href={a.analysis.screenshot_guidance.portal_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="portal-action-btn is-primary"
                      >
                        {a.analysis.screenshot_guidance.direct_action?.label || 'Open URA Portal ↗'}
                      </a>
                    ) : null}
                    {onInspectAttachment ? (
                      <button
                        type="button"
                        className="portal-action-btn is-ghost"
                        onClick={() => onInspectAttachment(a)}
                      >
                        <EyeIcon /> Inspect Click Guidance
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : null,
            )}
          </div>
        )}

        {isAssistant && !isGreeting && turn.escalationRequired && (
          <div className="escalation-banner" role="alert">
            <span aria-hidden="true">!</span> Human review recommended
            {turn.escalationReason ? ` — ${turn.escalationReason}` : ''}
            <div className="escalation-contacts">
              Or contact URA:{' '}
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

        {/* The way out, offered exactly where the assistant ran out of answer.
            It sits above the grounding badge and the citations on purpose: by
            the time someone is reading "Verify with URA" they have already
            decided this answer is not enough, and the next thing they should
            find is a person rather than a phone number that starts the
            conversation over. */}
        {isAssistant && !isGreeting && !phaseLabel && turn.content && needsHuman(turn) && (
          <HumanHandoff
            conversationId={conversationId}
            locale={locale}
            reason={userQuery}
          />
        )}

        {/* chatv2: grounding sits outside the citations block — a low-confidence
            answer often has no citations, and that is exactly when the reader
            most needs the warning. */}
        {isAssistant && !isGreeting && !phaseLabel && turn.content && (
          <div className="grounding-row">
            {turn.retrievalMode === 'calculator' ? (
              <span className="grounding-badge grounding-ok" title="Deterministic arithmetic against official URA statutory rate tables">
                ✓ Official Statutory Calculator
              </span>
            ) : turn.retrievalMode === 'education' ? (
              <span className="grounding-badge grounding-ok" title="Scaffolded curriculum verified against URA taxpayer handbooks">
                📚 Verified Taxpayer Education
              </span>
            ) : turn.retrievalMode === 'contact_channels' ? (
              <span className="grounding-badge grounding-ok" title="Official URA toll-free, WhatsApp, email and portal channels">
                📞 Official URA Helpdesk
              </span>
            ) : turn.faithfulnessScore != null ? (
              <span
                className={`grounding-badge ${turn.faithfulnessScore >= 0.6 ? 'grounding-ok' : 'grounding-warn'}`}
              >
                {turn.faithfulnessScore >= 0.6 ? 'Well grounded' : 'Verify with URA'}
              </span>
            ) : null}
          </div>
        )}

        {isAssistant && turn.citations && turn.citations.length > 0 && (
          <details className="citations">
            <summary>
              <SparklesIcon /> Sources ({turn.citations.length})
            </summary>
            <ol>
              {turn.citations.map((c: Citation) => {
                const href = citationHref(c);
                const label = c.title?.trim() || sourceLabel(c.source);
                return (
                  <li key={c.ref}>
                    {href ? (
                      <a
                        className="cite-source-link"
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer nofollow"
                        title={c.url || `${c.source} — official URA documents`}
                      >
                        <strong>{label}</strong>
                      </a>
                    ) : (
                      <strong title={c.source}>{label}</strong>
                    )}
                    {c.effective_date ? ` (${c.effective_date})` : ''}
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

        {isAssistant && !isGreeting && !phaseLabel && deadlineEvent && (
          <div className="deadline-sync-banner" role="region" aria-label="Statutory Tax Deadline Sync">
            <span className="deadline-sync-icon" aria-hidden="true">📅</span>
            <div className="deadline-sync-info">
              <span className="deadline-sync-title">{deadlineEvent.title}</span>
              <span className="deadline-sync-date">
                Due: {deadlineEvent.startDate.toLocaleDateString(locale === 'lg' ? 'en-UG' : locale, {
                  month: 'short',
                  day: 'numeric',
                  year: 'numeric',
                })}
              </span>
            </div>
            <div className="deadline-sync-actions">
              <button
                type="button"
                className="deadline-sync-btn"
                onClick={() => downloadCalendarEvent(deadlineEvent)}
                title="Download iCalendar (.ics) reminder file with 1-day alert"
                aria-label={`Download calendar reminder for ${deadlineEvent.title}`}
              >
                Add to Calendar (.ics)
              </button>
              <a
                className="deadline-sync-link"
                aria-label="Open in Google Calendar"
                href={getGoogleCalendarUrl(deadlineEvent)}
                target="_blank"
                rel="noopener noreferrer"
                title="Open in Google Calendar"
              >
                Google Calendar ↗
              </a>
            </div>
          </div>
        )}

        {isAssistant && !isGreeting && !phaseLabel && turn.nextActions && turn.nextActions.length > 0 && onActionClick && (
          <div className="chat-action-chips" role="group" aria-label="Suggested follow-up actions">
            {turn.nextActions.map((action, idx) => (
              <button
                key={idx}
                type="button"
                className="chat-action-chip"
                onClick={() => onActionClick(action)}
                title={`Ask: ${action}`}
              >
                <span className="chat-action-chip-arrow" aria-hidden="true">↳</span>
                <span>{action}</span>
              </button>
            ))}
          </div>
        )}

        {isAssistant && !isGreeting && !phaseLabel && turn.content && (
          <div className="bubble-actions">
            <button
              className={`listen-btn ${playingTurnId === turn.id ? 'listen-btn-active' : ''}`}
              onClick={() => onListen(turn.id, stripCitationMarkers(turn.content))}
              disabled={ttsLoading === turn.id || isTransitioning}
              aria-label={playingTurnId === turn.id ? 'Stop listening' : `Listen in ${localeLabel(locale)}`}
            >
              {ttsLoading === turn.id ? <LoadingDots /> : playingTurnId === turn.id ? <><StopIcon /> Stop</> : <><SpeakerIcon /> Listen</>}
            </button>
            <CopyButton text={stripCitationMarkers(turn.content)} />
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
    .map((c) => [c.ref, c.source, c.page ?? '', c.section ?? '', c.passage ?? '', c.url ?? '', c.effective_date ?? ''].join('\u001f'))
    .join('\u001e');
}

function attachmentSignature(attachments: ChatAttachment[] | undefined): string {
  return (attachments ?? []).map((a) => `${a.id}${a.docType ?? ''}`).join('|');
}

function actionsSignature(actions: string[] | undefined): string {
  return (actions ?? []).join('|');
}

const ChatMessage = memo(ChatMessageInner, (prev, next) => {
  return (
    prev.turn.id === next.turn.id &&
    prev.turn.content === next.turn.content &&
    prev.turn.thoughtForMs === next.turn.thoughtForMs &&
    prev.phaseLabel === next.phaseLabel &&
    prev.phaseVariant === next.phaseVariant &&
    prev.phaseStartedAt === next.phaseStartedAt &&
    prev.conversationId === next.conversationId &&
    prev.turn.faithfulnessScore === next.turn.faithfulnessScore &&
    prev.turn.retrievalMode === next.turn.retrievalMode &&
    prev.turn.escalationRequired === next.turn.escalationRequired &&
    prev.turn.escalationReason === next.turn.escalationReason &&
    actionsSignature(prev.turn.nextActions) === actionsSignature(next.turn.nextActions) &&
    prev.onActionClick === next.onActionClick &&
    attachmentSignature(prev.turn.attachments) === attachmentSignature(next.turn.attachments) &&
    citationSignature(prev.turn.citations) === citationSignature(next.turn.citations) &&
    prev.userQuery === next.userQuery &&
    prev.playingTurnId === next.playingTurnId &&
    prev.ttsLoading === next.ttsLoading &&
    prev.locale === next.locale &&
    prev.onInspectAttachment === next.onInspectAttachment &&
    prev.isTransitioning === next.isTransitioning
  );
});

export default ChatMessage;
