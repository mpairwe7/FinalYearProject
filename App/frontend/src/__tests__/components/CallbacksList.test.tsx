import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CallbacksList } from '@/components/staff/calls/CallbacksList';
import type { CallRecord } from '@/services/callsApi';

function owed(callId: string, reason: string, startedAt: number): CallRecord {
  return {
    call_id: callId,
    conversation_id: `conv_${callId}`,
    user_id: 'taxpayer',
    tenant_id: 'default',
    channel: 'browser_sim',
    locale: 'en',
    status: 'ended',
    started_at: startedAt,
    ended_at: startedAt + 60,
    end_reason: 'caller_hangup',
    transferred: 1,
    transfer_reason: 'caller_requested',
    ticket_id: null,
    officer_id: null,
    officer_rating: null,
    officer_note: null,
    needs_callback: 1,
    callback_reason: reason,
  };
}

describe('CallbacksList', () => {
  it('says why each callback is owed, the after-hours ones included (G180, G186, G195)', () => {
    const now = Date.now() / 1000;
    render(
      <CallbacksList
        calls={[
          owed('call_a', 'outside_working_hours', now - 300),
          owed('call_b', 'no_officer_online', now - 200),
          owed('call_c', 'caller_requested_callback', now - 100),
        ]}
        onRefresh={vi.fn()}
        onOpenCall={vi.fn()}
      />,
    );
    expect(screen.getByText("Called outside officers' hours")).toBeDefined();
    expect(screen.getByText('No officer was signed in')).toBeDefined();
    expect(screen.getByText('Caller asked to be called back')).toBeDefined();
  });
});
