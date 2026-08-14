"use client";

import React, { memo, useCallback, useRef, useState } from "react";
import { CloseIcon, SpeakerIcon } from "./Icons";
import { playVoiceSample, VOICES, type VoiceOption } from "@/lib/voices";

/**
 * Voice personnel selection modal.
 *
 * Lets users pick from available TTS voices with preview playback. The list
 * itself lives in `lib/voices` because the Voice tab in settings offers the
 * same choices and both write `useVoiceStore.voiceId`.
 */

export type { VoiceOption };

interface VoiceSettingsProps {
  open: boolean;
  selectedVoice: string;
  onClose: () => void;
  onSelect: (voiceId: string) => void;
}

function VoiceSettingsInner({ open, selectedVoice, onClose, onSelect }: VoiceSettingsProps) {
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const handlePreview = useCallback(
    async (voice: VoiceOption) => {
      if (playing === voice.id) {
        audioRef.current?.pause();
        setPlaying(null);
        return;
      }
      setPlaying(voice.id);
      try {
        const audio = await playVoiceSample(voice);
        audioRef.current = audio;
        // `play()` resolves when playback STARTS, so the button has to stay in
        // its "Stop" state until the clip ends rather than resetting here.
        audio.onended = () => setPlaying((p) => (p === voice.id ? null : p));
      } catch {
        // Preview unavailable (speech service down) — reset the button.
        setPlaying((p) => (p === voice.id ? null : p));
      }
    },
    [playing],
  );

  if (!open) return null;

  return (
    <div className="voice-settings-overlay" onClick={onClose}>
      <div className="voice-settings-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Voice settings">
        <div className="voice-settings-header">
          <h3>Voice Settings</h3>
          <button className="voice-settings-close" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </div>
        <p className="voice-settings-desc">Choose a voice for text-to-speech narration</p>
        <div className="voice-settings-list">
          {VOICES.map((v) => (
            <button
              key={v.id}
              className={`voice-option ${selectedVoice === v.id ? "voice-option-selected" : ""}`}
              onClick={() => onSelect(v.id)}
            >
              <div className="voice-option-info">
                <span className="voice-option-label">{v.label}</span>
                <span className="voice-option-desc">{v.description}</span>
              </div>
              <div className="voice-option-actions">
                <button
                  className={`voice-preview-btn ${playing === v.id ? "voice-preview-playing" : ""}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    handlePreview(v);
                  }}
                  aria-label={`Preview ${v.label}`}
                >
                  <SpeakerIcon /> {playing === v.id ? "Stop" : "Play"}
                </button>
                {selectedVoice === v.id && (
                  <span className="voice-option-badge">Active</span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

const VoiceSettings = memo(VoiceSettingsInner);
export default VoiceSettings;
