import { useEffect, useRef } from 'react';
import { stripCitationMarkers } from '../lib/answerText';
import { authHeaders } from '../lib/authSession';
import { getChatSessionId } from '../lib/chatSession';
import { useChatStore, type ChatTurn } from '../store/useChatStore';

/** Enough of each turn for the model to see what the conversation is about. */
const EXCERPT_CHARS = 1000;
const REQUEST_TIMEOUT_MS = 20_000;

function isAnswer(turn: ChatTurn): boolean {
  return (
    turn.role === 'assistant' &&
    turn.id !== 'greeting-0' &&
    !turn.notice &&
    turn.retrievalMode !== 'officer_reply' &&
    Boolean(turn.content.trim())
  );
}

/**
 * Name the active conversation the way ChatGPT, Claude and Gemini do: once the
 * first answer has landed, ask `/v1/chat/title` for a few-word summary of the
 * opening exchange and show that in the sidebar instead of the first message.
 *
 * It watches the store rather than the send path, so typed, spoken and
 * attachment turns are all covered, and an older conversation is named the
 * first time it is opened. One request per conversation per page load: a
 * failed or empty answer leaves the provisional title (lib/chatTitle) in place
 * until a later visit, and a rename always wins (`setGeneratedTitle`).
 */
export function useAutoTitle(isLoading: boolean): void {
  const conversation = useChatStore((s) => s.conversations.find((c) => c.id === s.activeConversationId));
  const locale = useChatStore((s) => s.locale);
  const attempted = useRef(new Set<string>());

  useEffect(() => {
    if (isLoading || !conversation || conversation.titleCustom || conversation.titleGenerated) return;
    if (attempted.current.has(conversation.id)) return;
    const question = conversation.turns.find((turn) => turn.role === 'user' && turn.content.trim());
    const answer = conversation.turns.find(isAnswer);
    if (!question || !answer) return;
    if (typeof navigator !== 'undefined' && navigator.onLine === false) return;

    attempted.current.add(conversation.id);
    const id = conversation.id;
    const body = JSON.stringify({
      messages: [
        { role: 'user', content: question.content.trim().slice(0, EXCERPT_CHARS) },
        { role: 'assistant', content: stripCitationMarkers(answer.content).trim().slice(0, EXCERPT_CHARS) },
      ],
      locale,
    });
    // Not aborted on re-render: every save hands this effect a new conversation
    // object, and the answer is still wanted when it arrives.
    void (async () => {
      try {
        const response = await fetch('/api/v1/chat/title', {
          method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/json', 'X-Session-ID': getChatSessionId() }),
          body,
          signal: typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(REQUEST_TIMEOUT_MS) : undefined,
        });
        if (!response.ok) return;
        const data = (await response.json()) as { title?: unknown; source?: unknown };
        if (data.source === 'model' && typeof data.title === 'string' && data.title.trim()) {
          useChatStore.getState().setGeneratedTitle(id, data.title);
        }
      } catch {
        // Offline, timed out or refused: the provisional title stays.
      }
    })();
  }, [conversation, isLoading, locale]);
}
