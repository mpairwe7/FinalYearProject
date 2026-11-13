/**
 * One answer that cannot be shown does not take the conversation with it (G650).
 * A card that threw during render reached app/error.tsx, which replaced the
 * whole chat, and "Try again" re-read the same stored turn and threw again.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MessageBoundary } from '../../components/MessageBoundary';
import { useChatStore } from '../../store/useChatStore';

function Boom(): React.ReactElement {
  throw new Error('workflow.options.map is not a function');
}

afterEach(() => {
  vi.restoreAllMocks();
  useChatStore.setState({ locale: 'en' });
});

describe('MessageBoundary', () => {
  it('shows a notice in place of the turn that failed, and keeps the others', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <div>
        <MessageBoundary turnId="a1"><p>First answer</p></MessageBoundary>
        <MessageBoundary turnId="a2"><Boom /></MessageBoundary>
        <MessageBoundary turnId="a3"><p>Third answer</p></MessageBoundary>
      </div>,
    );
    expect(screen.getByText('First answer')).toBeInTheDocument();
    expect(screen.getByText('Third answer')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/couldn.t be shown/i);
  });

  it('speaks the reader’s language', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    useChatStore.setState({ locale: 'sw' });
    render(<MessageBoundary turnId="a2"><Boom /></MessageBoundary>);
    expect(screen.getByRole('status')).not.toHaveTextContent(/couldn.t be shown/i);
  });
});
