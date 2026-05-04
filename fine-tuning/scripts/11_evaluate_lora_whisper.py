#!/usr/bin/env python3
"""Step 11: Evaluate Whisper LoRA adapters per language (WER metric).

Compares base Whisper-small vs fine-tuned adapter on synthesized speech.
Uses existing synthesized audio from fine-tuning/data/cv_{lang}/audio/.

Metrics: Word Error Rate (WER), Character Error Rate (CER).

Usage:
    python fine-tuning/scripts/11_evaluate_lora_whisper.py --languages lg sw nyn
    python fine-tuning/scripts/11_evaluate_lora_whisper.py --languages sw --max-samples 20
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = PROJECT_ROOT / "Results"

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-small")

LANG_NAMES = {"lg": "Luganda", "sw": "Swahili", "nyn": "Runyankole"}

ADAPTER_DIRS = {
    "lg": BASE_DIR / "adapters" / "whisper-lg",
    "sw": BASE_DIR / "adapters" / "whisper-sw",
    "nyn": BASE_DIR / "adapters" / "whisper-nyn",
}


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate using dynamic programming."""
    ref_words = reference.lower().split()
    hyp_words = hypothesis.lower().split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    d = [[0] * (len(hyp_words) + 1) for _ in range(len(ref_words) + 1)]
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])

    return d[len(ref_words)][len(hyp_words)] / len(ref_words)


def compute_cer(reference: str, hypothesis: str) -> float:
    """Compute Character Error Rate."""
    ref_chars = list(reference.lower().replace(" ", ""))
    hyp_chars = list(hypothesis.lower().replace(" ", ""))

    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0

    d = [[0] * (len(hyp_chars) + 1) for _ in range(len(ref_chars) + 1)]
    for i in range(len(ref_chars) + 1):
        d[i][0] = i
    for j in range(len(hyp_chars) + 1):
        d[0][j] = j

    for i in range(1, len(ref_chars) + 1):
        for j in range(1, len(hyp_chars) + 1):
            if ref_chars[i - 1] == hyp_chars[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])

    return d[len(ref_chars)][len(hyp_chars)] / len(ref_chars)


def load_test_data(lang: str, max_samples: int) -> list[dict]:
    """Load audio + transcript pairs for evaluation."""
    import soundfile as sf

    data_dir = BASE_DIR / "data" / f"cv_{lang}"
    audio_dir = data_dir / "audio"
    meta_path = data_dir / "metadata.jsonl"

    if not audio_dir.exists() or not meta_path.exists():
        logger.warning("[%s] No test data at %s", lang, data_dir)
        return []

    metadata = []
    with open(meta_path) as f:
        for line in f:
            metadata.append(json.loads(line))

    # Use last N samples as test set (training used the first ones)
    test_start = max(0, len(metadata) - max_samples)
    test_meta = metadata[test_start:]

    samples = []
    for entry in test_meta:
        idx = entry.get("id", 0)
        sentence = entry.get("sentence", "").strip()
        if not sentence:
            continue

        wav_path = audio_dir / f"{lang}_{idx:06d}.wav"
        if not wav_path.exists():
            continue

        try:
            audio, sr = sf.read(str(wav_path))
            if sr != 16000:
                import librosa
                audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
            samples.append({
                "audio": audio,
                "reference": sentence,
                "file": wav_path.name,
                "source": entry.get("source", "unknown"),
            })
        except Exception:
            continue

    logger.info("[%s] Loaded %d test samples from %s", lang, len(samples), audio_dir)
    return samples


def transcribe_with_model(model, processor, audio_array, device) -> tuple[str, float]:
    """Transcribe audio array, return (text, latency_s)."""
    import torch

    t0 = time.perf_counter()
    input_features = processor.feature_extractor(
        audio_array, sampling_rate=16000, return_tensors="pt",
    ).input_features.to(device, dtype=model.dtype)

    with torch.no_grad():
        predicted_ids = model.generate(input_features, max_new_tokens=225)
    text = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0].strip()
    latency = time.perf_counter() - t0

    return text, latency


def evaluate_language(lang: str, max_samples: int = 50):
    """Evaluate base vs fine-tuned Whisper for one language."""
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    lang_name = LANG_NAMES.get(lang, lang)
    adapter_dir = ADAPTER_DIRS.get(lang)

    logger.info("=" * 60)
    logger.info("EVALUATING WHISPER: %s (%s)", lang_name, lang)
    logger.info("=" * 60)

    samples = load_test_data(lang, max_samples)
    if not samples:
        logger.error("[%s] No test data available", lang)
        return None

    # Load base model
    logger.info("[%s] Loading base Whisper: %s", lang, WHISPER_MODEL)
    processor = WhisperProcessor.from_pretrained(WHISPER_MODEL)
    base_model = WhisperForConditionalGeneration.from_pretrained(
        WHISPER_MODEL, torch_dtype=torch.float16, device_map="auto",
    )
    base_model.config.forced_decoder_ids = None
    base_model.config.suppress_tokens = []
    base_model.eval()
    device = next(base_model.parameters()).device

    # Evaluate base model
    logger.info("[%s] Evaluating BASE Whisper (%d samples)...", lang, len(samples))
    base_results = []
    for i, sample in enumerate(samples):
        hyp, latency = transcribe_with_model(base_model, processor, sample["audio"], device)
        wer = compute_wer(sample["reference"], hyp)
        cer = compute_cer(sample["reference"], hyp)
        base_results.append({
            "reference": sample["reference"],
            "hypothesis": hyp,
            "file": sample["file"],
            "wer": round(wer, 4),
            "cer": round(cer, 4),
            "latency_s": round(latency, 2),
        })
        if (i + 1) % 10 == 0:
            avg_wer = sum(r["wer"] for r in base_results) / len(base_results)
            logger.info("  [%s/base] %d/%d avg_WER=%.3f", lang, i + 1, len(samples), avg_wer)

    # Evaluate fine-tuned model
    ft_results = []
    if adapter_dir and adapter_dir.exists():
        logger.info("[%s] Loading fine-tuned adapter from %s", lang, adapter_dir)
        try:
            from peft import PeftModel
            ft_model = PeftModel.from_pretrained(base_model, str(adapter_dir))
            ft_model = ft_model.merge_and_unload()
            ft_model.eval()

            logger.info("[%s] Evaluating FINE-TUNED Whisper (%d samples)...", lang, len(samples))
            for i, sample in enumerate(samples):
                hyp, latency = transcribe_with_model(ft_model, processor, sample["audio"], device)
                wer = compute_wer(sample["reference"], hyp)
                cer = compute_cer(sample["reference"], hyp)
                ft_results.append({
                    "reference": sample["reference"],
                    "hypothesis": hyp,
                    "file": sample["file"],
                    "wer": round(wer, 4),
                    "cer": round(cer, 4),
                    "latency_s": round(latency, 2),
                })
                if (i + 1) % 10 == 0:
                    avg_wer = sum(r["wer"] for r in ft_results) / len(ft_results)
                    logger.info("  [%s/ft] %d/%d avg_WER=%.3f", lang, i + 1, len(samples), avg_wer)

            del ft_model
        except Exception as e:
            logger.error("[%s] Adapter eval failed: %s", lang, e)
    else:
        logger.warning("[%s] No adapter at %s", lang, adapter_dir)

    del base_model
    torch.cuda.empty_cache()

    # Summarize
    def summarize(results: list[dict]) -> dict:
        if not results:
            return {}
        return {
            "wer_mean": round(sum(r["wer"] for r in results) / len(results), 4),
            "cer_mean": round(sum(r["cer"] for r in results) / len(results), 4),
            "latency_mean_s": round(sum(r["latency_s"] for r in results) / len(results), 2),
            "n_samples": len(results),
        }

    base_summary = summarize(base_results)
    ft_summary = summarize(ft_results)

    improvement = {}
    if base_summary and ft_summary:
        base_wer = base_summary["wer_mean"]
        ft_wer = ft_summary["wer_mean"]
        if base_wer > 0:
            improvement["wer_reduction_pct"] = round((base_wer - ft_wer) / base_wer * 100, 1)
        base_cer = base_summary["cer_mean"]
        ft_cer = ft_summary["cer_mean"]
        if base_cer > 0:
            improvement["cer_reduction_pct"] = round((base_cer - ft_cer) / base_cer * 100, 1)

    evaluation = {
        "language": lang,
        "language_name": lang_name,
        "base_model": WHISPER_MODEL,
        "adapter_dir": str(adapter_dir) if adapter_dir else None,
        "base_summary": base_summary,
        "fine_tuned_summary": ft_summary,
        "improvement": improvement,
        "base_results": base_results,
        "fine_tuned_results": ft_results,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"eval_whisper_{lang}.json"
    with open(out_path, "w") as f:
        json.dump(evaluation, f, indent=2, ensure_ascii=False)
    logger.info("[%s] Results saved to %s", lang, out_path)

    logger.info("\n--- %s WHISPER SUMMARY ---", lang_name.upper())
    logger.info("  Base:       WER=%.3f  CER=%.3f",
                base_summary.get("wer_mean", 0), base_summary.get("cer_mean", 0))
    if ft_summary:
        logger.info("  Fine-tuned: WER=%.3f  CER=%.3f",
                    ft_summary.get("wer_mean", 0), ft_summary.get("cer_mean", 0))
        logger.info("  WER reduction: %+.1f%%", improvement.get("wer_reduction_pct", 0))

    return evaluation


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Whisper LoRA adapters per language (WER)"
    )
    parser.add_argument(
        "--languages", nargs="+", default=["lg", "sw", "nyn"],
    )
    parser.add_argument("--max-samples", type=int, default=50)
    args = parser.parse_args()

    all_results = {}
    for lang in args.languages:
        if lang not in LANG_NAMES:
            logger.error("Unknown language: %s (supported: %s)", lang, list(LANG_NAMES.keys()))
            continue
        result = evaluate_language(lang, args.max_samples)
        if result:
            all_results[lang] = result

    if all_results:
        combined_path = RESULTS_DIR / "eval_whisper_all.json"
        summary = {}
        for lang, result in all_results.items():
            summary[lang] = {
                "base": result.get("base_summary", {}),
                "fine_tuned": result.get("fine_tuned_summary", {}),
                "improvement": result.get("improvement", {}),
            }
        with open(combined_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info("\nCombined summary saved to %s", combined_path)

    logger.info("\n" + "=" * 60)
    logger.info("WHISPER EVALUATION COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
