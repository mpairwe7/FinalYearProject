import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CallScreen } from '@/components/call/CallScreen';
import { useCallStore } from '@/store/useCallStore';

describe('CallScreen component', () => {
  beforeEach(() => {
    useCallStore.getState().reset();

    // Stub AudioContext and Web APIs
    const mockAudioContext = vi.fn().mockImplementation(() => ({
      state: 'running',
      currentTime: 0,
      createOscillator: vi.fn().mockReturnValue({
        frequency: { setValueAtTime: vi.fn(), value: 440 },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
        disconnect: vi.fn(),
      }),
      createGain: vi.fn().mockReturnValue({
        gain: {
          setValueAtTime: vi.fn(),
          linearRampToValueAtTime: vi.fn(),
          exponentialRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
        disconnect: vi.fn(),
      }),
      destination: {},
      resume: vi.fn().mockResolvedValue(undefined),
      close: vi.fn().mockResolvedValue(undefined),
      audioWorklet: {
        addModule: vi.fn().mockResolvedValue(undefined),
      },
    }));

    Object.defineProperty(globalThis, 'AudioContext', { value: mockAudioContext, writable: true });
    Object.defineProperty(globalThis, 'webkitAudioContext', { value: mockAudioContext, writable: true });
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
      writable: true,
      configurable: true,
    });
  });

  it('renders nothing when closed', () => {
    const { container } = render(<CallScreen />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the pre-call card when opened', () => {
    useCallStore.getState().openCall();
    render(<CallScreen />);

    expect(screen.getByRole('dialog')).toBeDefined();
    expect(screen.getByText('0800 117 000 · Toll-free')).toBeDefined();
    expect(
      screen.getByText('Calls are transcribed so an officer can pick up with full context.'),
    ).toBeDefined();
    // Decline sits on the left of Call URA, so the safe choice is the one the
    // thumb reaches first rather than the one it lands on by accident.
    const actions = screen.getByText('Decline').parentElement;
    const labels = Array.from(actions?.children ?? []).map((el) => el.textContent);
    expect(labels).toEqual(['Decline', 'Call URA']);
  });

  it('allows declining consent to close call', () => {
    useCallStore.getState().openCall();
    render(<CallScreen />);

    const declineBtn = screen.getByText('Decline');
    fireEvent.click(declineBtn);

    expect(useCallStore.getState().isOpen).toBe(false);
  });

  it('renders active AI call state with controls and captions', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ai');
    useCallStore.getState().addCaption({
      speaker: 'assistant',
      text: 'How can I assist you with your taxes today?',
    });

    render(<CallScreen />);

    expect(screen.getByText('Connected')).toBeDefined();
    expect(screen.getByText('How can I assist you with your taxes today?')).toBeDefined();
    expect(screen.getByLabelText('Mute')).toBeDefined();
    expect(screen.getByLabelText('Talk to an officer')).toBeDefined();
    expect(screen.getByLabelText('End call')).toBeDefined();
    // The persona block is gone: the conversation is the screen now.
    expect(screen.queryByText('URA Virtual Assistant')).toBeNull();
  });

  it('toggles mute button state', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ai');

    render(<CallScreen />);

    const muteBtn = screen.getByLabelText('Mute');
    fireEvent.click(muteBtn);
    expect(useCallStore.getState().isMuted).toBe(true);

    const unmuteBtn = screen.getByLabelText('Unmute');
    fireEvent.click(unmuteBtn);
    expect(useCallStore.getState().isMuted).toBe(false);
  });

  it('displays transferring state when officer requested', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('transferring');
    useCallStore.getState().setTicketRef('TICK-1234');

    render(<CallScreen />);

    expect(screen.getAllByText('Connecting you to an officer…').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Ticket: TICK-1234')).toBeDefined();
  });

  it('displays bridged officer state when officer joins', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('officer');
    useCallStore.getState().setOfficerName('Sarah');

    render(<CallScreen />);

    expect(screen.getByText('Officer Sarah')).toBeDefined();
    expect(screen.getByText('Live audio bridge active')).toBeDefined();
  });

  it('keeps the whole conversation on screen as a chat thread', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ai');
    useCallStore.getState().addCaption({ speaker: 'assistant', text: 'How can I help you?', final: true });
    useCallStore.getState().addCaption({ speaker: 'caller', text: 'What is the VAT rate?', final: true });
    useCallStore.getState().addCaption({ speaker: 'assistant', text: 'VAT is 18%.', final: true });

    render(<CallScreen />);

    // The earlier turns are still there, not replaced by the newest caption.
    expect(screen.getByText('How can I help you?')).toBeDefined();
    expect(screen.getByText('What is the VAT rate?')).toBeDefined();
    expect(screen.getByText('VAT is 18%.')).toBeDefined();
    expect(screen.getByRole('log')).toBeDefined();
    // The caller's own words read as chat bubbles; the assistant's are plain
    // transcript text, so only the caller turn carries the bubble class.
    const callerTurn = screen.getByText('What is the VAT rate?');
    expect(callerTurn.className).toContain('call-bubble');
    expect(screen.getByText('VAT is 18%.').className).toContain('call-say');
  });

  it('revises the caller interim caption in place, then keeps the final', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ai');
    useCallStore.getState().addCaption({ speaker: 'caller', text: 'what is the', final: false });
    useCallStore.getState().addCaption({ speaker: 'caller', text: 'what is the VAT', final: false });

    const { rerender } = render(<CallScreen />);

    expect(useCallStore.getState().captions.length).toBe(1);
    expect(screen.queryByText('what is the')).toBeNull();
    expect(screen.getByText('what is the VAT')).toBeDefined();

    useCallStore.getState().addCaption({ speaker: 'caller', text: 'What is the VAT rate?', final: true });
    rerender(<CallScreen />);

    expect(useCallStore.getState().captions.length).toBe(1);
    expect(screen.getByText('What is the VAT rate?')).toBeDefined();
  });

  it('offers a jump-to-latest control once the caller scrolls back', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ai');
    useCallStore.getState().addCaption({ speaker: 'assistant', text: 'VAT is 18%.', final: true });

    render(<CallScreen />);

    const thread = screen.getByRole('log');
    // jsdom reports every box as zero-sized, so the scroll geometry the pin
    // logic reads has to be supplied by hand.
    Object.defineProperty(thread, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(thread, 'clientHeight', { value: 300, configurable: true });
    thread.scrollTop = 120;
    fireEvent.scroll(thread);

    const jump = screen.getByText('Jump to latest');
    fireEvent.click(jump);

    expect(screen.queryByText('Jump to latest')).toBeNull();
  });

  it('shows ended screen with thank you note', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('ended');

    render(<CallScreen />);

    expect(screen.getByText('Call ended')).toBeDefined();
    expect(screen.getAllByText('Thank you for calling URA.').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Close')).toBeDefined();
  });
});
