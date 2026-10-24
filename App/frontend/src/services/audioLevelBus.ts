/**
 * Live audio levels for the call orb and the chat's voice mode, kept
 * deliberately outside React.
 *
 * The orb pulses with the caller's voice and with the assistant's reply, which
 * means a new value 30–50 times a second. Routing that through component state
 * would re-render the whole call sheet — transcript included — on every frame.
 * Instead the producers write here, and `CallOrb` reads it from inside one
 * requestAnimationFrame loop and writes a CSS variable straight onto its own
 * node. Nothing else in the tree ever hears about it.
 *
 * Voice mode's end-of-turn countdown lives here for the same reason: how far a
 * pause has gone towards sending the turn changes every 50 ms, and only the
 * ring drawn around the send button needs to know.
 *
 * Every value is normalised to 0..1 and lapses on its own: a producer that
 * stops posting (the player between sentences, a muted microphone, a turn that
 * has been sent) goes back to nothing instead of freezing mid-pulse.
 */

const DECAY_AFTER_MS = 150;
/** The countdown is read every 50 ms; four missed readings mean its turn is over. */
const PAUSE_STALE_MS = 200;

interface LevelSlot {
  value: number;
  at: number;
}

const input: LevelSlot = { value: 0, at: 0 };
const output: LevelSlot = { value: 0, at: 0 };
const pause: LevelSlot = { value: 0, at: 0 };

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

/** How far the speaker's pause has gone towards sending the turn, 0..1 (voice mode). */
export function setPauseProgress(progress: number): void {
  pause.value = clamp01(progress);
  pause.at = now();
}

/**
 * Held between readings rather than faded like the levels: a countdown that
 * sagged between two 50 ms readings would visibly jump back and forth.
 */
export function getPauseProgress(): number {
  if (pause.value <= 0 || now() - pause.at > PAUSE_STALE_MS) return 0;
  return pause.value;
}

/** Called when a call or a voice-mode session ends, so the next starts from silence. */
export function resetAudioLevels(): void {
  input.value = 0;
  input.at = 0;
  output.value = 0;
  output.at = 0;
  pause.value = 0;
  pause.at = 0;
}
