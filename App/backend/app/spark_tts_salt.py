"""Sunbird/spark-tts-salt — local neural TTS for Ugandan languages (2026).

Sibling to :mod:`app.sunbird` (an external speech provider, imported lazily
by :mod:`app.speech_service`) rather than a member of the ``native_voice``
package: this is a batch full-utterance synthesizer, the same granularity as
Piper or edge-tts, not part of that package's sub-600ms streaming
architecture.

Architecture (from the model card, verified against the actual weights —
see the long comment on ``SALT_LANGUAGE_TOKEN_IDS`` in ``speech_service.py``
for why "verified", not "the README says", is the standard this file holds
itself to):

    text -> "{speaker_id}: <|task_tts|><|start_content|>{text}<|end_content|>"
         -> Sunbird/spark-tts-salt (a fine-tuned Qwen2-0.5B causal LM,
            loaded via plain ``transformers.AutoModelForCausalLM`` — no
            ``unsloth`` dependency; confirmed by inspecting
            ``LLM/config.json``: ``architectures: ["Qwen2ForCausalLM"]``)
         -> a token stream containing interleaved
            ``<|bicodec_global_N|>`` / ``<|bicodec_semantic_N|>`` ids
         -> BiCodec (from the *base* repo, unsloth/Spark-TTS-0.5B — the
            fine-tune ships only the LLM half) detokenizes those ids to a
            16 kHz waveform.

Verification status (2026-08-19, real GPU, real weights)
-----------------------------------------------------------
Loaded for real (LLM + BiCodec, ~80s cold) and synthesized all four mapped
locales end to end — lg/sw/ach/nyn all produced non-trivial audio (peak
0.72-0.99, RMS 0.07-0.08 — consistent with real speech, not silence or
clipped noise); ``en`` correctly raised :class:`SparkTtsUnavailable` rather
than guessing a speaker id. Two real bugs were found and fixed by this run,
neither visible from reading the model card (see the comments on the
``.to(self.device)`` calls and the ``global_ids`` shape in
:meth:`SparkTtsSalt.synthesize` for both). Not done: perceptual verification
— nobody has *listened* to the output to confirm pronunciation, prosody, or
which speaker a listener actually hears; the claim above is about the
pipeline running correctly and producing speech-shaped audio, not about
audio quality.

Tool-calling is unrelated to this file, but the same verification pass
checked it for the LLM swap sitting alongside this one — see
``App/docker-compose.local-sunflower.yml``'s comment if relevant to your
change.

Why :data:`SPARK_TTS_ENABLED` still needs an explicit local checkout
------------------------------------------------------------------------
Unlike Whisper-SALT (``speech_service.WHISPER_SALT_*``), which needed only
``transformers`` — already a hard dependency of this project — this model
needs BiCodec, which lives in a *separate* GitHub repository
(``github.com/SparkAudio/Spark-TTS``, Apache-2.0) that ships no
``setup.py``/``pyproject.toml``. It is not ``pip install``-able, so it
cannot be a normal ``requirements.txt`` entry, and vendoring roughly 3,800
lines of third-party inference code into this repository is a supply-chain
and license-provenance decision this file does not make unilaterally.
Instead, exactly like ``WHISPER_ADAPTER_LG`` pointing at a local checkout,
this tier activates only once an operator has cloned that repository to
:data:`SPARK_TTS_REPO_DIR` (see ``docs/runbooks/salt-speech-backends.md``
for the pinned-commit recipe) and installed its own requirements — its
absence is a normal, expected :class:`SparkTtsUnavailable`, not an error.

:data:`SPARK_TTS_ENABLED` defaults to **on**, matching Whisper-SALT now that
both are verified — but that alone changes nothing on a deployment without
the checkout above; :func:`load` still raises immediately on a missing
:data:`SPARK_TTS_REPO_DIR`. The flag exists for operators who *have* done
the checkout and want an explicit way to turn this tier back off.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

SPARK_TTS_ENABLED = os.getenv("SPARK_TTS_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
SPARK_TTS_MODEL = os.getenv("SPARK_TTS_MODEL", "Sunbird/spark-tts-salt")
# The fine-tune ships only the LLM; BiCodec (encoder/decoder + the wav2vec2
# feature extractor it conditions on) lives in the base repo.
SPARK_TTS_CODEC_REPO = os.getenv("SPARK_TTS_CODEC_REPO", "unsloth/Spark-TTS-0.5B")
# Local checkout of github.com/SparkAudio/Spark-TTS. Empty/missing means this
# tier never activates — see the module docstring for why it cannot simply
# be pip-installed.
SPARK_TTS_REPO_DIR = os.getenv("SPARK_TTS_REPO_DIR", "") or None
SPARK_TTS_DEVICE = os.getenv("SPARK_TTS_DEVICE", "") or None  # None -> autodetect in load()

# Speaker ids, from the model card's own training table — CONFIRMED for these
# four locales, each one of the languages the fine-tune's speaker-aware
# conditioning was actually trained on (id: language, gender):
#   241 Acholi (F)  242 Ateso (F)  243 Runyankore (F)
#   245 Lugbara (F)  246 Swahili (M)  248 Luganda (F)
# "Ateso" and "Lugbara" have no locale slot in this deployment's UI (same gap
# noted for the ASR SALT model's language tokens), so they are not mapped.
#
# The architecture section of the model card also claims "English (Ugandan
# accent)" as one of seven covered languages, but the training table lists
# only six speaker ids and none of them for English. Rather than guess which
# id (if any) was silently omitted from the table — the same mistake already
# caught and reverted for the ASR model's language-token ids — English is
# left unmapped. A caller with no id for its language gets a KeyError from
# :func:`speaker_id_for`, which the (single) caller in speech_service.py
# treats as "this tier does not cover this language" and falls through.
SPARK_TTS_SPEAKER_IDS: dict[str, int] = {}
for _locale, _default_id in (("lg", 248), ("sw", 246), ("ach", 241), ("nyn", 243)):
    _override = os.getenv(f"SPARK_TTS_SPEAKER_{_locale.upper()}", "")
    SPARK_TTS_SPEAKER_IDS[_locale] = int(_override) if _override else _default_id
del _locale, _default_id, _override


def speaker_id_for(language: str) -> int:
    """The speaker id for *language*, or raise ``KeyError`` if unmapped."""
    return SPARK_TTS_SPEAKER_IDS[language]


class SparkTtsUnavailable(RuntimeError):
    """The external checkout, its dependencies, or the weights are missing.

    Raised by :func:`load` and by :meth:`SparkTtsSalt.synthesize`. Callers
    treat this exactly like :class:`app.speech_service.LocalVoiceUnavailable`
    — a normal "this tier cannot answer", not a bug.
    """


_SEMANTIC_TOKEN_RE = re.compile(r"<\|bicodec_semantic_(\d+)\|>")
_GLOBAL_TOKEN_RE = re.compile(r"<\|bicodec_global_(\d+)\|>")


class SparkTtsSalt:
    """Loaded once by :meth:`load`; :meth:`synthesize` is the per-call path."""

    def __init__(self, model, tokenizer, audio_tokenizer, device, sample_rate: int) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.audio_tokenizer = audio_tokenizer
        self.device = device
        self.sample_rate = sample_rate

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        temperature: float = 0.8,
        top_k: int = 50,
        top_p: float = 1.0,
    ) -> tuple["np.ndarray", int]:
        """*text* -> (float32 mono samples, sample_rate).

        Raises :class:`SparkTtsUnavailable` for anything that means "this
        tier could not answer" (unmapped language, empty generation) so the
        caller's blanket ``except Exception`` in the fallback chain is
        sufficient — it does not need to distinguish this from any other
        backend failure.
        """
        try:
            speaker_id = speaker_id_for(language)
        except KeyError as exc:
            raise SparkTtsUnavailable(f"no speaker id mapped for locale {language!r}") from exc

        import torch

        prompt = "".join([
            f"{speaker_id}: ",
            "<|task_tts|>",
            "<|start_content|>",
            text,
            "<|end_content|>",
            "<|start_global_token|>",
        ])
        inputs = self.tokenizer([prompt], return_tensors="pt").to(self.device)
        with torch.no_grad():
            # Unlike a chat/instruction LLM, this fine-tune's only trained
            # task is text->bicodec-token-sequence; its generated text is
            # NEVER displayed, logged, or re-used as a prompt anywhere — the
            # regexes below extract ONLY <|bicodec_semantic_N|>/
            # <|bicodec_global_N|> numeric ids and discard everything else,
            # so there is no channel for injected "instructions" in `text` to
            # reach anything beyond which audio comes out (or a clean
            # SparkTtsUnavailable if no valid tokens appear). That's a
            # content/quality concern, not an LLM01 instruction-override one.
            # nosemgrep: ura-llm01-raw-user-input-to-llm
            generated = self.model.generate(
                **inputs,
                max_new_tokens=2048,
                do_sample=True,
                temperature=temperature,
                top_k=top_k,
                top_p=top_p,
                eos_token_id=self.tokenizer.eos_token_id,
            )
        output = self.tokenizer.batch_decode(
            generated[:, inputs["input_ids"].shape[1]:], skip_special_tokens=False,
        )[0]

        # Two real bugs found here by actual GPU verification, neither
        # visible from reading the model card:
        #
        # 1. .to(self.device) — the model card's own generate_speech()
        #    example builds these via bare torch.tensor(...).long(), no
        #    device placement, and never actually exercises detokenize() on
        #    a CUDA-loaded BiCodec in that example, so the gap survives
        #    there too. Without it, detokenize() raises "Expected all
        #    tensors to be on the same device, but found at least two
        #    devices, cuda:N and cpu" inside the VQ codebook embedding
        #    lookup. (Generation above succeeds either way — its inputs
        #    already went through .to(self.device) via the tokenizer call —
        #    so this only ever surfaces at the audio step.)
        #
        # 2. ONE unsqueeze(0) for global_ids, not two. BiCodecTokenizer.
        #    detokenize() — the method actually called below — does its own
        #    internal global_tokens.unsqueeze(1) before handing off to the
        #    inner model (see sparktts/models/audio_tokenizer.py), because
        #    its documented input contract is 2D: (batch_size, global_dim).
        #    The model card's generate_speech() example applies
        #    .unsqueeze(0) TWICE, producing a 3D (1, 1, N) tensor that
        #    becomes 4D after that internal unsqueeze — one dimension too
        #    many. It fails inside residual_fsq.py's einx codebook lookup
        #    ("q [c] d, b n q -> q b n d") with a shape-contradiction error
        #    (codebooks' q=1 vs indices' q=32) that gives no hint the actual
        #    problem is an extra leading dimension. semantic_ids' single
        #    unsqueeze(0) is correct as-is — its contract is already 2D,
        #    (batch_size, latent_dim), with no further unsqueeze downstream.
        semantic_ids = torch.tensor(
            [int(m) for m in _SEMANTIC_TOKEN_RE.findall(output)]
        ).long().unsqueeze(0).to(self.device)
        global_ids = torch.tensor(
            [int(m) for m in _GLOBAL_TOKEN_RE.findall(output)]
        ).long().unsqueeze(0).to(self.device)
        if semantic_ids.numel() == 0 or global_ids.numel() == 0:
            raise SparkTtsUnavailable("generation produced no BiCodec tokens")

        wav = self.audio_tokenizer.detokenize(global_ids, semantic_ids)
        return wav.astype("float32"), self.sample_rate


def load() -> SparkTtsSalt:
    """Build a ready :class:`SparkTtsSalt`, or raise :class:`SparkTtsUnavailable`.

    Every failure mode short of a genuine bug in this file is normal here:
    the flag is off, the checkout is missing, its ``sparktts`` package is
    missing, or one of ITS dependencies (``omegaconf``, ``einx``, ...) is
    missing. All of those collapse to the same signal for the caller.
    """
    if not SPARK_TTS_ENABLED:
        raise SparkTtsUnavailable("SPARK_TTS_ENABLED is not set")
    if not SPARK_TTS_REPO_DIR:
        raise SparkTtsUnavailable(
            "SPARK_TTS_REPO_DIR is not set — clone github.com/SparkAudio/Spark-TTS "
            "and point this at it; see docs/runbooks/salt-speech-backends.md"
        )
    repo_dir = Path(SPARK_TTS_REPO_DIR)
    if not (repo_dir / "sparktts").is_dir():
        raise SparkTtsUnavailable(f"no sparktts/ package found under {repo_dir}")

    try:
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SparkTtsUnavailable(f"transformers/torch/huggingface_hub missing: {exc}") from exc

    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    try:
        from sparktts.models.audio_tokenizer import BiCodecTokenizer  # type: ignore
    except ImportError as exc:
        raise SparkTtsUnavailable(
            f"sparktts import failed — its own requirements.txt "
            f"(torch/torchaudio/einx/einops/omegaconf/soundfile/soxr) may be "
            f"missing from this environment: {exc}"
        ) from exc

    device_name = SPARK_TTS_DEVICE or ("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(device_name)

    logger.info("Loading Spark-TTS-SALT '%s' (device=%s)", SPARK_TTS_MODEL, device_name)
    tokenizer = AutoTokenizer.from_pretrained(SPARK_TTS_MODEL)
    model = AutoModelForCausalLM.from_pretrained(SPARK_TTS_MODEL, dtype=torch.float32)
    model = model.to(device)
    model.eval()

    # BiCodecTokenizer does raw filesystem joins against model_dir (no HF repo
    # resolution of its own — see sparktts/models/audio_tokenizer.py), so the
    # codec repo has to be resolved to its local snapshot first.
    codec_dir = snapshot_download(SPARK_TTS_CODEC_REPO)
    audio_tokenizer = BiCodecTokenizer(model_dir=codec_dir, device=device)
    sample_rate = int(audio_tokenizer.config.get("sample_rate", 16000))

    logger.info("Spark-TTS-SALT ready (device=%s, sample_rate=%d)", device_name, sample_rate)
    return SparkTtsSalt(model, tokenizer, audio_tokenizer, device, sample_rate)
