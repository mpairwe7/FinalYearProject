"use client";

/**
 * Voice settings — narration on/off and which voice narrates.
 *
 * Deliberately short. `useVoiceStore` persists several other fields
 * (auto-barge-in, silence timeout, accent profile) that nothing currently
 * reads, so offering them here would be a panel of switches that change
 * nothing. Only `voiceId` (read by every TTS call in page.tsx and VoiceChat)
 * and narration (the page's auto-narrate state) are wired, so only those two
 * are shown.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { AUTOMATIC_VOICE_LABEL, playVoiceSample, VOICES } from "../../lib/voices";
import { useVoiceStore } from "../../store/useVoiceStore";
import {
  ActionButton,
  SelectControl,
  SettingsRow,
  SettingsSection,
  StatusNote,
  Toggle,
} from "./controls";

const VOICE_SELECT_OPTIONS = [
  { value: "", label: AUTOMATIC_VOICE_LABEL },
  ...VOICES.map((v) => ({ value: v.id, label: v.label })),
];

interface VoiceSectionProps {
  autoNarrate: boolean;
  onAutoNarrateChange: (on: boolean) => void;
  /** From the cached `/v1/speech/health` probe the header pill already shows. */
  speechReady: boolean;
}

export default function VoiceSection({
  autoNarrate,
  onAutoNarrateChange,
  speechReady,
}: VoiceSectionProps) {
  const voiceId = useVoiceStore((s) => s.voiceId);
  const setVoiceId = useVoiceStore((s) => s.setVoiceId);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // A preview left playing when the dialog closes would keep talking over the
  // page it returned to.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    [],
  );

  const selected = VOICES.find((v) => v.id === voiceId);

  const preview = useCallback(async () => {
    if (!selected) return;
    if (previewing) {
      audioRef.current?.pause();
      audioRef.current = null;
      setPreviewing(false);
      return;
    }
    setError("");
    setPreviewing(true);
    try {
      const audio = await playVoiceSample(selected);
      audioRef.current = audio;
      audio.onended = () => setPreviewing(false);
    } catch (err) {
      setPreviewing(false);
      setError(
        `Could not play a preview: ${(err as Error).message}. The reply text is unaffected.`,
      );
    }
  }, [previewing, selected]);

  return (
    <SettingsSection
      title="Voice"
      description="Narration reads replies aloud with the speech service; typing and reading never depend on it."
    >
      <SettingsRow
        label="Narrate replies aloud"
        hint={
          speechReady
            ? "Turning on voice mode in the composer also turns this on."
            : "The speech service is not reachable right now, so narration will stay silent."
        }
      >
        <Toggle
          label="Narrate replies aloud"
          checked={autoNarrate}
          onChange={onAutoNarrateChange}
        />
      </SettingsRow>

      <SettingsRow
        label="Narration voice"
        hint="Automatic lets the server pick a voice that matches the response language."
        htmlFor="setv2-voice"
      >
        <SelectControl
          id="setv2-voice"
          value={voiceId}
          options={VOICE_SELECT_OPTIONS}
          onChange={setVoiceId}
        />
      </SettingsRow>

      <SettingsRow
        label="Preview"
        hint={
          selected
            ? `Speaks one line in ${selected.label}.`
            : "Choose a specific voice above to hear it."
        }
      >
        <ActionButton onClick={preview} disabled={!selected} busy={previewing}>
          {previewing ? "Stop" : "Play sample"}
        </ActionButton>
      </SettingsRow>

      {error && <StatusNote kind="error">{error}</StatusNote>}
    </SettingsSection>
  );
}
