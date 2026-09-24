import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CallAlertStack } from '@/components/staff/calls/console/CallAlertStack';
import { callsApi, ClaimConflictError } from '@/services/callsApi';
import { resetSessionForTests } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';

const waiting = (id: string, extra: Record<string, unknown> = {}) =>
  useCallConsoleStore.getState().applyLobbyEvent({
    type: 'call.transfer_requested',
    data: { call_id: id, topic: 'customs', priority: 'high', language: 'sw', reason: 'caller_requested',
            ticket_ref: 'TKT-12345678', waiting_since: Date.now() / 1000 - 5, ...extra },
  });

describe('CallAlertStack', () => {
  beforeEach(() => {
    useCallConsoleStore.getState().reset();
    document.title = 'Tickets — URA Console';
  });
  afterEach(() => {
    resetSessionForTests();
    vi.restoreAllMocks();
  });

  it('shows a waiting caller to an available officer, with the tab badged', () => {
    waiting('c1');
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    expect(screen.getByText('Caller waiting for an officer')).toBeInTheDocument();
    expect(screen.getByText('Customs')).toBeInTheDocument();
    expect(screen.getByText(/Swahili · Asked for a person · Ticket TKT-1234/)).toBeInTheDocument();
    expect(document.title).toBe('(1) Caller waiting — Tickets — URA Console');
  });

  it('stays quiet for a busy officer and for auditors', () => {
    waiting('c1');
    useCallConsoleStore.getState().setAvailability('busy');
    const { rerender } = render(<CallAlertStack canTake onPreview={vi.fn()} />);
    expect(screen.queryByText('Caller waiting for an officer')).not.toBeInTheDocument();
    useCallConsoleStore.getState().setAvailability('available');
    rerender(<CallAlertStack canTake={false} onPreview={vi.fn()} />);
    expect(screen.queryByText('Caller waiting for an officer')).not.toBeInTheDocument();
  });

  it('collapses more than two waiting callers into one card', () => {
    waiting('c1');
    waiting('c2');
    waiting('c3');
    const onPreview = vi.fn();
    render(<CallAlertStack canTake onPreview={onPreview} />);
    expect(screen.getByText('3 callers waiting')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open queue' }));
    expect(onPreview).toHaveBeenCalled();
  });

  it('says who took it when another officer got there first', async () => {
    vi.spyOn(callsApi, 'claimCall').mockRejectedValue(new ClaimConflictError('taken', 'nakato', 'Officer Nakato'));
    waiting('c1');
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Take call' }));
    });
    expect(screen.getByText('Taken by Officer Nakato')).toBeInTheDocument();
  });

  it('turns into "Taken by …" when the lobby says someone claimed it', () => {
    waiting('c1');
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    act(() =>
      useCallConsoleStore.getState().applyLobbyEvent({
        type: 'call.claimed',
        data: { call_id: 'c1', officer_id: 'okello', officer_name: 'Officer Okello' },
      }),
    );
    expect(screen.getByText('Taken by Officer Okello')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Take call' })).not.toBeInTheDocument();
  });

  it('dismisses for me only, and previews without claiming', () => {
    waiting('c1');
    const onPreview = vi.fn();
    render(<CallAlertStack canTake onPreview={onPreview} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    expect(onPreview).toHaveBeenCalledWith('c1');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByText('Caller waiting for an officer')).not.toBeInTheDocument();
    expect(document.title).toMatch(/^\(1\) Caller waiting/); // still waiting, still counted
  });
});
