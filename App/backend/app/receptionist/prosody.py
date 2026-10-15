"""Acoustic prosody & emotion analyzer for live voice receptionist turns (2026).

Extracts zero-VRAM acoustic paralinguistic features (RMS energy, zero-crossing
rate, frame energy variance, and speech rate) from 16 kHz PCM16 caller audio
and fuses them with lexical crisis/frustration signals to condition TTS pacing
and empathy guidance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

import numpy as np

from ..text_signals import detect_crisis

logger = logging.getLogger(__name__)

_FRUSTRATION_CUES_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:not\s+working|wrong|unfair|delayed|still\s+waiting|angry|frustrated|rejected|"
    r"penalty\s+is\s+too\s+high|why\s+did\s+you|useless|terikola|ebizibu| bukya|"
    r"kuzibu|tatizo|shida|haifanyi\s+kazi|imekataa|"
    r"tikirikukora|obuzibu|pe\s+tiyo|peko)\b",
    re.IGNORECASE,
)

_CONFUSION_CUES_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:i\s+don'?t\s+understand|confused|what\s+does\s+that\s+mean|how\s+do\s+i\s+start|"
    r"not\s+sure|sitegedde|simanyi|sielewi|sijui|"
    r"tinyetegyereize|timmanyire|pe\s+aniang|pe\s+angeyo)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProsodyProfile:
    """Acoustic and lexical affect profile for a single caller utterance."""

    caller_affect: str = "calm"  # "calm" | "confused" | "frustrated" | "urgent_distress"
    rms_energy: float = 0.0
    energy_variance: float = 0.0
    zero_crossing_rate: float = 0.0
    words_per_second: float = 0.0
    recommended_speech_rate: float = 1.0
    prosody_tag: str = "<neutral>"
    dictation_pause_ms: int = 120


def analyze_utterance_prosody(
    pcm16_bytes: bytes,
    *,
    sample_rate: int = 16000,
    text: str = "",
    locale: str = "en",
    clarifications_asked: int = 0,
) -> ProsodyProfile:
    """Compute acoustic prosody metrics and classify caller affect."""
    clean_text = (text or "").strip()
    if detect_crisis(clean_text):
        return ProsodyProfile(
            caller_affect="urgent_distress",
            recommended_speech_rate=0.88,
            prosody_tag="<calm_reassuring>",
            dictation_pause_ms=260,
        )

    rms = 0.0
    energy_var = 0.0
    zcr = 0.0
    duration_s = 0.0

    if pcm16_bytes and len(pcm16_bytes) >= 4:
        usable_len = len(pcm16_bytes) - (len(pcm16_bytes) % 2)
        samples = np.frombuffer(pcm16_bytes[:usable_len], dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size > 0:
            duration_s = samples.size / max(1, sample_rate)
            rms = float(np.sqrt(np.mean(samples * samples)))
            signs = np.signbit(samples)
            if signs.size > 1:
                zcr = float(np.mean(signs[:-1] != signs[1:]))
            frame_len = max(160, sample_rate // 50)  # 20ms frames
            n_frames = samples.size // frame_len
            if n_frames >= 2:
                framed = samples[: n_frames * frame_len].reshape(n_frames, frame_len)
                frame_rms = np.sqrt(np.mean(framed * framed, axis=1))
                energy_var = float(np.std(frame_rms))

    words = len(clean_text.split()) if clean_text else 0
    wps = round(words / duration_s, 2) if duration_s > 0.25 and words > 0 else 0.0

    has_frustration = bool(_FRUSTRATION_CUES_RE.search(clean_text))
    has_confusion = bool(_CONFUSION_CUES_RE.search(clean_text)) or clarifications_asked >= 2
    high_arousal = rms >= 0.18 and (energy_var >= 0.08 or wps >= 3.8)

    if has_frustration and high_arousal:
        affect = "urgent_distress"
        rate = 0.88
        tag = "<calm_reassuring>"
        pause_ms = 240
    elif has_frustration or high_arousal:
        affect = "frustrated"
        rate = 0.92
        tag = "<empathetic_steady>"
        pause_ms = 200
    elif has_confusion or (0.0 < wps < 1.1 and words >= 2):
        affect = "confused"
        rate = 0.92
        tag = "<patient_clear>"
        pause_ms = 200
    else:
        affect = "calm"
        rate = 1.0
        tag = "<neutral>"
        pause_ms = 120

    return ProsodyProfile(
        caller_affect=affect,
        rms_energy=round(rms, 4),
        energy_variance=round(energy_var, 4),
        zero_crossing_rate=round(zcr, 4),
        words_per_second=wps,
        recommended_speech_rate=rate,
        prosody_tag=tag,
        dictation_pause_ms=pause_ms,
    )


def empathetic_opening_for(affect: str, locale: str = "en") -> str:
    """Return a brief, culturally appropriate empathy opening when caller is frustrated or confused."""
    loc = (locale or "en").lower()
    if affect in ("frustrated", "urgent_distress"):
        return {
            "lg": "Ntegedde obuzibu bw'oyitamu, ka tukikoleko mangu. ",
            "sw": "Naelewa changamoto unayopitia, wacha tuishughulikie mara moja. ",
            "nyn": "Nyetegyereza obuzibu obu orikurabamu, ka tukikoreho ahonaaho. ",
            "ach": "Aniang peko ma itye ka wok ki iye, wek wati iye oyot-oyot. ",
            "en": "I understand this can be frustrating; let us resolve it step by step. ",
        }.get(loc, "I understand this can be frustrating; let us resolve it step by step. ")
    if affect == "confused":
        return {
            "lg": "Tofaayo, ka nkunnyonnyole mu mitendera emyangu. ",
            "sw": "Usijali, wacha nikuelezee kwa hatua rahisi. ",
            "nyn": "Otafaayo, ka nkushoboororere omu madaara gworobi. ",
            "ach": "Pe ipar, wek atit boti i rwom mayot. ",
            "en": "No problem, let me walk you through it clearly. ",
        }.get(loc, "No problem, let me walk you through it clearly. ")
    return ""
