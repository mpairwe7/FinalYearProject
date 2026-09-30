/**
 * Voice mode's end of turn: has the speaker finished?
 *
 * Decided from the microphone's level, frame by frame, so voice mode can send
 * a turn when the speaker pauses instead of waiting for a tap. Voice agents end
 * a turn after 0.8–1.2 s of silence: shorter cuts people off while they think,
 * longer feels like nobody is listening.
 *
 * Speech is sound well above the room's own level. The floor is the quiet end
 * of the last few seconds, so it follows the room: a fan or a busy street does
 * not count as talking. A turn starts after `minSpeechMs` of speech, so a
 * click or a cough does not start one. This is a level detector, not a speech
 * model: a loud noise that lasts can still pass for speech. Tapping to send
 * works as before.
 */

export type TurnEvent =
  /** The speaker has started talking. */
  | 'speech'
  /** They have been quiet for `silenceMs` after speaking: send the turn. */
  | 'end'
  /** Nothing at all was heard within `noSpeechMs`: close the mic, send nothing. */
  | 'no-speech'
  /** The turn reached `maxTurnMs`: send what there is. */
  | 'too-long';

export interface EndOfTurnOptions {
  /** Quiet after speech that ends the turn, in ms. */
  silenceMs: number;
  /** Speech a turn needs before a pause can end it. */
  minSpeechMs?: number;
  /** With nothing heard for this long, the mic closes. */
  noSpeechMs?: number;
  /** A turn ends here whatever is happening. */
  maxTurnMs?: number;
}

/** Speech starts this far above the room's level (dB). */
const ONSET_DB = 12;
/** Once started, sound within this much of the onset level still counts (dB). */
const HOLD_DB = 6;
/** Nothing quieter than this is ever speech, however still the room (dBFS). */
const MIN_SPEECH_DBFS = -50;
/** Levels below this are silence; it keeps log10(0) out of the arithmetic. */
const SILENT_DBFS = -100;
/** The floor is this quantile of the levels in the last `FLOOR_WINDOW_MS`. */
const FLOOR_QUANTILE = 0.1;
const FLOOR_WINDOW_MS = 4000;
/** No turn can start before the floor has this much of the room to go on. */
const CALIBRATION_MS = 300;
/** A burst of speech survives dips this long, the gaps between words. */
const GAP_MS = 250;
/** Unbroken sound this long means something was heard; a click or a beep is shorter. */
const HEARD_MS = 150;
/** A timer in a background tab can fire late; one late frame counts no longer than this. */
const MAX_FRAME_MS = 250;

/** Level of `samples` (−1…1) in dB relative to full scale. */
export function levelDbfs(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = samples.length ? Math.sqrt(sum / samples.length) : 0;
  return rms > 0 ? Math.max(SILENT_DBFS, 20 * Math.log10(rms)) : SILENT_DBFS;
}

export class EndOfTurnDetector {
  private readonly silenceMs: number;
  private readonly minSpeechMs: number;
  private readonly noSpeechMs: number;
  private readonly maxTurnMs: number;
  private readonly recent: Array<{ at: number; db: number }> = [];
  private startedAt: number | null = null;
  private lastAt: number | null = null;
  private lastSoundAt = 0;
  /** The burst of sound that may be the start of speech, and how much of it sounded. */
  private burstStart: number | null = null;
  private burstMs = 0;
  /** The current run of unbroken sound, and whether any run was long enough to be heard. */
  private soundRunMs = 0;
  private heard = false;
  private speaking = false;
  private finished = false;

  constructor(opts: EndOfTurnOptions) {
    this.silenceMs = opts.silenceMs;
    this.minSpeechMs = opts.minSpeechMs ?? 300;
    this.noSpeechMs = opts.noSpeechMs ?? 8000;
    this.maxTurnMs = opts.maxTurnMs ?? 60_000;
  }

  /** The speaker has started talking. */
  get hasSpeech(): boolean {
    return this.speaking;
  }

  /**
   * One frame: the microphone's level in dBFS at `now` (ms, monotonic).
   * Returns the event this frame decides, if any. After 'end', 'no-speech' or
   * 'too-long' the detector is done and returns null.
   */
  push(db: number, now: number): TurnEvent | null {
    if (this.finished) return null;
    this.startedAt ??= now;
    const frameMs = Math.min(MAX_FRAME_MS, now - (this.lastAt ?? now));
    this.lastAt = now;
    const level = Math.max(SILENT_DBFS, db);
    const onset = Math.max(this.floor(now, level) + ONSET_DB, MIN_SPEECH_DBFS);
    const sounding = level >= onset - HOLD_DB;

    if (now - this.startedAt >= this.maxTurnMs) return this.finish('too-long');
    if (this.speaking) {
      if (sounding) this.lastSoundAt = now;
      return now - this.lastSoundAt >= this.silenceMs ? this.finish('end') : null;
    }

    if (sounding) this.lastSoundAt = now;
    this.soundRunMs = sounding || level >= MIN_SPEECH_DBFS ? this.soundRunMs + frameMs : 0;
    if (this.soundRunMs >= HEARD_MS) this.heard = true;
    // A burst starts loud, once the floor has had a moment to learn the room,
    // and lasts through the dips between words.
    if (this.burstStart === null) {
      if (level >= onset && now - this.startedAt >= CALIBRATION_MS) {
        this.burstStart = now;
        this.burstMs = 0;
      }
    } else if (now - this.lastSoundAt > GAP_MS) {
      this.burstStart = null;
    } else if (sounding) {
      this.burstMs += frameMs;
    }
    if (this.burstStart !== null && this.burstMs >= this.minSpeechMs) {
      this.speaking = true;
      return 'speech';
    }
    // Only a room where nothing was heard is closed: clicks and beeps do not
    // count. Sound that never passed for speech may still be a quiet speaker:
    // that turn waits for a tap, or for maxTurnMs.
    if (now - this.startedAt >= this.noSpeechMs && !this.heard) return this.finish('no-speech');
    return null;
  }

  /** The room's level: the quiet end of the last few seconds, this frame included. */
  private floor(now: number, level: number): number {
    this.recent.push({ at: now, db: level });
    while (now - this.recent[0].at > FLOOR_WINDOW_MS) this.recent.shift();
    const sorted = this.recent.map((f) => f.db).sort((a, b) => a - b);
    return sorted[Math.floor((sorted.length - 1) * FLOOR_QUANTILE)];
  }

  private finish(event: TurnEvent): TurnEvent {
    this.finished = true;
    return event;
  }
}

export interface EndOfTurnWatch extends EndOfTurnOptions {
  onEvent: (event: TurnEvent) => void;
  /** How often the level is read, in ms. */
  intervalMs?: number;
}

/**
 * Feed a detector from `source`, a live microphone, every `intervalMs`.
 *
 * The level is taken in the speech band (250–3800 Hz), where a voice's
 * energy is: rumble from traffic or a fan, and hiss, count for less. A timer
 * rather than animation frames, which stop in a background tab. Returns the
 * function that stops watching; a terminal event stops it too.
 */
export function watchEndOfTurn(
  ctx: BaseAudioContext,
  source: AudioNode,
  { onEvent, intervalMs = 50, ...options }: EndOfTurnWatch,
): () => void {
  const detector = new EndOfTurnDetector(options);
  const highpass = ctx.createBiquadFilter();
  highpass.type = 'highpass';
  highpass.frequency.value = 250;
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = 'lowpass';
  lowpass.frequency.value = 3800;
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  source.connect(highpass);
  highpass.connect(lowpass);
  lowpass.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);

  let timer: ReturnType<typeof setInterval> | null = null;
  const stop = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
    try {
      source.disconnect(highpass);
    } catch {
      // Already disconnected, or the context has closed.
    }
  };
  timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    const event = detector.push(levelDbfs(samples), performance.now());
    if (!event) return;
    if (event !== 'speech') stop();
    onEvent(event);
  }, intervalMs);
  return stop;
}
