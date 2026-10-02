'use client';

import React, { useEffect, useRef } from 'react';
import { getAudioLevels } from '@/services/audioLevelBus';

const BARS = 5;

/**
 * Five bars for a live audio level — the officer's microphone or the caller.
 * Reads the level bus inside one requestAnimationFrame loop and writes a data
 * attribute on its own node, so a level change never re-renders React.
 */
export function LevelMeter({ source, label }: { source: 'input' | 'output'; label: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const level = getAudioLevels()[source];
      if (ref.current) ref.current.dataset.level = String(Math.min(BARS, Math.round(level * BARS)));
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [source]);
  return (
    <span className="cc-level" ref={ref} data-level="0" role="img" aria-label={label}>
      {Array.from({ length: BARS }, (_, i) => (
        <span key={i} className="cc-level-bar" data-bar={i + 1} />
      ))}
    </span>
  );
}
