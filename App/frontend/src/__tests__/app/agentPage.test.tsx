import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentQueue } from '../../app/agent/page';
import { analyticsApi, type TicketDetail, type TicketQueueItem } from '../../services/analyticsApi';

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

/**
 * Claiming a case takes it out of Next up. The page used to drop a case the
 * moment it left the list being shown and fall back to the top of the queue,
 * so the pane swapped to the next taxpayer's case and the officer's next
 * reply went to them — which is what happened on the live system.
 */
describe('AgentPage keeps the case the officer is working on', () => {
  const now = Date.now() / 1000;
  const caseA: TicketQueueItem = {
    ...MOCK_QUEUE_TICKET, id: 'aaaaaaaa-0000-4000-8000-000000000001', user_query: 'Customs seizure notice dispute',
    priority: 'high', created_at: now - 7200,
  };
  const caseB: TicketQueueItem = {
    ...MOCK_QUEUE_TICKET, id: 'bbbbbbbb-0000-4000-8000-000000000002', user_query: 'PAYE for 25 factory workers',
    created_at: now - 600,
  };
  let claimed: Set<string>;

  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, '', '/agent');
    claimed = new Set();
    const assignee = (t: TicketQueueItem) => (claimed.has(t.id) ? 'officer@ura.go.ug' : '');
    vi.spyOn(analyticsApi, 'tickets').mockImplementation(async (status = 'open') => {
      const all = [caseA, caseB].map((t) => ({ ...t, assignee: assignee(t), status: claimed.has(t.id) ? 'assigned' : 'open' }));
      const rows = all.filter((t) => (status === 'assigned' ? claimed.has(t.id) : status === 'open' ? !claimed.has(t.id) : true));
      return { count: rows.length, status_filter: status, priority_filter: 'all', team_filter: 'all', teams: [], limit: 50, offset: 0, tickets: rows };
    });
    vi.spyOn(analyticsApi, 'ticket').mockImplementation(async (id: string) => {
      const t = id === caseA.id ? caseA : caseB;
      return { ...t, assignee: assignee(t), status: claimed.has(t.id) ? 'assigned' : 'open', transcript: [] } as unknown as TicketDetail;
    });
    vi.spyOn(analyticsApi, 'ticketSla').mockResolvedValue({
      period_days: 30, tickets: 2, responded: 0, resolved: 0, awaiting_first_response: 2,
      median_response_seconds: null, median_resolution_seconds: null,
    } as never);
    vi.spyOn(analyticsApi, 'updateTicket').mockImplementation(async (id: string) => {
      claimed.add(id);
      return { status: 'assigned' };
    });
  });

  const casePane = () => screen.getByRole('region', { name: 'Ticket detail' });
  /** The reference of the case the pane shows. */
  const paneRef = () => casePane().querySelector('.st-case-ref-badge')?.textContent ?? '';

  it('stays on a claimed case and follows it to Mine', async () => {
    renderAgentPage();
    // Lands on the top of Next up: the high-priority case A.
    await waitFor(() => expect(paneRef()).toBe('#TIC-AAAAAAAA'));
    fireEvent.click(within(casePane()).getByRole('button', { name: 'Assign to me' }));
    await waitFor(() => expect(analyticsApi.updateTicket).toHaveBeenCalledWith(caseA.id, expect.objectContaining({ assignee: 'officer@ura.go.ug' })));
    // A has left Next up; the pane must not have swapped to B.
    await waitFor(() => expect(screen.getByRole('tab', { name: /Mine/ })).toHaveAttribute('aria-selected', 'true'));
    await waitFor(() => expect(paneRef()).toBe('#TIC-AAAAAAAA'));
    expect(window.location.search).toContain(`ticket=${caseA.id}`);
  });

  it('keeps an opened case open after it leaves the list shown', async () => {
    window.history.replaceState(null, '', `/agent?ticket=${caseB.id}`);
    claimed.add(caseB.id); // claimed elsewhere: no longer in Next up
    renderAgentPage();
    await waitFor(() => expect(paneRef()).toBe('#TIC-BBBBBBBB'));
  });
});
