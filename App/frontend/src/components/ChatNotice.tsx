'use client';

/**
 * A line from the chat itself, between the messages: an officer joined, or the
 * chat with them ended and the assistant is answering again. Centred and
 * quiet, so it reads as what happened to the conversation rather than as
 * something anyone said. Worded at render time, so it follows the language.
 */
import React from 'react';
import type { ChatTurn } from '../store/useChatStore';
import { useTranslation } from '../lib/i18n';

export function ChatNotice({ turn }: { turn: ChatTurn }) {
  const t = useTranslation();
  const name = turn.officerName || t('officerChat.officerLabel');
  const text = turn.notice === 'officer_joined' ? t('officerChat.joined', { name }) : t('officerChat.ended');
  return (
    <div className={`chat-notice is-${turn.notice ?? 'info'}`} data-notice={turn.notice}>
      <span className="chat-notice-line" aria-hidden="true" />
      <p className="chat-notice-text">{text}</p>
      <span className="chat-notice-line" aria-hidden="true" />
    </div>
  );
}
