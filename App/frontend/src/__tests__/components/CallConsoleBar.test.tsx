import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CallConsoleBar } from '@/components/staff/calls/console/CallConsoleBar';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

describe('CallConsoleBar', () => {
  beforeEach(() => {
    const store = useCallConsoleStore.getState();
    store.reset();
    store.applyLobbyEvent({ type: 'call.started', data: { call_id: 'ai_1', started_at: 1 } });
    store.applyLobbyEvent({ type: 'call.transfer_requested', data: { call_id: 'old', waiting_since: Date.now() / 1000 - 40 } });
    store.applyLobbyEvent({ type: 'call.transfer_requested', data: { call_id: 'new', waiting_since: Date.now() / 1000 - 5 } });
  });

  it('counts live and waiting calls, and opens the longest wait', () => {
    const onOpenCall = vi.fn();
    render(<CallConsoleBar canTake onOpenCall={onOpenCall} onCheckMic={vi.fn()} />);
    expect(screen.getByText(/Live/).textContent).toContain('3');
    fireEvent.click(screen.getByRole('button', { name: /Waiting 2/ }));
    expect(onOpenCall).toHaveBeenCalledWith('old');
  });

  it('sets my availability', () => {
    render(<CallConsoleBar canTake onOpenCall={vi.fn()} onCheckMic={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Availability', { selector: 'select' }), { target: { value: 'away' } });
    expect(useCallConsoleStore.getState().availability).toBe('away');
  });

  it('gives an auditor the counts and nothing to press', () => {
    render(<CallConsoleBar canTake={false} onOpenCall={vi.fn()} onCheckMic={vi.fn()} />);
    expect(screen.queryByLabelText('Availability', { selector: 'select' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /mic/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sound/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Waiting 2/ })).toBeInTheDocument();
  });

  it('shows "On call" instead of the menu while I am on one', () => {
    useCallConsoleStore.getState().setActiveCall({
      callId: 'old', state: 'bridged', since: Date.now(), muted: false, officerName: '', error: null,
    });
    render(<CallConsoleBar canTake onOpenCall={vi.fn()} onCheckMic={vi.fn()} />);
    expect(screen.getByText('On call')).toBeInTheDocument();
  });
});
