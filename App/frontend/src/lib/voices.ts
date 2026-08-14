/**
 * Narration voices offered for text-to-speech.
 *
 * One list, two surfaces: the voice-settings modal and the Voice tab in
 * settings both pick from here, and `useVoiceStore.voiceId` is the single
 * value they write. The ids are what `/v1/tts` forwards to the speech
 * service — an id this list invents would synthesise with the server default
 * and quietly ignore the choice.
 */

import { authHeaders } from "./authSession";

export interface VoiceOption {
  id: string;
  label: string;
  description: string;
  /** Locale the sample text is written in. */
  language: string;
  /** Preview line — short enough to synthesise inside the request timeout. */
  sample: string;
}

export const VOICES: readonly VoiceOption[] = [
  {
    id: "en-US-AriaNeural",
    label: "English — Female (Professional)",
    description: "Clear, professional tone ideal for tax guidance",
    language: "en",
    sample: "Welcome to URA. How can I help you today?",
  },
  {
    id: "en-US-GuyNeural",
    label: "English — Male (Friendly)",
    description: "Warm, approachable voice for general queries",
    language: "en",
    sample: "The VAT rate in Uganda is 18 percent.",
  },
  {
    id: "en-GB-SoniaNeural",
    label: "English — British (Formal)",
    description: "Formal British accent for official communication",
    language: "en",
    sample: "Your TIN registration has been processed successfully.",
  },
  {
    id: "en-default",
    label: "English — Default",
    description: "Standard voice for English responses",
    language: "en",
    sample: "Please visit URA dot go dot UG for more information.",
  },
  {
    id: "lg-default",
    label: "Luganda — Default",
    description: "Voice for Luganda language responses",
    language: "lg",
    sample: "Tukusanyukidde. Oyinza okubuuza ku musolo.",
  },
];

/** The label to show when nothing has been chosen (server picks per locale). */
export const AUTOMATIC_VOICE_LABEL = "Automatic (match the response language)";

export function voiceLabel(voiceId: string): string {
  if (!voiceId) return AUTOMATIC_VOICE_LABEL;
  return VOICES.find((v) => v.id === voiceId)?.label ?? voiceId;
}

/**
 * Synthesise a voice's sample line and start playing it.
 *
 * Returns the playing element so the caller can stop it; throws if speech is
 * unavailable, which the caller shows as "preview unavailable" rather than
 * failing the whole settings panel.
 */
export async function playVoiceSample(voice: VoiceOption): Promise<HTMLAudioElement> {
  const res = await fetch("/api/v1/tts", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ text: voice.sample, language: voice.language, voice: voice.id }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) throw new Error(`Speech synthesis failed (${res.status})`);
  const data = await res.json();
  if (!data.audio_base64) throw new Error("Speech synthesis returned no audio");
  const audio = new Audio(`data:audio/wav;base64,${data.audio_base64}`);
  await audio.play();
  return audio;
}
