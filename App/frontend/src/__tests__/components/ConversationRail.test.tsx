/**
 * The rail shows which conversations hold an open support case (G629).
 *
 * A case is followed only while its conversation is open, so "New chat" left
 * an officer writing into a conversation the taxpayer could no longer see, and
 * nothing in the rail said where it was.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import ConversationRail from '../../components/ConversationRail';
import type { Conversation } from '../../store/useChatStore';

const conv = (over: Partial<Conversation>): Conversation => ({
  id: 'c', title: 'Chat', preview: '', turns: [], createdAt: 1, updatedAt: 1, ...over,
});

function renderRail(conversations: Conversation[]) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
    <ConversationRail
      open
      conversations={conversations}
      activeConversationId="new"
      onClose={vi.fn()}
      onNewConversation={vi.fn()}
      onSelectConversation={vi.fn()}
      onDeleteConversation={vi.fn()}
      onRenameConversation={vi.fn()}
      onPinConversation={vi.fn()}
      onOpenSearch={vi.fn()}
      onToggleRailCollapse={vi.fn()}
      onOpenSettings={vi.fn()}
    />
    </QueryClientProvider>,
  );
}

describe('ConversationRail — open support cases', () => {
  it('marks a conversation whose case is waiting, and one an officer is chatting in', () => {
    renderRail([
      conv({ id: 'a', title: 'Assessment query', activeTicketId: 'abcdef12-3456' }),
      conv({
        id: 'b', title: 'Refund', activeTicketId: 'fedcba98-7654',
        officerChat: { ticketId: 'fedcba98-7654', lastSeq: 2, state: 'officer' },
      }),
      conv({ id: 'c', title: 'VAT rate' }),
    ]);
    const rows = screen.getAllByRole('listitem');
    const row = (title: string) => rows.find((r) => r.textContent?.includes(title))!;
    expect(within(row('Assessment query')).getByText('Case open')).toBeInTheDocument();
    expect(within(row('Refund')).getByText('Officer chatting')).toBeInTheDocument();
    expect(within(row('VAT rate')).queryByText(/Case open|Officer chatting/)).toBeNull();
  });
});
