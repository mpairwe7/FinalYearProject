"""Build the spoken language-identification set for the receptionist (Phase 0A).

Downloads held-out *test* splits only — never train — so a model fine-tuned
on these corpora is not being graded on its own training data:

| label | source                      | what it is                                   |
|-------|-----------------------------|----------------------------------------------|
| en    | afrispeech_luganda          | English read by Luganda first-language speakers |
| en    | afrispeech_swahili          | English read by Swahili first-language speakers |
| en    | salt_eng                    | Sunbird SALT, Ugandan English speakers        |
| lg    | fleurs_lg_ug                | Google FLEURS Luganda                         |
| lg    | salt_lug                    | Sunbird SALT Luganda                          |
| sw    | fleurs_sw_ke                | Google FLEURS Swahili                         |

The accented-English sources exist to measure the costliest error: an English
caller moved off the English engine because their accent reads as Luganda.

Each source utterance becomes three duration variants (a 1.2 s crop, a 3.0 s
crop, and the full clip) and each variant two channels: ``16k`` as recorded
and ``8k`` (band-limited to 8 kHz and back, a phone line). Crops start at the
first voiced frame, so a crop is speech, not leading silence.

The audio is written under ``--out`` (default ``evals/language_id/data``,
git-ignored — AfriSpeech is CC-BY-NC-SA and is not redistributed here); the
manifest (``manifest.jsonl``) is committed and regenerates identically: the
selection is seeded.

Needs ``HF_TOKEN`` for Sunbird/salt (gated, auto-approved). Run inside the
``app-api:gpu`` image — see ``evals/language_id/README.md``.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import random
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger("lid.build")

SR = 16000
PHONE_SR = 8000
CROPS_S: tuple[float | None, ...] = (1.2, 3.0, None)  # None = full clip
HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Utterance:
    uid: str
    label: str
    source: str
    text: str
    audio: np.ndarray  # float32 mono at SR


def _to_mono_16k(samples: np.ndarray, sr: int) -> np.ndarray:
    import torch
    import torchaudio.functional as F

    x = np.asarray(samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if sr != SR:
        x = F.resample(torch.from_numpy(x), sr, SR).numpy()
    return x.astype(np.float32)


def _decode(data: bytes) -> np.ndarray:
    import soundfile as sf

    samples, sr = sf.read(io.BytesIO(data), dtype="float32")
    return _to_mono_16k(samples, sr)


def _download(repo: str, filename: str) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(hf_hub_download(repo_id=repo, repo_type="dataset", filename=filename))


def _fleurs(config: str, label: str, n: int, rng: random.Random) -> Iterator[Utterance]:
    tsv = _download("google/fleurs", f"data/{config}/test.tsv")
    rows = list(csv.reader(tsv.open(encoding="utf-8"), delimiter="\t"))
    # FLEURS repeats a sentence across speakers; keep one reading per sentence.
    by_sentence: dict[str, list[str]] = {}
    for row in rows:
        by_sentence.setdefault(row[0], row)
    chosen = rng.sample(sorted(by_sentence.values()), min(n, len(by_sentence)))
    wanted = {row[1]: row for row in chosen}
    archive = _download("google/fleurs", f"data/{config}/audio/test.tar.gz")
    with tarfile.open(archive) as tar:
        for member in tar:
            name = Path(member.name).name
            row = wanted.get(name)
            if row is None or not member.isfile():
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            yield Utterance(f"fleurs_{config}_{row[0]}", label, f"fleurs_{config}", row[2], _decode(fh.read()))


def _salt(config: str, label: str, n: int, rng: random.Random) -> Iterator[Utterance]:
    import pandas as pd

    parquet = _download("Sunbird/salt", f"{config}/test-00000-of-00001.parquet")
    df = pd.read_parquet(parquet)
    text_col = next(c for c in ("text", "transcription", "sentence") if c in df.columns)
    idx = rng.sample(range(len(df)), min(n, len(df)))
    for i in idx:
        row = df.iloc[i]
        audio = row["audio"]
        if isinstance(audio, dict) and audio.get("bytes"):
            samples = _decode(audio["bytes"])
        elif isinstance(audio, dict) and audio.get("array") is not None:
            samples = _to_mono_16k(np.asarray(audio["array"]), int(audio.get("sampling_rate", SR)))
        else:
            continue
        uid = str(row.get("id", i))
        yield Utterance(f"{config}_{uid}", label, config.replace("multispeaker-", "salt_"), str(row[text_col]), samples)


def _afrispeech(accent: str, n: int, rng: random.Random) -> Iterator[Utterance]:
    transcripts = _download("intronhealth/afrispeech-200", f"transcripts/{accent}/test.csv")
    with transcripts.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_file = {Path(r.get("audio_paths", "")).name: r for r in rows if r.get("audio_paths")}
    archive = _download("intronhealth/afrispeech-200", f"audio/{accent}/test/test_{accent}_0.tar.gz")
    members: list[tuple[str, bytes]] = []
    with tarfile.open(archive) as tar:
        for member in tar:
            name = Path(member.name).name
            if not member.isfile() or name not in by_file:
                continue
            fh = tar.extractfile(member)
            if fh is not None:
                members.append((name, fh.read()))
    for name, data in rng.sample(members, min(n, len(members))):
        row = by_file[name]
        yield Utterance(
            f"afrispeech_{accent}_{Path(name).stem}", "en", f"afrispeech_{accent}",
            row.get("transcript", ""), _decode(data),
        )


def _speech_onset(x: np.ndarray, frame: int = 320) -> int:
    """Index of the first 20 ms frame loud enough to be speech."""
    if len(x) < frame:
        return 0
    n = len(x) // frame
    rms = np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1))
    floor = 0.1 * float(np.percentile(rms, 95))
    voiced = np.nonzero(rms > floor)[0]
    return int(voiced[0] * frame) if len(voiced) else 0


def _phone_band(x: np.ndarray) -> np.ndarray:
    import torch
    import torchaudio.functional as F

    t = torch.from_numpy(x)
    return F.resample(F.resample(t, SR, PHONE_SR), PHONE_SR, SR).numpy().astype(np.float32)


def _write_wav(path: Path, x: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.clip(x, -1.0, 1.0), SR, subtype="PCM_16")


def _bucket(duration_s: float) -> str:
    if duration_s < 1.5:
        return "<1.5s"
    if duration_s <= 4.0:
        return "1.5-4s"
    return ">4s"


def build(out: Path, per_source: int, seed: int) -> list[dict[str, object]]:
    rng = random.Random(seed)
    sources: list[Iterator[Utterance]] = [
        _afrispeech("luganda", per_source, rng),
        _afrispeech("swahili", per_source // 2, rng),
        _salt("multispeaker-eng", "en", per_source, rng),
        _fleurs("lg_ug", "lg", per_source, rng),
        _salt("multispeaker-lug", "lg", per_source, rng),
        _fleurs("sw_ke", "sw", per_source * 2, rng),
    ]
    manifest: list[dict[str, object]] = []
    for source in sources:
        for utt in source:
            onset = _speech_onset(utt.audio)
            for crop in CROPS_S:
                if crop is None:
                    clip, variant = utt.audio, "full"
                else:
                    end = onset + int(crop * SR)
                    if end > len(utt.audio):
                        continue
                    clip, variant = utt.audio[onset:end], f"crop{crop:g}s"
                for channel, audio in (("16k", clip), ("8k", _phone_band(clip))):
                    rel = Path(utt.label) / utt.source / f"{utt.uid}_{variant}_{channel}.wav"
                    _write_wav(out / rel, audio)
                    duration = round(len(audio) / SR, 3)
                    entry = {
                        "path": rel.as_posix(),
                        "label": utt.label,
                        "duration_s": duration,
                        "bucket": _bucket(duration),
                        "source": utt.source,
                        "channel": channel,
                        "variant": variant,
                        "utterance": utt.uid,
                    }
                    # The reference transcript describes the whole utterance, so
                    # it rides on the uncropped rows only (join on "utterance").
                    if variant == "full":
                        entry["text"] = utt.text
                    manifest.append(entry)
        logger.info("built %d entries so far", len(manifest))
    return manifest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", type=Path, default=HERE / "data")
    ap.add_argument("--manifest", type=Path, default=HERE / "manifest.jsonl")
    ap.add_argument("--per-source", type=int, default=40, help="utterances per source (sw gets 2x, afrispeech_swahili 0.5x)")
    ap.add_argument("--seed", type=int, default=20260924)
    args = ap.parse_args()
    manifest = build(args.out, args.per_source, args.seed)
    with args.manifest.open("w", encoding="utf-8") as fh:
        for row in manifest:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("wrote %d entries to %s", len(manifest), args.manifest)


if __name__ == "__main__":
    main()
