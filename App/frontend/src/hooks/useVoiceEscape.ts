import { useEffect } from 'react';

interface VoiceEscapeOptions {
  /** A recording is open (voice mode, or dictation recorded for the server). */
  recording: boolean;
  /** The browser's speech engine is listening for dictation. */
  dictating: boolean;
  /** A reply is being read aloud, or its audio is being fetched. */
  reading: boolean;
  onCancelRecording: () => unknown;
  onStopDictation: () => unknown;
  onStopReading: () => unknown;
}

/**
 * Escape stops what the voice is doing (G204): a recording is cancelled,
 * dictation stops listening (what it heard stays in the composer), and a reply
 * being read aloud stops — in that order, one per press. Never while a dialog
 * that Escape belongs to is open, nor when something else already handled it.
 */
export function useVoiceEscape({
  recording,
  dictating,
  reading,
  onCancelRecording,
  onStopDictation,
  onStopReading,
}: VoiceEscapeOptions): void {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"], dialog[open]')) return;
      if (recording) {
        void onCancelRecording();
      } else if (dictating) {
        void onStopDictation();
      } else if (reading) {
        void onStopReading();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [recording, dictating, reading, onCancelRecording, onStopDictation, onStopReading]);
}
