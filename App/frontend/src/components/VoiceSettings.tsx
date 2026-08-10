"use client";

import React, { memo, useCallback, useRef, useState } from "react";
import { CloseIcon, SpeakerIcon } from "./Icons";
import { authHeaders } from "@/lib/authSession";

/**
 * Voice personnel selection modal.
 *
 * Lets users pick from available TTS voices with preview playback.
 */

export interface VoiceOption {
  id: string;
  label: string;
  description: string;
  language: string;
  sample: string; // text to preview
}

const VOICES: VoiceOption[] = [
  {
    id: "en-US-AriaNeural",
    label: "English - Female (Professional)",
    description: "Clear, professional tone ideal for tax guidance",
    language: "en",
    sample: "Welcome to URA. How can I help you today?",
  },
  {
    id: "en-US-GuyNeural",
    label: "English - Male (Friendly)",
    description: "Warm, approachable voice for general queries",
    language: "en",
    sample: "The VAT rate in Uganda is 18 percent.",
  },
  {
    id: "en-GB-SoniaNeural",
    label: "English - British (Formal)",
    description: "Formal British accent for official communication",
    language: "en",
    sample: "Your TIN registration has been processed successfully.",
  },
  {
    id: "en-default",
    label: "English - Default",
    description: "Standard voice for English responses",
    language: "en",
    sample: "Please visit URA dot go dot UG for more information.",
  },
  {
    id: "lg-default",
    label: "Luganda - Default",
    description: "Voice for Luganda language responses",
    language: "lg",
    sample: "Tukusanyukidde. Oyinza okubuuza ku musolo.",
  },
];

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
        const res = await fetch("/api/v1/tts", {
          method: "POST",
          headers: authHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ text: voice.sample, language: voice.language, voice: voice.id }),
          signal: AbortSignal.timeout(15000),
        });
        if (!res.ok) throw new Error("TTS failed");
        const data = await res.json();
        if (data.audio_base64) {
          const audio = new Audio(`data:audio/wav;base64,${data.audio_base64}`);
          audioRef.current = audio;
          audio.onended = () => setPlaying(null);
          await audio.play();
        }
      } catch {
        // preview unavailable
      } finally {
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
