/**
 * Synthetic telephony audio tones using WebAudio OscillatorNode.
 */

export function playDialTone(ctx: AudioContext): () => void {
  if (ctx.state === 'suspended') {
    ctx.resume().catch(() => {});
  }

  const now = ctx.currentTime;
  const osc1 = ctx.createOscillator();
  const osc2 = ctx.createOscillator();
  const gain = ctx.createGain();

  osc1.frequency.value = 440;
  osc2.frequency.value = 480;

  osc1.connect(gain);
  osc2.connect(gain);
  gain.connect(ctx.destination);

  // Cadence: 1.5s tone, 2s silence, 1.5s tone...
  gain.gain.setValueAtTime(0, now);
  // Pulse 1
  gain.gain.linearRampToValueAtTime(0.08, now + 0.05);
  gain.gain.setValueAtTime(0.08, now + 1.2);
  gain.gain.linearRampToValueAtTime(0, now + 1.25);
  // Pulse 2
  gain.gain.setValueAtTime(0, now + 2.5);
  gain.gain.linearRampToValueAtTime(0.08, now + 2.55);
  gain.gain.setValueAtTime(0.08, now + 3.8);
  gain.gain.linearRampToValueAtTime(0, now + 3.85);

  osc1.start(now);
  osc2.start(now);

  osc1.stop(now + 4.0);
  osc2.stop(now + 4.0);

  return () => {
    try {
      osc1.stop();
      osc2.stop();
      osc1.disconnect();
      osc2.disconnect();
      gain.disconnect();
    } catch {}
  };
}

export function playJoinChime(ctx: AudioContext): void {
  if (ctx.state === 'suspended') {
    ctx.resume().catch(() => {});
  }

  const now = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();

  osc.connect(gain);
  gain.connect(ctx.destination);

  // Two-note rising chime (C5 -> E5)
  osc.frequency.setValueAtTime(523.25, now);
  osc.frequency.setValueAtTime(659.25, now + 0.15);

  gain.gain.setValueAtTime(0.12, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);

  osc.start(now);
  osc.stop(now + 0.55);
}

/**
 * A caller is waiting for an officer: two short, bright notes, once. Quiet
 * enough to sit under a conversation, distinct from the join chime.
 */
export function playAlertChime(ctx: AudioContext): void {
  if (ctx.state === 'suspended') {
    ctx.resume().catch(() => {});
  }
  const now = ctx.currentTime;
  [880, 1174.66].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.value = freq;
    osc.connect(gain);
    gain.connect(ctx.destination);
    const start = now + i * 0.18;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.09, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.3);
    osc.start(start);
    osc.stop(start + 0.32);
  });
}
