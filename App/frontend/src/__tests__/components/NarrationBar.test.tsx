import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import NarrationBar from '@/components/NarrationBar';
import { pausePlayback, resumePlayback } from '@/services/voiceService';
import { useChatStore } from '@/store/useChatStore';

const state = vi.hoisted(() => ({ narration: { active: true, paused: false, sentence: 'VAT is 18 percent.' } }));

vi.mock('@/services/voiceService', () => ({
  pausePlayback: vi.fn(async () => {}),
  resumePlayback: vi.fn(async () => {}),
}));
vi.mock('@/hooks/useNarration', () => ({ useNarration: () => state.narration }));

function bar(props: Partial<React.ComponentProps<typeof NarrationBar>> = {}) {
  const handlers = { onStop: vi.fn(), onRetry: vi.fn(), onDismiss: vi.fn() };
  render(<NarrationBar active loading={false} failed={false} {...handlers} {...props} />);
  return handlers;
}

describe('NarrationBar (G200, G202)', () => {
  afterEach(() => {
    state.narration = { active: true, paused: false, sentence: 'VAT is 18 percent.' };
    useChatStore.setState({ locale: 'en' });
    vi.clearAllMocks();
  });

  it('is not there when nothing is read', () => {
    const { container } = render(
      <NarrationBar active={false} loading={false} failed={false} onStop={vi.fn()} onRetry={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('pauses, shows the sentence being heard, and stops (also Escape)', () => {
    const { onStop } = bar();
    expect(screen.getByRole('region', { name: 'Read-aloud controls' })).toHaveTextContent('Reading the reply aloud');
    expect(screen.getByText('VAT is 18 percent.')).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Pause reading' }));
    expect(pausePlayback).toHaveBeenCalledTimes(1);
    const stop = screen.getByRole('button', { name: 'Stop reading' });
    expect(stop).toHaveAttribute('aria-keyshortcuts', 'Escape');
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('resumes when paused', () => {
    state.narration = { active: true, paused: true, sentence: 'VAT is 18 percent.' };
    bar();
    expect(screen.getByRole('region')).toHaveTextContent('Paused');
    fireEvent.click(screen.getByRole('button', { name: 'Resume reading' }));
    expect(resumePlayback).toHaveBeenCalledTimes(1);
  });

  it('has nothing to pause while the audio is still coming', () => {
    bar({ loading: true });
    expect(screen.getByRole('region')).toHaveTextContent('Getting the audio…');
    expect(screen.getByRole('button', { name: 'Pause reading' })).toBeDisabled();
  });

  it('says when a reply could not be read aloud, with a retry', () => {
    const { onRetry, onDismiss } = bar({ active: false, failed: true });
    expect(screen.getByRole('status')).toHaveTextContent('Couldn’t read this aloud. The answer is on screen.');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('speaks the reader’s language', () => {
    useChatStore.setState({ locale: 'sw' });
    bar();
    expect(screen.getByRole('region')).toHaveTextContent('Ninasoma jibu kwa sauti');
  });
});
