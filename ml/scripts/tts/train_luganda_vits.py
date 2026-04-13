#!/usr/bin/env python3
"""Train a custom Luganda VITS voice using Coqui TTS.

Since no commercial-safe pretrained Luganda TTS exists in 2026
(MMS-TTS-lug is CC-BY-NC), this pipeline trains a single-speaker VITS
model on recorded Luganda voice data. The output is an ONNX-exportable
checkpoint that ships to mobile via ``export_tts_onnx.py``.

Data layout expected under ``Data/speech/tts/lg_voice/``::

    speaker_001/
      metadata.csv                       # | audio_file | text | normalised_text
      wavs/
        000001.wav
        000002.wav
        ...

Usage::

    # Dry run (no voice data required — validates config only)
    python -m ml.scripts.tts.train_luganda_vits --dry-run

    # Real training (requires GPU, 3-4 hours of clean speech, Coqui TTS installed)
    python -m ml.scripts.tts.train_luganda_vits \\
        --data-dir Data/speech/tts/lg_voice/speaker_001 \\
        --output-dir artifacts/speech/tts/luganda_vits

Commercial-safety: Coqui TTS is MPL-2.0 (commercial use allowed with source
disclosure for any modifications). The trained voice model inherits the
speaker's consent terms — see ``Data/speech/tts/lg_voice/README.md``.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("tts.train_luganda_vits")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR_DEFAULT = PROJECT_ROOT / "Data" / "speech" / "tts" / "lg_voice" / "speaker_001"
OUTPUT_DIR_DEFAULT = PROJECT_ROOT / "artifacts" / "speech" / "tts" / "luganda_vits"


@dataclass
class VitsConfig:
    audio_sample_rate: int = 22050
    batch_size: int = 16
    eval_batch_size: int = 4
    num_epochs: int = 1000
    learning_rate: float = 1e-4
    grad_clip: float = 5.0
    num_loader_workers: int = 4
    use_phonemes: bool = True
    phoneme_language: str = "sw"  # Swahili — closest espeak-ng approximation for Luganda
    phoneme_cache_path: str = "phoneme_cache"
    text_cleaner: str = "phoneme_cleaners"
    save_step: int = 1000
    print_step: int = 25
    mixed_precision: bool = True
    run_name: str = "luganda_vits_v1"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunStats:
    n_clips: int = 0
    total_hours: float = 0.0
    speaker_ids: list[str] = field(default_factory=list)
    consent_version: str | None = None


@dataclass
class RunResult:
    data_dir: str
    output_dir: str
    config: dict[str, Any]
    stats: RunStats
    ok: bool = False
    error: str | None = None


def _probe_dataset(data_dir: Path) -> RunStats:
    """Scan metadata.csv + wavs/ to report the dataset shape."""
    stats = RunStats()
    if not data_dir.exists():
        return stats
    metadata = data_dir / "metadata.csv"
    wavs_dir = data_dir / "wavs"
    if not metadata.exists() or not wavs_dir.exists():
        return stats

    try:
        import soundfile as sf  # type: ignore

        have_sf = True
    except ImportError:
        have_sf = False

    total_s = 0.0
    n = 0
    with open(metadata, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            wav_rel = parts[0]
            wav_path = (
                wavs_dir / f"{wav_rel}.wav" if not wav_rel.endswith(".wav") else wavs_dir / wav_rel
            )
            if not wav_path.exists():
                continue
            n += 1
            if have_sf:
                try:
                    info = sf.info(str(wav_path))
                    total_s += float(info.duration)
                except Exception:
                    pass
    stats.n_clips = n
    stats.total_hours = round(total_s / 3600, 3)
    stats.speaker_ids = [data_dir.name]
    # Consent tracking — the consent file lives next to metadata.csv.
    consent_path = data_dir / "CONSENT.yaml"
    if consent_path.exists():
        try:
            import yaml  # type: ignore

            consent = yaml.safe_load(consent_path.read_text())
            stats.consent_version = (consent or {}).get("version")
        except Exception:
            pass
    return stats


def run(
    *,
    data_dir: Path,
    output_dir: Path,
    dry_run: bool,
) -> RunResult:
    cfg = VitsConfig()
    stats = _probe_dataset(data_dir)
    result = RunResult(
        data_dir=str(data_dir),
        output_dir=str(output_dir),
        config=cfg.as_dict(),
        stats=stats,
    )

    if dry_run:
        log.info(
            "[dry-run] config OK. data_dir=%s n_clips=%d hours=%.2f consent=%s",
            data_dir,
            stats.n_clips,
            stats.total_hours,
            stats.consent_version,
        )
        if stats.n_clips == 0:
            log.warning(
                "no voice data found — record and place wavs under %s/wavs/ + metadata.csv",
                data_dir,
            )
        result.ok = True
        return result

    if stats.n_clips == 0:
        result.error = f"no voice data under {data_dir} — see Data/speech/tts/lg_voice/README.md"
        log.error(result.error)
        return result

    if stats.consent_version is None:
        result.error = (
            f"voice data lacks a CONSENT.yaml under {data_dir} — speaker consent is mandatory"
        )
        log.error(result.error)
        return result

    # Consent check passed. Now attempt the real Coqui TTS training run.
    try:
        from trainer import Trainer, TrainerArgs  # type: ignore
        from TTS.config.shared_configs import BaseDatasetConfig  # type: ignore
        from TTS.tts.configs.vits_config import VitsConfig as CoquiVitsConfig  # type: ignore
        from TTS.tts.datasets import load_tts_samples  # type: ignore
        from TTS.tts.models.vits import Vits  # type: ignore
        from TTS.tts.utils.text.tokenizer import TTSTokenizer  # type: ignore
        from TTS.utils.audio import AudioProcessor  # type: ignore
    except ImportError as exc:
        result.error = f"Coqui TTS not installed: {exc}. pip install TTS trainer"
        log.error(result.error)
        return result

    output_dir.mkdir(parents=True, exist_ok=True)

    dataset_config = BaseDatasetConfig(
        formatter="ljspeech",
        meta_file_train="metadata.csv",
        path=str(data_dir),
        language="lg",
    )

    config = CoquiVitsConfig(
        audio={"sample_rate": cfg.audio_sample_rate},
        run_name=cfg.run_name,
        batch_size=cfg.batch_size,
        eval_batch_size=cfg.eval_batch_size,
        num_loader_workers=cfg.num_loader_workers,
        num_eval_loader_workers=2,
        run_eval=True,
        test_delay_epochs=-1,
        epochs=cfg.num_epochs,
        lr=cfg.learning_rate,
        text_cleaner=cfg.text_cleaner,
        use_phonemes=cfg.use_phonemes,
        phoneme_language=cfg.phoneme_language,
        phoneme_cache_path=str(output_dir / cfg.phoneme_cache_path),
        output_path=str(output_dir),
        datasets=[dataset_config],
        save_step=cfg.save_step,
        print_step=cfg.print_step,
        mixed_precision=cfg.mixed_precision,
    )

    ap = AudioProcessor.init_from_config(config)
    tokenizer, config = TTSTokenizer.init_from_config(config)
    train_samples, eval_samples = load_tts_samples(
        dataset_config, eval_split=True, eval_split_max_size=32, eval_split_size=0.05
    )
    model = Vits(config, ap, tokenizer)

    trainer = Trainer(
        TrainerArgs(),
        config,
        str(output_dir),
        model=model,
        train_samples=train_samples,
        eval_samples=eval_samples,
    )
    try:
        trainer.fit()
    except Exception as exc:
        log.exception("VITS training failed")
        result.error = str(exc)
        return result

    manifest = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "data_dir": str(data_dir),
        "config": cfg.as_dict(),
        "stats": asdict(stats),
    }
    (output_dir / "training_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    result.ok = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    result = run(data_dir=args.data_dir, output_dir=args.output_dir, dry_run=args.dry_run)
    print(
        json.dumps(
            {
                "data_dir": result.data_dir,
                "output_dir": result.output_dir,
                "config": result.config,
                "stats": asdict(result.stats),
                "ok": result.ok,
                "error": result.error,
            },
            indent=2,
        )
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
