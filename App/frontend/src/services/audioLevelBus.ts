/**
 * Live audio levels for the call orb, kept deliberately outside React.
 *
 * The orb pulses with the caller's voice and with the assistant's reply, which
 * means a new value 30–50 times a second. Routing that through component state
 * would re-render the whole call sheet — transcript included — on every frame.
 * Instead the producers write here, and `CallOrb` reads it from inside one
 * requestAnimationFrame loop and writes a CSS variable straight onto its own
 * node. Nothing else in the tree ever hears about it.
 *
 * Both levels are normalised to 0..1 and decay on their own: a producer that
 * stops posting (the player between sentences, a muted microphone) fades to
 * silence instead of freezing the orb mid-pulse.
 */

const DECAY_AFTER_MS = 150;

interface LevelSlot {
  value: number;
  at: number;
}

const input: LevelSlot = { value: 0, at: 0 };
const output: LevelSlot = { value: 0, at: 0 };

function now(): number {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

/** Fade a stale reading toward zero so the orb settles when a source stops. */
function read(slot: LevelSlot): number {
  if (slot.value <= 0) return 0;
  const age = now() - slot.at;
  if (age <= 0) return slot.value;
  if (age >= DECAY_AFTER_MS) {
    slot.value = 0;
    return 0;
  }
  return slot.value * (1 - age / DECAY_AFTER_MS);
}

/** Caller microphone level, 0..1. */
export function setInputLevel(level: number): void {
  input.value = clamp01(level);
  input.at = now();
}

/** Assistant/officer playback level, 0..1. */
export function setOutputLevel(level: number): void {
  output.value = clamp01(level);
  output.at = now();
}

export function getAudioLevels(): { input: number; output: number } {
  return { input: read(input), output: read(output) };
}

/** Called when a call ends so the next call starts from silence. */
export function resetAudioLevels(): void {
  input.value = 0;
  input.at = 0;
  output.value = 0;
  output.at = 0;
}
