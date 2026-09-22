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

  it('renders consent dialog when opened', () => {
    useCallStore.getState().openCall();
    render(<CallScreen />);

    expect(screen.getByRole('dialog')).toBeDefined();
    expect(screen.getByText('Call Recording & Transcription Consent')).toBeDefined();
    expect(screen.getByText('Accept & Call')).toBeDefined();
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

    expect(screen.getAllByText('URA Virtual Assistant').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('How can I assist you with your taxes today?')).toBeDefined();
    expect(screen.getByLabelText('Mute')).toBeDefined();
    expect(screen.getByLabelText('Talk to an officer')).toBeDefined();
    expect(screen.getByLabelText('End call')).toBeDefined();
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

    expect(screen.getByText('Connecting you to an officer…')).toBeDefined();
    expect(screen.getByText('Ticket: TICK-1234')).toBeDefined();
  });

  it('displays bridged officer state when officer joins', () => {
    useCallStore.getState().openCall();
    useCallStore.getState().setStatus('officer');
    useCallStore.getState().setOfficerName('Sarah');

    render(<CallScreen />);

    expect(screen.getByText('Officer Sarah')).toBeDefined();
    expect(screen.getByText('Live Audio Bridge Active')).toBeDefined();
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
