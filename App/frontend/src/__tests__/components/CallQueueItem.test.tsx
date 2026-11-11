import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CallQueueItem } from '@/components/staff/calls/CallQueueItem';
import { lobbyCall } from '../helpers/callConsole';

const reserved = () =>
  lobbyCall({
    reserved_for: 'nakato',
    reserved_name: 'Officer Nakato',
    reserved_until: Date.now() / 1000 + 20,
    from_officer: 'Officer Okello',
  });

describe('CallQueueItem', () => {
  it('shows a waiting call as waiting', () => {
    render(<ul><CallQueueItem call={lobbyCall()} role="waiting" selected={false} myUserId="mukasa" onSelect={vi.fn()} /></ul>);
    expect(screen.getByText('Waiting')).toBeInTheDocument();
  });

  it('says who a passed-on call is reserved for', () => {
    render(<ul><CallQueueItem call={reserved()} role="waiting" selected={false} myUserId="mukasa" onSelect={vi.fn()} /></ul>);
    expect(screen.getByText('Reserved for Officer Nakato')).toBeInTheDocument();
    expect(screen.queryByText('Waiting')).not.toBeInTheDocument();
  });

  it('tells the named officer it was passed to them', () => {
    render(<ul><CallQueueItem call={reserved()} role="waiting" selected={false} myUserId="nakato" onSelect={vi.fn()} /></ul>);
    expect(screen.getByText('Passed to you')).toBeInTheDocument();
  });

  it('shows a reservation that has run out as an ordinary wait', () => {
    const lapsed = { ...reserved(), reserved_until: Date.now() / 1000 - 1 };
    render(<ul><CallQueueItem call={lapsed} role="waiting" selected={false} myUserId="mukasa" onSelect={vi.fn()} /></ul>);
    expect(screen.getByText('Waiting')).toBeInTheDocument();
  });
});
