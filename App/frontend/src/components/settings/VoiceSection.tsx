"use client";

/**
 * Voice — narration on/off, and a speaker per language.
 *
 * Per language, not one voice, because a speaker only exists inside its own
 * language: Sunbird's catalog tags are language-scoped and the backend refuses
 * one from another language rather than synthesising the wrong one. So the
 * question this panel answers is "who reads Luganda to me", asked once per
 * language you actually use.
 *
 * The catalogue is fetched. A hardcoded list would keep offering Ugandan
 * voices on a deployment with no Sunbird key, and picking one would silently
 * get an English voice reading Luganda.
 *
 * Deliberately absent: `useVoiceStore` also persists auto-barge-in, silence
 * timeout and accent profile, which nothing reads. Offering them would be a
 * panel of switches that change nothing.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LOCALE_OPTIONS } from "../../lib/locales";
import {
  fetchVoiceCatalogue,
  playVoiceSample,
  voiceDisplayName,
  type VoiceOption,
} from "../../lib/voices";
import { useChatStore } from "../../store/useChatStore";
import { useVoiceStore } from "../../store/useVoiceStore";
import { SpeakerIcon, StopIcon } from "../Icons";
import { SettingsRow, SettingsSection, StatusNote, Toggle } from "./controls";

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
  const chatLocale = useChatStore((s) => s.locale);
  const voiceByLocale = useVoiceStore((s) => s.voiceByLocale);
  const setVoiceForLocale = useVoiceStore((s) => s.setVoiceForLocale);

  const [playing, setPlaying] = useState<string | null>(null);
  const [error, setError] = useState("");
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const catalogue = useQuery({
    queryKey: ["speech-voices"],
    queryFn: fetchVoiceCatalogue,
    staleTime: 5 * 60_000,
    retry: false,
  });

  // A preview left playing when the dialog closes would talk over the page it
  // returned to.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    [],
  );

  const stop = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPlaying(null);
  }, []);

  const preview = useCallback(
    async (locale: string, voice: VoiceOption) => {
      const key = `${locale}:${voice.id}`;
      if (playing === key) {
        stop();
        return;
      }
      stop();
      setError("");
      setPlaying(key);
      try {
        const audio = await playVoiceSample(locale, voice.id);
        audioRef.current = audio;
        // `play()` resolves when playback STARTS, so the button has to stay in
        // its stop state until the clip actually ends.
        audio.onended = () => setPlaying((p) => (p === key ? null : p));
      } catch (err) {
        setPlaying((p) => (p === key ? null : p));
        setError(
          `Could not play that voice: ${(err as Error).message}. The reply text is unaffected.`,
        );
      }
    },
    [playing, stop],
  );

  /** The chat's own language first — that is the voice being chosen right now. */
  const languages = useMemo(() => {
    const available = catalogue.data?.voices ?? {};
    return LOCALE_OPTIONS.filter((l) => (available[l.value]?.length ?? 0) > 0).sort(
      (a, b) =>
        (a.value === chatLocale ? -1 : 0) - (b.value === chatLocale ? -1 : 0),
    );
  }, [catalogue.data, chatLocale]);

  return (
    <>
      <SettingsSection
        title="Narration"
        description="Reads replies aloud with the speech service. Typing and reading never depend on it."
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
      </SettingsSection>

      <SettingsSection
        title="Voices"
        description="One speaker per language — a voice belongs to the language it speaks, so choosing here sets who reads that language to you."
      >
        {catalogue.isPending && <StatusNote kind="info">Loading the voice catalogue…</StatusNote>}

        {catalogue.error && (
          <StatusNote kind="error">
            Could not load the voice catalogue: {(catalogue.error as Error).message}
          </StatusNote>
        )}

        {catalogue.data && !catalogue.data.sunbird_configured && (
          <StatusNote kind="info">
            This deployment has no Sunbird key, so only the English voices can be
            used. The Ugandan languages fall back to an English speaker.
          </StatusNote>
        )}

        {catalogue.data &&
          languages.map((language) => {
            const voices = catalogue.data.voices[language.value] ?? [];
            const chosen = voiceByLocale[language.value];
            return (
              <div key={language.value} className="setv2-voicegroup">
                <div className="setv2-voicegroup-head">
                  <span className="setv2-voicegroup-name">
                    {language.label}
                    {language.native !== language.label && (
                      <span className="setv2-voicegroup-native"> · {language.native}</span>
                    )}
                  </span>
                  {language.value === chatLocale && (
                    <span className="setv2-voicegroup-current">Current language</span>
                  )}
                </div>

                <div
                  className="setv2-voicelist"
                  role="radiogroup"
                  aria-label={`Narration voice for ${language.label}`}
                >
                  {voices.map((voice, index) => {
                    const key = `${language.value}:${voice.id}`;
                    const isChosen = chosen ? chosen === voice.id : voice.default;
                    const name = voiceDisplayName(language.value, voice, index);
                    return (
                      <div
                        key={voice.id}
                        className={`setv2-voice${isChosen ? " setv2-voice-on" : ""}`}
                      >
                        <button
                          type="button"
                          role="radio"
                          aria-checked={isChosen}
                          className="setv2-voice-pick"
                          disabled={!voice.available}
                          onClick={() =>
                            setVoiceForLocale(
                              language.value,
                              // Re-picking the default clears the choice, so the
                              // locale follows the backend if that default moves.
                              voice.default ? "" : voice.id,
                            )
                          }
                        >
                          <span className="setv2-voice-name">{name}</span>
                          <span className="setv2-voice-meta">
                            {voice.default && "Default"}
                            {voice.default && voice.native && " · "}
                            {voice.native && "Native speaker"}
                            {!voice.available && " · unavailable here"}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="setv2-voice-play"
                          disabled={!voice.available}
                          aria-label={
                            playing === key
                              ? `Stop ${name}`
                              : `Play a ${language.label} sample in ${name}`
                          }
                          onClick={() => preview(language.value, voice)}
                        >
                          {playing === key ? <StopIcon /> : <SpeakerIcon />}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}

        {error && <StatusNote kind="error">{error}</StatusNote>}
      </SettingsSection>
    </>
  );
}
