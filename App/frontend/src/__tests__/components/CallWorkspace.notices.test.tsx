/**
 * The officer's call workspace shows the live stream's notices where they can
 * be seen: a waiting call starts with its transcript collapsed, and an officer
 * deciding whether to take it must still see a paused stream or a near time
 * limit (ADR-0042, review of #717).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const live = {
  call: null,
  turns: [] as unknown[],
  status: 'reconnecting',
  secondsRemaining: 60 as number | null,
  limitAt: Date.now() + 60_000 as number | null,
  interimCaption: '',
  brief: null,
};

vi.mock('@/hooks/useCalls', () => ({
  useCall: () => ({
    data: {
      call_id: 'call_waiting_1',
      status: 'transferring',
      started_at: 1_000,
      transfer_requested_at: 1_010,
      locale: 'en',
      topic: 'paye',
      turns: [],
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCallLive: () => live,
  useCallBrief: () => ({ brief: null, loading: false, refresh: vi.fn(), refreshing: false }),
  useReviewCall: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));
vi.mock('@/hooks/useMyUserId', () => ({ useMyUserId: () => 'okello@ura.go.ug' }));

describe('CallWorkspace notices', () => {
  it('shows a paused stream and a near time limit on a waiting call whose transcript is collapsed', async () => {
    const { CallWorkspace } = await import('@/components/staff/calls/CallWorkspace');

    render(<CallWorkspace callId="call_waiting_1" role="officer" />);

    expect(screen.getByText('Caller waiting')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Transcript/ }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('Live transcript paused — reconnecting…')).toBeTruthy();
    expect(screen.getByText(/reaches its time limit in about a minute/)).toBeTruthy();
  });
});
