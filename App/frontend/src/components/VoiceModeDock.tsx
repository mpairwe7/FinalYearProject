/**
 * Voice mode's dock: what the conversation is doing right now, and the one
 * control that answers it.
 *
 * Voice mode used to be a ring on a button. Entering it changed a placeholder;
 * the microphone still wanted its own tap; a turn sent itself without warning;
 * and from the moment it went until the answer came back — recognition and
 * the whole reply, 2–20 s — nothing on screen moved, which on the landing page
 * meant nothing at all. This takes the composer's place while voice mode is on
 * and says everything a hands-free conversation needs, the way voice now works
 * in the assistants people already use (voice inside the chat with a live
 * transcript, a bar with a state, an orb and a way to take the floor back):
 *
 *   - one named state at a time, never animation alone, which nobody can read;
 *   - the call's orb, in teal while the taxpayer has the floor and navy while
 *     the assistant does, moving with whoever is talking;
 *   - a countdown round the send button while a pause is about to end the turn;
 *   - what was heard, as soon as it is known, and the sentence being read;
 *   - one tap that always takes the floor back: send now, stop, or interrupt.
 *
 * Changes are announced politely (WCAG 2.2 SC 4.1.3) — but not while the
 * microphone is open, where a screen reader's voice would be recorded into the
 * turn; the mic's own tones say it opened. Controls are 44 px or more (SC 2.5.8
 * asks for 24). Escape still cancels a recording or stops the reading (G204).
 */
import React, { useEffect, useRef } from 'react';

import { useNarration } from '../hooks/useNarration';
import { useTranslation } from '../lib/i18n';
import { getAudioLevels, getPauseProgress } from '../services/audioLevelBus';
import { pausePlayback, readPlaybackLevel, resumePlayback } from '../services/voiceService';
import { CallOrb, type OrbLevels, type OrbState } from './call/CallOrb';
import { CheckIcon, MicIcon, MicOffIcon, PauseIcon, PlayIcon, StopIcon } from './Icons';

export type VoiceDockPhase =
  /** The microphone is being opened: a permission prompt, then engine warm-up. */
  | 'opening'
  /** Open, and nothing has been said yet. */
  | 'listening'
  /** The taxpayer is talking. */
  | 'hearing'
  /** They have paused, and the turn sends itself unless they go on. */
  | 'pausing'
  /** The recording is with the speech recogniser. */
  | 'transcribing'
  /** The answer is being worked on; `workingLabel` says which step. */
  | 'thinking'
  /** The answer is on screen and its speech is being fetched. */
  | 'preparing'
  | 'speaking'
  /** Shown by the dock itself while a reading it was told is `speaking` is paused. */
  | 'paused'
  /** Nothing is open: the next turn starts with a tap. */
  | 'idle';

/**
 * Once a pause is this far towards ending the turn, the dock says it will send.
 * A quarter of the default 1.2 s is 300 ms: longer than the gaps between words
 * (the detector bridges 250 ms), so it does not flicker on while someone talks.
 */
export const PAUSE_SHOWN_FROM = 0.25;

interface VoiceModeDockProps {
  phase: VoiceDockPhase;
  /** While thinking: the step, in words ("Searching the URA knowledge base"). */
  workingLabel?: string;
  /** What the speech recogniser heard, once it is known. */
  heard?: string | null;
  /** About the last attempt ("I didn't hear anything…"), until the next one. */
  notice?: string | null;
  /** The turn sends itself after a pause (Settings → Voice); else a tap sends it. */
  autoSend: boolean;
  /** Open the microphone; while the assistant speaks, interrupt it and listen. */
  onTalk: () => void;
  /** Send what has been said, now. */
  onSend: () => void;
  /** Close the microphone; nothing is sent. */
  onCancel: () => void;
  /** Stop the turn being transcribed or answered. */
  onStop: () => void;
  /** Leave voice mode for the typed composer. */
  onEnd: () => void;
}

/** Microphone from the bus, the reply from the player — stable, so the orb's loop never restarts. */
function readVoiceLevels(): OrbLevels {
  return { input: getAudioLevels().input, output: readPlaybackLevel() };
}

const MIC_OPEN: ReadonlySet<VoiceDockPhase> = new Set(['opening', 'listening', 'hearing', 'pausing']);
const READING: ReadonlySet<VoiceDockPhase> = new Set(['preparing', 'speaking', 'paused']);

function orbStateFor(phase: VoiceDockPhase): OrbState {
  if (phase === 'listening' || phase === 'hearing' || phase === 'pausing') return 'listening';
  if (phase === 'opening' || phase === 'transcribing' || phase === 'thinking' || phase === 'preparing') return 'thinking';
  if (phase === 'speaking' || phase === 'paused') return 'speaking';
  return 'idle';
}

export default function VoiceModeDock({
  phase: given,
  workingLabel,
  heard,
  notice,
  autoSend,
  onTalk,
  onSend,
  onCancel,
  onStop,
  onEnd,
}: VoiceModeDockProps) {
  const t = useTranslation();
  const { sentence, paused } = useNarration();
  // Paused is the player's to know: the page says "speaking", the dock asks it.
  const phase: VoiceDockPhase = given === 'speaking' && paused ? 'paused' : given;
  const primaryRef = useRef<HTMLButtonElement>(null);
  const ringRef = useRef<HTMLSpanElement>(null);
  const micOpen = MIC_OPEN.has(phase);
  const reading = READING.has(phase);

  // The button that opened voice mode went with the composer (and the first
  // answer moves the dock from the landing page into the conversation): focus
  // lands on the control that matters now rather than falling back to the
  // page — unless the reader has already put it somewhere else.
  useEffect(() => {
    const current = document.activeElement;
    if (current && current !== document.body) return;
    primaryRef.current?.focus({ preventScroll: true });
  }, []);

  // The countdown, read every frame and written straight onto the ring: it
  // changes every 50 ms, and nothing else on the page needs to re-render for it.
  useEffect(() => {
    const ring = ringRef.current;
    if (!ring || !autoSend || (phase !== 'hearing' && phase !== 'pausing')) return;
    if (typeof requestAnimationFrame !== 'function') return;
    let frame = requestAnimationFrame(function tick() {
      ring.style.setProperty('--eot', getPauseProgress().toFixed(3));
      frame = requestAnimationFrame(tick);
    });
    return () => {
      cancelAnimationFrame(frame);
      ring.style.setProperty('--eot', '0');
    };
  }, [phase, autoSend]);

  const label = {
    opening: t('voiceDock.opening'),
    listening: t('voiceDock.listening'),
    hearing: t('voiceDock.hearing'),
    pausing: t('voiceDock.pausing'),
    transcribing: t('voiceDock.transcribing'),
    thinking: workingLabel || t('phase.thinking'),
    preparing: t('voiceDock.preparing'),
    speaking: t('voiceDock.speaking'),
    paused: t('narration.paused'),
    idle: t('voiceDock.idle'),
  }[phase];

  // The second line: what just went wrong outranks everything, then what was
  // heard (while the answer is worked on) or is being read (while it is).
  const youSaid = heard ? t('voiceDock.youSaid', { text: heard }) : '';
  const detail = notice
    ? notice
    : {
        opening: t('voiceDock.openingHint'),
        listening: t(autoSend ? 'voiceDock.listeningHintAuto' : 'voiceDock.listeningHint'),
        hearing: t(autoSend ? 'voiceDock.listeningHintAuto' : 'voiceDock.listeningHint'),
        pausing: t('voiceDock.pausingHint'),
        transcribing: t('voiceDock.transcribingHint'),
        thinking: youSaid,
        preparing: youSaid,
        speaking: sentence || t('voiceDock.speakingHint'),
        paused: sentence || t('voiceDock.speakingHint'),
        idle: t('voiceDock.idleHint'),
      }[phase];

  // Announced: what changed while the microphone is closed, and only that. The
  // steps of the answer (searching, writing…) are one announcement, not four.
  const announcement = micOpen
    ? ''
    : phase === 'thinking' || phase === 'preparing'
      ? t('phase.thinking')
      : phase === 'speaking'
        ? ''
        : [label, notice].filter(Boolean).join('. ');

  const primary: {
    action: 'talk' | 'send' | 'stop' | 'interrupt' | 'busy';
    label: string;
    icon: React.ReactNode;
    onClick?: () => void;
  } =
    phase === 'opening'
      ? { action: 'busy', label: t('composer.micStarting'), icon: <MicIcon /> }
      : micOpen
        ? { action: 'send', label: t('voiceDock.send'), icon: <CheckIcon />, onClick: onSend }
        : phase === 'transcribing' || phase === 'thinking'
          ? { action: 'stop', label: t('voiceDock.stop'), icon: <StopIcon />, onClick: onStop }
          : reading
            ? { action: 'interrupt', label: t('voiceDock.interrupt'), icon: <MicIcon />, onClick: onTalk }
            : { action: 'talk', label: t('voiceDock.talk'), icon: <MicIcon />, onClick: onTalk };

  return (
    <section
      className="voice-dock"
      data-phase={phase}
      data-floor={orbStateFor(phase)}
      aria-label={t('voiceDock.region')}
      data-testid="voice-dock"
    >
      <p className="sr-only" role="status" aria-atomic="true">
        {announcement}
      </p>
      <div className="voice-dock-status">
        {/* Nothing to follow while idle: the loop rests, and the orb breathes in CSS. */}
        <CallOrb active={phase !== 'idle'} state={orbStateFor(phase)} readLevels={readVoiceLevels} />
        <div className="voice-dock-text">
          <p className="voice-dock-label" data-testid="voice-dock-label">
            {label}
          </p>
          {detail && (
            <p
              className={`voice-dock-detail${notice ? ' is-notice' : ''}`}
              data-testid="voice-dock-detail"
              // The sentence being read is already being heard; read out here
              // too, it would talk over itself.
              aria-hidden={!notice && (phase === 'speaking' || phase === 'paused') ? true : undefined}
            >
              {detail}
            </p>
          )}
        </div>
      </div>
      <div className="voice-dock-controls">
        <button
          type="button"
          className="voice-dock-end"
          onClick={onEnd}
          aria-label={t('voiceDock.end')}
          data-testid="voice-dock-end"
        >
          {t('voiceDock.endShort')}
        </button>
        <span className="voice-dock-spacer" />
        {micOpen && phase !== 'opening' && (
          <button
            type="button"
            className="voice-dock-btn"
            onClick={onCancel}
            aria-label={t('voiceDock.micOff')}
            aria-keyshortcuts="Escape"
            data-tip={t('voiceDock.micOff')}
            data-testid="voice-dock-mic-off"
          >
            <MicOffIcon size={20} />
          </button>
        )}
        {(phase === 'speaking' || phase === 'paused') && (
          <button
            type="button"
            className="voice-dock-btn"
            onClick={() => void (phase === 'paused' ? resumePlayback() : pausePlayback())}
            aria-label={phase === 'paused' ? t('narration.resume') : t('narration.pause')}
            data-tip={phase === 'paused' ? t('narration.resume') : t('narration.pause')}
            data-testid="voice-dock-pause"
          >
            {phase === 'paused' ? <PlayIcon /> : <PauseIcon />}
          </button>
        )}
        {/* One element in every phase, so focus stays on it as its job
            changes. aria-disabled rather than disabled while the mic opens:
            the dock mounts in that phase, and a disabled button cannot take
            the focus it is given. */}
        <button
          ref={primaryRef}
          type="button"
          className="voice-dock-btn voice-dock-primary"
          data-action={primary.action}
          onClick={primary.onClick}
          aria-disabled={primary.onClick ? undefined : true}
          aria-label={primary.label}
          data-tip={primary.label}
          data-testid="voice-dock-primary"
        >
          {primary.action === 'send' && autoSend && (
            <span ref={ringRef} className="voice-dock-ring" aria-hidden="true">
              <svg viewBox="0 0 36 36">
                <circle cx="18" cy="18" r="16.5" pathLength="100" />
              </svg>
            </span>
          )}
          {primary.icon}
        </button>
      </div>
    </section>
  );
}
