/**
 * Voice mode's dock: one named state at a time, the one control that answers
 * it, and nothing announced while the microphone is open.
 *
 * Voice mode gave no feedback between the end of a turn and the answer — and
 * none at all on the landing page. These pin what the dock now says in each
 * phase, so a phase cannot quietly lose its words or its control again.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import VoiceModeDock, { type VoiceDockPhase } from '@/components/VoiceModeDock';
import { pausePlayback, resumePlayback } from '@/services/voiceService';
import { useChatStore } from '@/store/useChatStore';

const narration = vi.hoisted(() => ({ value: { active: false, paused: false, sentence: '' } }));

vi.mock('@/services/voiceService', () => ({
  pausePlayback: vi.fn(async () => true),
  resumePlayback: vi.fn(async () => true),
  readPlaybackLevel: () => 0,
}));
vi.mock('@/hooks/useNarration', () => ({ useNarration: () => narration.value }));

function dock(props: Partial<React.ComponentProps<typeof VoiceModeDock>> = {}) {
  const handlers = { onTalk: vi.fn(), onSend: vi.fn(), onCancel: vi.fn(), onStop: vi.fn(), onEnd: vi.fn() };
  const utils = render(<VoiceModeDock phase="idle" autoSend {...handlers} {...props} />);
  return { ...handlers, ...utils };
}

const label = () => screen.getByTestId('voice-dock-label');
const detail = () => screen.getByTestId('voice-dock-detail');
const primary = () => screen.getByTestId('voice-dock-primary');
const announced = () => screen.getByRole('status');

describe('VoiceModeDock', () => {
  afterEach(() => {
    narration.value = { active: false, paused: false, sentence: '' };
    useChatStore.setState({ locale: 'en' });
    vi.clearAllMocks();
  });

  it('names every phase in words and gives its one control the right job', () => {
    const cases: Array<[VoiceDockPhase, string, string]> = [
      ['opening', 'Opening the microphone…', 'Opening the microphone'],
      ['listening', 'Listening', 'Send now'],
      ['hearing', 'Hearing you…', 'Send now'],
      ['pausing', 'Sending when you stop…', 'Send now'],
      ['transcribing', 'Got it', 'Stop'],
      ['thinking', 'Thinking', 'Stop'],
      ['preparing', 'Getting ready to speak…', 'Interrupt and talk'],
      ['speaking', 'Speaking', 'Interrupt and talk'],
      ['idle', 'Tap the mic to talk', 'Start talking'],
    ];
    for (const [phase, words, control] of cases) {
      const { unmount } = dock({ phase });
      expect(label()).toHaveTextContent(words);
      expect(primary()).toHaveAccessibleName(control);
      unmount();
    }
  });

  it('sends, stops or takes the floor with the same button, as the phase asks', () => {
    const hearing = dock({ phase: 'hearing' });
    fireEvent.click(primary());
    expect(hearing.onSend).toHaveBeenCalledTimes(1);
    hearing.unmount();

    const thinking = dock({ phase: 'thinking' });
    fireEvent.click(primary());
    expect(thinking.onStop).toHaveBeenCalledTimes(1);
    thinking.unmount();

    const speaking = dock({ phase: 'speaking' });
    fireEvent.click(primary());
    expect(speaking.onTalk).toHaveBeenCalledTimes(1);
    speaking.unmount();

    const idle = dock({ phase: 'idle' });
    fireEvent.click(primary());
    expect(idle.onTalk).toHaveBeenCalledTimes(1);
  });

  it('takes the focus the composer had, and keeps it while the mic opens', () => {
    const h = dock({ phase: 'opening' });
    // Not `disabled`: a disabled button cannot take the focus it is given, and
    // the dock mounts in exactly this phase.
    expect(primary()).toHaveAttribute('aria-disabled', 'true');
    expect(primary()).not.toBeDisabled();
    expect(document.activeElement).toBe(primary());
    fireEvent.click(primary());
    expect(h.onTalk).not.toHaveBeenCalled();
    expect(h.onSend).not.toHaveBeenCalled();
  });

  it('leaves the focus alone when the reader has put it somewhere else', () => {
    const elsewhere = document.createElement('button');
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    dock({ phase: 'listening' });
    expect(document.activeElement).toBe(elsewhere);
    elsewhere.remove();
  });

  it('draws the end-of-turn countdown only where a pause will send the turn', () => {
    const auto = dock({ phase: 'pausing', autoSend: true });
    expect(auto.container.querySelector('.voice-dock-ring')).not.toBeNull();
    expect(detail()).toHaveTextContent('Keep talking to add more.');
    auto.unmount();

    const tap = dock({ phase: 'hearing', autoSend: false });
    expect(tap.container.querySelector('.voice-dock-ring')).toBeNull();
    expect(detail()).toHaveTextContent('tap the tick to send');
  });

  it('says back what was heard while the answer is worked on, step by step', () => {
    dock({
      phase: 'thinking',
      heard: 'how do I register for a TIN',
      workingLabel: 'Searching the URA knowledge base',
    });
    expect(label()).toHaveTextContent('Searching the URA knowledge base');
    expect(detail()).toHaveTextContent('You said: “how do I register for a TIN”');
  });

  it('puts what went wrong where the hint was, and announces it', () => {
    dock({ phase: 'idle', notice: 'Didn’t catch that. Try again, or type your question.' });
    expect(detail()).toHaveTextContent('Didn’t catch that');
    expect(detail()).toHaveClass('is-notice');
    expect(announced()).toHaveTextContent('Tap the mic to talk. Didn’t catch that.');
  });

  it('announces nothing while the microphone is open — a screen reader would be recorded', () => {
    for (const phase of ['opening', 'listening', 'hearing', 'pausing'] as const) {
      const { unmount } = dock({ phase });
      expect(announced()).toBeEmptyDOMElement();
      unmount();
    }
  });

  it('announces the steps of an answer once, not once per step', () => {
    const { rerender, onTalk, onSend, onCancel, onStop, onEnd } = dock({ phase: 'transcribing' });
    expect(announced()).toHaveTextContent('Got it');
    const handlers = { onTalk, onSend, onCancel, onStop, onEnd };
    rerender(<VoiceModeDock phase="thinking" workingLabel="Thinking" autoSend {...handlers} />);
    expect(announced()).toHaveTextContent('Thinking');
    rerender(<VoiceModeDock phase="thinking" workingLabel="Searching the URA knowledge base" autoSend {...handlers} />);
    expect(label()).toHaveTextContent('Searching the URA knowledge base');
    expect(announced()).toHaveTextContent('Thinking');
  });

  it('shows the sentence being read without reading it out twice, and pauses it', () => {
    narration.value = { active: true, paused: false, sentence: 'VAT is 18 percent.' };
    dock({ phase: 'speaking' });
    expect(detail()).toHaveTextContent('VAT is 18 percent.');
    expect(detail()).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Pause reading' }));
    expect(pausePlayback).toHaveBeenCalledTimes(1);
  });

  it('knows a paused reading from the player, and resumes it', () => {
    narration.value = { active: true, paused: true, sentence: 'VAT is 18 percent.' };
    dock({ phase: 'speaking' });
    expect(label()).toHaveTextContent('Paused');
    expect(screen.getByTestId('voice-dock')).toHaveAttribute('data-phase', 'paused');
    fireEvent.click(screen.getByRole('button', { name: 'Resume reading' }));
    expect(resumePlayback).toHaveBeenCalledTimes(1);
  });

  it('always has a way out, and a mic-off that sends nothing', () => {
    const h = dock({ phase: 'listening' });
    fireEvent.click(screen.getByRole('button', { name: /Turn the microphone off/ }));
    expect(h.onCancel).toHaveBeenCalledTimes(1);
    expect(h.onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'End voice mode' }));
    expect(h.onEnd).toHaveBeenCalledTimes(1);
  });

  it('colours the orb by who has the floor', () => {
    const floors: Array<[VoiceDockPhase, string]> = [
      ['listening', 'listening'],
      ['pausing', 'listening'],
      ['transcribing', 'thinking'],
      ['thinking', 'thinking'],
      ['speaking', 'speaking'],
      ['idle', 'idle'],
    ];
    for (const [phase, floor] of floors) {
      const { container, unmount } = dock({ phase });
      expect(container.querySelector('.call-orb')).toHaveAttribute('data-state', floor);
      expect(screen.getByTestId('voice-dock')).toHaveAttribute('data-floor', floor);
      unmount();
    }
  });

  it('speaks the reader’s language', () => {
    useChatStore.setState({ locale: 'sw' });
    dock({ phase: 'listening' });
    expect(label()).toHaveTextContent('Ninasikiliza');
    expect(screen.getByRole('button', { name: 'Toka hali ya sauti' })).toBeInTheDocument();
  });
});
