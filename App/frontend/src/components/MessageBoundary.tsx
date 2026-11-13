'use client';

import React from 'react';
import { translate } from '../lib/i18n';
import { useChatStore } from '../store/useChatStore';

interface Props {
  turnId: string;
  children: React.ReactNode;
}

interface State {
  failed: boolean;
}

/**
 * One turn that cannot be rendered is replaced by a notice; the rest of the
 * conversation stays (G650).
 *
 * Without it, a card that threw (a workflow from an older app version, a
 * corrupt stored field) reached app/error.tsx, which replaced the whole chat,
 * and "Try again" re-read the same stored turn and threw again: the taxpayer
 * lost every conversation until they cleared site data.
 */
export class MessageBoundary extends React.Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.error(`turn ${this.props.turnId} could not be shown`, error);
  }

  componentDidUpdate(previous: Props): void {
    // A different turn in this slot gets its own chance.
    if (previous.turnId !== this.props.turnId && this.state.failed) this.setState({ failed: false });
  }

  render(): React.ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <p className="message-boundary-notice" role="status">
        {translate(useChatStore.getState().locale, 'message.cannotShow')}
      </p>
    );
  }
}
