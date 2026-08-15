/**
 * Narration voices, as served by the backend.
 *
 * The catalogue is FETCHED, not hardcoded, because the client cannot know which
 * speakers a deployment can actually reach: the Ugandan voices are Sunbird
 * catalog tags that only work when Sunbird is configured, and English is served
 * by edge-tts, which needs no key. A baked-in list keeps offering voices after
 * the backend loses the ability to serve them, and the person who picks one
 * gets an English fallback reading Luganda with nothing to say why.
 *
 * Voices are per-language on purpose. A Sunbird tag is language-scoped — the
 * backend refuses a Luganda speaker for Acholi rather than synthesising the
 * wrong language — so "your voice" is really "your voice for this language".
 */

import { authHeaders } from "./authSession";

export interface VoiceOption {
  /** What `/v1/tts` takes as `voice`: an edge-tts name or a Sunbird catalog tag. */
  id: string;
  provider: "sunbird" | "edge_tts" | string;
  /** True when the speaker is a native speaker of the language, not a stand-in. */
  native: boolean;
  /** The speaker used when no choice is made. Exactly one per language. */
  default: boolean;
  /** False when this deployment cannot reach the provider (e.g. no Sunbird key). */
  available: boolean;
}

export interface VoiceCatalogue {
  voices: Record<string, VoiceOption[]>;
  sunbird_configured: boolean;
}

/** Sample line per language, so a preview is heard in the language it belongs to. */
export const VOICE_SAMPLES: Record<string, string> = {
  en: "Welcome to URA. The standard VAT rate in Uganda is 18 percent.",
  lg: "Tukusanyukidde. Omusolo gwa VAT mu Uganda guli ku bitundu 18.",
  sw: "Karibu URA. Kiwango cha VAT nchini Uganda ni asilimia 18.",
  nyn: "Tukwakiriza. Omusoro gwa VAT omuri Uganda ni ebicweka 18.",
  ach: "Wabedo. Mucoro me VAT i Uganda tye i wi 18.",
};

/**
 * Display name for a speaker.
 *
 * Deliberately neutral. The catalog gives opaque tags (`waxal_lug_0004`) and
 * nothing about the person behind them — inventing "Nakato, warm and friendly"
 * would be asserting a gender and a character this app cannot know. Numbering
 * them and letting the preview button do the describing is honest, and it is
 * what the person actually chooses on: how it sounds.
 */
export function voiceDisplayName(locale: string, voice: VoiceOption, index: number): string {
  if (voice.provider === "edge_tts") {
    // edge-tts names are self-describing: en-GB-SoniaNeural -> "Sonia (en-GB)".
    const parts = voice.id.split("-");
    const name = parts[2]?.replace(/Neural$/, "") ?? voice.id;
    const region = parts.slice(0, 2).join("-");
    return `${name} (${region})`;
  }
  return `Voice ${index + 1}`;
}

export async function fetchVoiceCatalogue(): Promise<VoiceCatalogue> {
  const res = await fetch("/api/v1/speech/voices", {
    headers: authHeaders(),
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(`Could not load voices (${res.status})`);
  return res.json();
}

/**
 * Synthesise a sample in *locale* with *voiceId* and start playing it.
 *
 * Returns the playing element so the caller can stop it; throws when speech is
 * unavailable, which the caller shows as "preview unavailable" rather than
 * failing the whole panel.
 */
export async function playVoiceSample(
  locale: string,
  voiceId: string,
): Promise<HTMLAudioElement> {
  const res = await fetch("/api/v1/tts", {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      text: VOICE_SAMPLES[locale] ?? VOICE_SAMPLES.en,
      language: locale,
      voice: voiceId,
    }),
    // A cold Sunbird speaker has been measured at ~25s; the default 15s cut
    // previews off mid-warmup and reported it as a failure.
    signal: AbortSignal.timeout(40_000),
  });
  if (!res.ok) throw new Error(`Speech synthesis failed (${res.status})`);
  const data = await res.json();
  if (!data.audio_base64) throw new Error(data.error || "Speech synthesis returned no audio");
  const audio = new Audio(`data:audio/wav;base64,${data.audio_base64}`);
  await audio.play();
  return audio;
}
