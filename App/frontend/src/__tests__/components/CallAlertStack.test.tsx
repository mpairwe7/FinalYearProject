import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CallAlertStack } from '@/components/staff/calls/console/CallAlertStack';
import { transferReasonLabel } from '@/components/staff/calls/console/CallAlertToast';
import { callsApi, ClaimConflictError } from '@/services/callsApi';
import { resetSessionForTests } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { clearAuthToken, setAuthToken } from '@/lib/authSession';

/** A token whose `sub` is *userId*. Unsigned: the console only reads who it says it is. */
function signInAs(userId: string): void {
  const part = (value: object) => btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  setAuthToken(`${part({ alg: 'none' })}.${part({ sub: userId, exp: Math.floor(Date.now() / 1000) + 600 })}.sig`);
}

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
    clearAuthToken();
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

  it('does not ring for a call a colleague was named to take', () => {
    signInAs('mukasa');
    waiting('c1', {
      reserved_for: 'nakato', reserved_name: 'Officer Nakato', reserved_until: Date.now() / 1000 + 20,
      from_officer: 'Officer Okello',
    });
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    expect(screen.queryByText('Caller waiting for an officer')).not.toBeInTheDocument();
    // Open to everyone once the named officer has not taken it.
    act(() =>
      useCallConsoleStore.getState().applyLobbyEvent({ type: 'call.unreserved', data: { call_id: 'c1', reason: 'not_taken' } }),
    );
    expect(screen.getByText('Caller waiting for an officer')).toBeInTheDocument();
  });

  it('tells the named officer the call was passed to them, and by whom', () => {
    signInAs('nakato');
    waiting('c1', {
      reserved_for: 'nakato', reserved_name: 'Officer Nakato', reserved_until: Date.now() / 1000 + 20,
      from_officer: 'Officer Okello',
    });
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    expect(screen.getByText('Passed to you by Officer Okello')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Take call' })).toBeInTheDocument();
  });

  it('stops saying a call was passed to me once the reservation has run out', () => {
    signInAs('nakato');
    // Its `call.unreserved` event was missed: the hold has lapsed all the same.
    waiting('c1', {
      reserved_for: 'nakato', reserved_name: 'Officer Nakato', reserved_until: Date.now() / 1000 - 1,
      from_officer: 'Officer Okello', attempt: 2,
    });
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    expect(screen.queryByText('Passed to you by Officer Okello')).not.toBeInTheDocument();
    expect(screen.getByText(/Passed on by an officer/)).toBeInTheDocument();
  });

  it('says a call is reserved when that is why the claim was refused', async () => {
    vi.spyOn(callsApi, 'claimCall').mockRejectedValue(
      new ClaimConflictError('This call is reserved for another officer', '', 'Officer Nakato', 'nakato'),
    );
    waiting('c1');
    render(<CallAlertStack canTake onPreview={vi.fn()} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Take call' }));
    });
    expect(screen.getByText('Reserved for Officer Nakato')).toBeInTheDocument();
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

describe('transferReasonLabel', () => {
  it('says why the AI handed the caller over', () => {
    expect(transferReasonLabel('officer_takeover')).toBe('Taken over from the AI');
    expect(transferReasonLabel('offer_accepted')).toBe('Said yes when the AI offered an officer');
    expect(transferReasonLabel('safety_concern')).toBe('Safety concern: crisis lines given');
    expect(transferReasonLabel('caller_requested')).toBe('Asked for a person');
    expect(transferReasonLabel('no_knowledge_match')).toBe('The AI could not answer');
    expect(transferReasonLabel('tax_dispute')).toBe('A tax dispute');
    expect(transferReasonLabel('')).toBe('Asked for an officer');
  });
});
