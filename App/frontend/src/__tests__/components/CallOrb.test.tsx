/**
 * The live orb, shared by the call dock and the chat's voice mode.
 *
 * The call lets the loudest voice decide who is talking; voice mode tells the
 * orb who has the floor (a quiet open mic is still listening) and where its
 * levels come from. Both must keep working from the one component.
 */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';

import { CallOrb } from '@/components/call/CallOrb';

/** Animation frames that run only when the test says so. */
function manualFrames() {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
  return () => frames.shift()?.(0);
}

describe('CallOrb', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('follows the levels it is given and the floor it is told', () => {
    const step = manualFrames();
    const readLevels = vi.fn(() => ({ input: 0, output: 0.8 }));
    const { container } = render(<CallOrb state="speaking" readLevels={readLevels} />);
    step();
    const orb = container.querySelector('.call-orb') as HTMLElement;
    expect(readLevels).toHaveBeenCalled();
    expect(orb).toHaveAttribute('data-state', 'speaking');
    expect(orb).toHaveAttribute('data-mode', 'speaking');
    expect(Number(orb.style.getPropertyValue('--orb-level'))).toBeGreaterThan(0);
  });

  it('leaves the floor to the levels when nobody says (the call)', () => {
    const { container } = render(<CallOrb />);
    expect(container.querySelector('.call-orb')).not.toHaveAttribute('data-state');
  });

  it('rests while inactive: no reading, and the level back at zero', () => {
    const step = manualFrames();
    const readLevels = vi.fn(() => ({ input: 0.9, output: 0 }));
    const { container } = render(<CallOrb active={false} state="idle" readLevels={readLevels} />);
    step();
    expect(readLevels).not.toHaveBeenCalled();
    expect((container.querySelector('.call-orb') as HTMLElement).style.getPropertyValue('--orb-level')).toBe('0');
  });
});
