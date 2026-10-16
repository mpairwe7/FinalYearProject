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
  nyn: "Tukwakira omu URA. Omushoro gwa VAT omuri Uganda guri aha bicweka 18 aha kikumi.",
  ach: "Wajoli i URA. Wel mucoro me VAT i Uganda tye dul 18 i kom miya.",
};

export interface VoicePersona {
  name: string;
  role: string;
  tone: string;
  avatar: string;
}

export const VOICE_PERSONAS: Record<string, VoicePersona> = {
  // English Personas
  "en-US-AriaNeural": {
    name: "Aria",
    role: "Senior Taxpayer Services Specialist",
    tone: "Professional, Empathetic & Clear",
    avatar: "👩‍💼",
  },
  "en-US-GuyNeural": {
    name: "Guy",
    role: "Senior Compliance & Revenue Officer",
    tone: "Authoritative, Structured & Crisp",
    avatar: "👨‍💼",
  },
  "en-GB-SoniaNeural": {
    name: "Sonia",
    role: "International Trade & Customs Specialist",
    tone: "Polished, Measured & Articulate",
    avatar: "👩‍⚖️",
  },
  "en-KE-AsiliaNeural": {
    name: "Asilia",
    role: "East African Regional Tax Specialist",
    tone: "Warm, Clear & Regional",
    avatar: "👩‍💼",
  },
  "salt_eng_0001": {
    name: "Mugisha",
    role: "National Tax Education Officer",
    tone: "Ugandan Accent, Engaging & Natural",
    avatar: "👨‍🎓",
  },
  "salt_eng_0002": {
    name: "Achieng",
    role: "Domestic Taxes & PAYE Advisor",
    tone: "Ugandan Accent, Warm & Reassuring",
    avatar: "👩‍💼",
  },
  "salt_eng_0003": {
    name: "Kizza",
    role: "Customs & Excise Guidance Officer",
    tone: "Ugandan Accent, Crisp & Steady",
    avatar: "👨‍✈️",
  },

  // Luganda Personas
  "salt_lug_0001": {
    name: "Nakato",
    role: "Omubuulirizi w'Emisolo (Lead Luganda Advisor)",
    tone: "Mpolamu, Ntegeevu era Ntuufu (Gentle & Authoritative)",
    avatar: "👩‍🌾",
  },
  "waxal_lug_0002": {
    name: "Kato",
    role: "Omukugu w'Emmotoka n'Ebyobusuubuzi (Trade Officer)",
    tone: "Mwangu era Ayanguya (Direct & Expressive)",
    avatar: "👨‍💼",
  },
  "waxal_lug_0003": {
    name: "Babirye",
    role: "Omuweereza w'Ebyemisolo (Tax Services Specialist)",
    tone: "Ntegeevu era Ennyonnyola (Articulate & Clear)",
    avatar: "👩‍💻",
  },
  "waxal_lug_0004": {
    name: "Nalubega",
    role: "Omuweereza w'Abasuubuzi Abalala (Vendor Support)",
    tone: "Wadde nga Mukwano (Patient & Engaging)",
    avatar: "👩‍💼",
  },
  "waxal_lug_0005": {
    name: "Mukasa",
    role: "Omukebezi w'Ebyensimbi (Financial Auditor)",
    tone: "Mwangu era Omukakafu (Firm & Accurate)",
    avatar: "👨‍⚖️",
  },
  "waxal_lug_0006": {
    name: "Ssemakula",
    role: "Omukugu w'Eby'oku Mwalo (Customs & Border Advisor)",
    tone: "Muteefu era Mwesigwa (Steady & Reliable)",
    avatar: "👨‍✈️",
  },
  "waxal_lug_0007": {
    name: "Namukasa",
    role: "Omukugu wa EFRIS ne VAT (Digital Invoicing Specialist)",
    tone: "Mangu era Ntegeevu (Crisp & Clear)",
    avatar: "👩‍💻",
  },
  "waxal_lug_0008": {
    name: "Wasswa",
    role: "Omuwabuzi w'Abasuubuzi Abato (Small Business Advisor)",
    tone: "Wa Mukwano era Ayamba (Friendly & Supportive)",
    avatar: "👨‍🎓",
  },

  // Swahili Personas
  "waxal_swa_0006": {
    name: "Baraka",
    role: "Afisa Ushuru na Forodha (EAC Customs Officer)",
    tone: "Rasmi, Fasaha na Rafiki (Official & Fluent)",
    avatar: "👨‍✈️",
  },
  "waxal_swa_0007": {
    name: "Amina",
    role: "Mshauri wa Biashara Mpakani (Cross-Border Advisor)",
    tone: "Mpole na Mwenye Kuelekeza (Supportive & Articulate)",
    avatar: "👩‍💼",
  },

  // Runyankole / Rukiga Personas
  "salt_nyn_0001": {
    name: "Tumusiime",
    role: "Omwegyesa w'Emisoro (Regional Advisor)",
    tone: "Ow'oburinganiza (Clear & Accessible)",
    avatar: "👨‍💼",
  },
  "waxal_nyn_0003": {
    name: "Kyomugisha",
    role: "Omuhabuzi w'Obushuubuzi n'Emishoro (Business Tax Advisor)",
    tone: "Ow'embabazi kandi Orwanisa (Warm & Articulate)",
    avatar: "👩‍💼",
  },
  "waxal_nyn_0004": {
    name: "Mbabazi",
    role: "Omukugu wa EFRIS na VAT (EFRIS & VAT Specialist)",
    tone: "Orikushoboorora Gye (Crisp & Structured)",
    avatar: "👩‍💻",
  },
  "waxal_nyn_0007": {
    name: "Byaruhanga",
    role: "Omuhabuzi w'Emishoro y'Ebyombeko (Rental & Property Advisor)",
    tone: "Omuhami kandi Orikuhurikiza (Calm & Authoritative)",
    avatar: "👨‍⚖️",
  },
  "waxal_nyn_0008": {
    name: "Kansiime",
    role: "Omuhwezi w'Abashasuzi b'Emishoro (Taxpayer Support Officer)",
    tone: "Ow'omukago kandi Orahuka (Friendly & Responsive)",
    avatar: "👩‍🎓",
  },

  // Acholi Personas
  "salt_ach_0001": {
    name: "Laker",
    role: "Lapony me Culu Mucoro (Community Outreach)",
    tone: "Maber dok Maleng (Clear & Direct)",
    avatar: "👩‍🏫",
  },
  "waxal_ach_0001": {
    name: "Okello",
    role: "Lami Tam i kom Mucoro ki Biacara (Business Tax Officer)",
    tone: "upore dok Gene (Steady & Trustworthy)",
    avatar: "👨‍💼",
  },
  "waxal_ach_0005": {
    name: "Akello",
    role: "Ladiro Lok me EFRIS ki VAT (EFRIS & VAT Specialist)",
    tone: "Maleng dok Oyot (Articulate & Prompt)",
    avatar: "👩‍💻",
  },
  "waxal_ach_0006": {
    name: "Odong",
    role: "Latic me Mucoro me Wang Lobo (Customs & Border Officer)",
    tone: "Matek dok Atir (Firm & Clear)",
    avatar: "👨‍✈️",
  },
  "waxal_ach_0008": {
    name: "Aber",
    role: "Lakony Lutim Biacara Matino (Small Business Support)",
    tone: "Kica dok Yomcwiny (Empathetic & Patient)",
    avatar: "👩‍💼",
  },
};

/**
 * Display name for a speaker with human persona role branding.
 */
export function voiceDisplayName(locale: string, voice: VoiceOption, index: number): string {
  const persona = VOICE_PERSONAS[voice.id];
  const prefix = voice.provider === "edge_tts" ? "" : `Voice ${index + 1}: `;
  if (persona) {
    return `${prefix}${persona.avatar} ${persona.name} — ${persona.role}`;
  }
  if (voice.provider === "edge_tts") {
    const parts = voice.id.split("-");
    const name = parts[2]?.replace(/Neural$/, "") ?? voice.id;
    const region = parts.slice(0, 2).join("-");
    return `${name} (${region})`;
  }
  return `Voice ${index + 1}`;
}

export function voicePersonaInfo(voiceId: string): VoicePersona | null {
  return VOICE_PERSONAS[voiceId] || null;
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
