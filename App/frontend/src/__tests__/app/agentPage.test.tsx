import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentQueue } from '../../app/agent/page';
import { analyticsApi, type TicketQueueItem } from '../../services/analyticsApi';

const MOCK_QUEUE_TICKET: TicketQueueItem = {
  id: 'tkt-agent-1',
  conversation_id: 'conv-agent-1',
  status: 'open',
  priority: 'normal',
  reason: 'Taxpayer requested officer consultation',
  user_query: 'How to file VAT return?',
  bot_reply: 'VAT is filed by 15th of the month.',
  created_at: Date.now() / 1000 - 3600,
  updated_at: Date.now() / 1000,
  assignee: '',
};

function renderAgentPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AgentQueue who={{ authenticated: true, role: 'ura_staff', email: 'officer@ura.go.ug' }} />
    </QueryClientProvider>,
  );
}

describe('AgentPage (/agent)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(analyticsApi, 'tickets').mockResolvedValue({
      count: 1,
      status_filter: 'open',
      priority_filter: 'all',
      team_filter: 'all',
      teams: ['general', 'customs'],
      limit: 50,
      offset: 0,
      tickets: [MOCK_QUEUE_TICKET],
    });
    vi.spyOn(analyticsApi, 'ticket').mockResolvedValue({
      ...MOCK_QUEUE_TICKET,
      handoff: {
        summary: 'Taxpayer needs VAT return filing assistance',
        topic: 'vat_filing',
        sentiment: 'neutral',
      },
      transcript: [
        { user_message: 'How to file VAT return?', bot_reply: 'VAT is filed by 15th.', created_at: 1 },
      ],
    });
    vi.spyOn(analyticsApi, 'ticketSla').mockResolvedValue({
      period_days: 30,
      tickets: 5,
      responded: 4,
      resolved: 3,
      awaiting_first_response: 1,
      median_response_seconds: 120,
      median_resolution_seconds: 600,
    });
  });

  it('renders queue tabs and ticket item', async () => {
    renderAgentPage();

    // Check tabs
    expect(await screen.findByRole('tab', { name: /Next up/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Mine/i })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Resolved/i })).toBeInTheDocument();

    // Check ticket item in queue list
    await waitFor(() => {
      expect(screen.getAllByText(/How to file VAT return\?/i).length).toBeGreaterThanOrEqual(1);
    });
  });
});
