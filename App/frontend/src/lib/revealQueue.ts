/**
 * Paces a reply onto the screen so it reads as typed rather than pasted.
 *
 * The transport cannot do this for us. SSE runs with sentence batching, some
 * service branches emit the whole answer as a single token frame, and the
 * `done` frame replaces whatever streamed with the cleaned reply — so what
 * arrives is bursty at best and one lump at worst. This keeps two pointers, the
 * text that has *arrived* and the text that has been *shown*, and walks the
 * second toward the first on a timer.
 *
 * Committing into the store (rather than slicing at render time) means Copy,
 * Listen, feedback and persistence all keep seeing one complete string; only
 * the moment it becomes complete moves.
 */

/** Reveal step, ms. Not per-frame: the transcript re-parses markdown on every
 *  content change, and 60fps of that on a long reply is wasted work. */
const TICK_MS = 30;
const MIN_CHARS = 2;
const MAX_CHARS = 100;
/** Bigger backlog ⇒ bigger steps, so a long answer still lands promptly. */
const BACKLOG_DIVISOR = 20;

/**
 * How far to advance this tick, snapped forward to a word boundary.
 *
 * Breaking mid-word is the thing that reads as a progress bar rather than
 * typing, so the index is nudged to the next space — unless that would overrun
 * the budget badly, in which case a long unbroken token (a URL, a code span)
 * is allowed to appear whole rather than stall the queue.
 */
export function nextRevealIndex(target: string, shown: number, budget: number): number {
  if (shown >= target.length) return target.length;
  const raw = Math.min(target.length, shown + budget);
  if (raw >= target.length) return target.length;

  // Walk forward to the end of the word we landed inside.
  const nextBreak = target.slice(raw).search(/[\s\n]/);
  if (nextBreak === -1) return target.length;
  return raw + nextBreak + 1;
}

export function revealBudget(remaining: number): number {
  return Math.max(MIN_CHARS, Math.min(MAX_CHARS, Math.ceil(remaining / BACKLOG_DIVISOR)));
}

export interface RevealQueue {
  /** Append newly arrived text. */
  push(chunk: string): void;
  /** Replace the target outright (`done` / `revision`). Snaps if it is not a
   *  continuation of what is already on screen. */
  set(text: string): void;
  /** Everything that has arrived, revealed or not. */
  getTarget(): string;
  /** Drain to the end and stop. Resolves once the store holds the full text. */
  finish(): Promise<void>;
  /** Abandon pacing, commit what has arrived, stop. */
  stop(): void;
}

interface RevealQueueOptions {
  onCommit: (text: string) => void;
  /** Skip pacing entirely — reduced motion, or a caller that wants it whole. */
  instant?: boolean;
  tickMs?: number;
}

export function createRevealQueue({
  onCommit,
  instant = false,
  tickMs = TICK_MS,
}: RevealQueueOptions): RevealQueue {
  let target = '';
  let shown = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let done = false;
  const waiters: (() => void)[] = [];

  const clear = () => {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };

  const settle = () => {
    if (shown < target.length) return;
    clear();
    while (waiters.length) waiters.shift()!();
  };

  const commitAll = () => {
    if (shown !== target.length) {
      shown = target.length;
      onCommit(target);
    }
    settle();
  };

  const tick = () => {
    if (shown >= target.length) {
      settle();
      return;
    }
    shown = nextRevealIndex(target, shown, revealBudget(target.length - shown));
    onCommit(target.slice(0, shown));
    if (shown >= target.length) settle();
  };

  const ensureRunning = () => {
    if (done || instant || timer !== null || shown >= target.length) return;
    timer = setInterval(tick, tickMs);
  };

  return {
    push(chunk) {
      if (done || !chunk) return;
      target += chunk;
      if (instant) {
        commitAll();
        return;
      }
      ensureRunning();
    },
    set(text) {
      if (done) return;
      // Compare against what is on screen *now*, before the target moves.
      const onScreen = target.slice(0, shown);
      target = text;
      if (instant) {
        commitAll();
        return;
      }
      if (text.startsWith(onScreen)) {
        // Still a continuation — keep typing from where the reader is.
        ensureRunning();
        return;
      }
      // Diverged: `done` swaps in the cleaned reply, which can drop the
      // model's chain-of-thought from the middle of what was already shown.
      // Snap to the new text rather than rewinding and retyping — the reader
      // has seen this prefix, and animating it away would draw the eye to
      // exactly the text we are trying to remove.
      shown = text.length;
      onCommit(text);
      settle();
    },
    getTarget() {
      return target;
    },
    finish() {
      if (instant || shown >= target.length) {
        commitAll();
        return Promise.resolve();
      }
      ensureRunning();
      return new Promise<void>((resolve) => {
        waiters.push(resolve);
      });
    },
    stop() {
      done = true;
      commitAll();
      clear();
    },
  };
}
