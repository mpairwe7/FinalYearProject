import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CallBriefCard } from '@/components/staff/calls/CallBriefCard';
import { brief } from '../helpers/callConsole';

const props = { loading: false, refreshing: false, onRefresh: vi.fn() };

describe('CallBriefCard', () => {
  it('lays the brief out for a ten-second read', () => {
    render(<CallBriefCard {...props} brief={brief()} onEvidence={vi.fn()} />);
    expect(screen.getByText('Why they need you')).toBeInTheDocument();
    expect(screen.getByText(/Object to a tax assessment/)).toBeInTheDocument();
    expect(screen.getByText(/TIN \(given \(redacted\)\)/)).toBeInTheDocument();
    expect(screen.getByText('Frustrated')).toBeInTheDocument();
    expect(screen.getByText(/covers 3 turns · gemini-2.5-flash-lite/)).toBeInTheDocument();
  });

  it('takes the officer to the turn a line rests on', () => {
    const onEvidence = vi.fn();
    render(<CallBriefCard {...props} brief={brief()} onEvidence={onEvidence} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Show turn 3 in the transcript' })[0]);
    expect(onEvidence).toHaveBeenCalledWith(3);
  });

  it('says when no model wrote it', () => {
    render(<CallBriefCard {...props} brief={brief({ fallback: true, model: 'fallback' })} onEvidence={vi.fn()} />);
    expect(screen.getByText(/no model answered — check the transcript/)).toBeInTheDocument();
  });

  it('is one line on a call already under way, and expands', () => {
    render(<CallBriefCard {...props} brief={brief()} onEvidence={vi.fn()} compact />);
    expect(screen.queryByText('Why they need you')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(screen.getByText('Why they need you')).toBeInTheDocument();
  });

  it('says it is being written before the first one lands', () => {
    render(<CallBriefCard {...props} brief={null} loading onEvidence={vi.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent('Writing the brief…');
  });
});
