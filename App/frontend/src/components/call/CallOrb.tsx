'use client';

import React, { useEffect, useRef } from 'react';
import { getAudioLevels } from '@/services/audioLevelBus';
import '@/styles/call/orb.css';

type OrbMode = 'idle' | 'listening' | 'speaking';

/**
 * Who has the floor, from a caller that knows better than the levels do.
 *
 * The call leaves it out: there, the voice that is loudest is the one talking.
 * Voice mode in the chat cannot work that way — a microphone that is open in a
 * quiet room is still listening, and a reply that is still being worked on
 * makes no sound at all — so it says which it is, and the orb's colour follows
 * that while its size still follows the voice.
 */
export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface OrbLevels {
  input: number;
  output: number;
}

interface CallOrbProps {
  /** Pause the animation loop when the call is not carrying audio. */
  active?: boolean;
  label?: string;
  /** Who has the floor; omitted, the levels decide (the call). */
  state?: OrbState;
  /**
   * Where the levels are read from, every frame — the shared bus unless the
   * caller has its own. Must be a stable function: the loop restarts on change.
   */
  readLevels?: () => OrbLevels;
}

/**
 * The live orb at the centre of the call dock, and of the chat's voice mode.
 *
 * It reads the audio levels inside a single animation frame loop and writes
 * them onto its own node as a CSS variable, so a voice that changes 50 times a
 * second never re-renders the transcript above it. The mode (who is talking)
 * is a data attribute rather than React state for the same reason.
 *
 * `smoothed` is an exponential follower: raw peaks jitter enough to make the
 * orb twitch, and easing in fast / out slow reads as breathing rather than
 * strobing.
 */
export function CallOrb({ active = true, label, state, readLevels = getAudioLevels }: CallOrbProps) {
  const orbRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef<number | null>(null);

  useEffect(() => {
    const node = orbRef.current;
    if (!node) return;

    const reducedMotion =
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (!active || reducedMotion || typeof requestAnimationFrame !== 'function') {
      node.style.setProperty('--orb-level', '0');
      node.dataset.mode = 'idle';
      return;
    }

    let smoothed = 0;
    let mode: OrbMode = 'idle';

    const tick = () => {
      const { input, output } = readLevels();
      // Playback wins ties: while the assistant speaks, the microphone is
      // still picking up that same voice through the speakers, and an orb that
      // flickered to "listening" on echo would misreport who has the floor.
      const speaking = output >= 0.06 && output >= input;
      const listening = !speaking && input >= 0.06;
      const target = speaking ? output : listening ? input : 0;

      smoothed += (target - smoothed) * (target > smoothed ? 0.45 : 0.12);
      if (smoothed < 0.002) smoothed = 0;

      const nextMode: OrbMode = speaking ? 'speaking' : listening ? 'listening' : 'idle';
      if (nextMode !== mode) {
        mode = nextMode;
        node.dataset.mode = mode;
      }
      node.style.setProperty('--orb-level', smoothed.toFixed(3));

      frameRef.current = requestAnimationFrame(tick);
    };

    frameRef.current = requestAnimationFrame(tick);

    return () => {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      node.style.setProperty('--orb-level', '0');
      node.dataset.mode = 'idle';
    };
  }, [active, readLevels]);

  return (
    <div className="call-orb-wrap" aria-hidden={label ? undefined : true}>
      <div
        ref={orbRef}
        className="call-orb"
        data-mode="idle"
        data-state={state}
        role={label ? 'img' : undefined}
        aria-label={label}
      >
        <span className="call-orb-core" />
        <span className="call-orb-halo" />
      </div>
    </div>
  );
}
