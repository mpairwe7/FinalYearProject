#!/usr/bin/env python3
"""Step 10: Evaluate Qwen3-8B LoRA adapters per language.

Compares base model vs fine-tuned adapter on held-out test prompts.
Metrics: ROUGE-L, BLEU, response quality scoring.

Usage:
    python fine-tuning/scripts/10_evaluate_lora_qwen.py --languages lg sw nyn ach
    python fine-tuning/scripts/10_evaluate_lora_qwen.py --languages sw --max-samples 20
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

FINETUNE_MODEL = os.getenv("FINETUNE_MODEL")
if FINETUNE_MODEL and os.path.isdir(FINETUNE_MODEL):
    snapshots_dir = Path(FINETUNE_MODEL) / "snapshots"
    if snapshots_dir.exists():
        snapshot_paths = list(snapshots_dir.glob("*/"))
        MODEL_NAME = str(snapshot_paths[0]) if snapshot_paths else "Qwen/Qwen3-8B"
    else:
        MODEL_NAME = "Qwen/Qwen3-8B"
else:
    MODEL_NAME = FINETUNE_MODEL or "Qwen/Qwen3-8B"

LANG_NAMES = {"lg": "Luganda", "sw": "Swahili", "nyn": "Runyankole", "ach": "Acholi"}

ADAPTER_DIRS = {
    "lg": BASE_DIR / "adapters" / "luganda-lora",
    "sw": BASE_DIR / "adapters" / "sw-lora",
    "nyn": BASE_DIR / "adapters" / "nyn-lora",
    "ach": BASE_DIR / "adapters" / "ach-lora",
}

# Evaluation test prompts per language — domain-specific
EVAL_PROMPTS = {
    "lg": [
        {"query": "Ndagala okumanya engeri y'okweyunyisiza TIN", "domain": "registration",
         "reference": "Okufuna TIN, genda ku portal ya URA ura.go.ug"},
        {"query": "VAT ebalibwa etya mu Uganda?", "domain": "tax",
         "reference": "VAT mu Uganda eri ku 18% ku bintu n'obuweereza"},
        {"query": "EFRIS y'ekiki?", "domain": "efris",
         "reference": "EFRIS ye Electronic Fiscal Receipting and Invoicing System"},
        {"query": "Nkoze ntya okusasula omusolo gwange?", "domain": "payment",
         "reference": "Osobola okusasula omusolo ku portal ya e-Tax"},
        {"query": "Biki ebyetaagibwa okutandika obusubuzi mu Uganda?", "domain": "business",
         "reference": "Okutandika obusubuzi, wetaaga TIN, lisansi ya biashara"},
    ],
    "sw": [
        {"query": "Ninawezaje kusajili kwa TIN nchini Uganda?", "domain": "registration",
         "reference": "Ili kupata TIN, tembelea portal ya URA ura.go.ug"},
        {"query": "Kiwango cha VAT ni kipi nchini Uganda?", "domain": "tax",
         "reference": "VAT nchini Uganda ni 18% kwa bidhaa na huduma"},
        {"query": "EFRIS ni nini?", "domain": "efris",
         "reference": "EFRIS ni Electronic Fiscal Receipting and Invoicing System"},
        {"query": "Ninawezaje kulipa kodi yangu?", "domain": "payment",
         "reference": "Unaweza kulipa kodi kwenye portal ya e-Tax"},
        {"query": "Nini kinahitajika kuanzisha biashara Uganda?", "domain": "business",
         "reference": "Kuanzisha biashara unahitaji TIN, leseni ya biashara"},
    ],
    "nyn": [
        {"query": "Ninkora ntaa okweyunyisiza TIN?", "domain": "registration",
         "reference": "Okushanga TIN, genda aha portal ya URA ura.go.ug"},
        {"query": "VAT ebaribwa eta omu Uganda?", "domain": "tax",
         "reference": "VAT omu Uganda eri aha 18%"},
        {"query": "EFRIS ni ki?", "domain": "efris",
         "reference": "EFRIS ni Electronic Fiscal Receipting and Invoicing System"},
        {"query": "Ninkora ntaa okushashura omusoro gwangye?", "domain": "payment",
         "reference": "Orikushobora okushashura omusoro aha portal ya e-Tax"},
        {"query": "Biki ebitagisibwa okutandika obusubuzi omu Uganda?", "domain": "business",
         "reference": "Okutandika obusubuzi, notagisa TIN, lisansi ya biashara"},
    ],
    "ach": [
        {"query": "Atimo nining me gamo TIN?", "domain": "registration",
         "reference": "Pi nongo TIN, cit i portal pa URA ura.go.ug"},
        {"query": "Cul pa VAT tye adi i Uganda?", "domain": "tax",
         "reference": "VAT i Uganda tye i 18% pi jami ki tic"},
        {"query": "EFRIS en ango?", "domain": "efris",
         "reference": "EFRIS en Electronic Fiscal Receipting and Invoicing System"},
        {"query": "Atwero culo ajog anga nining?", "domain": "payment",
         "reference": "Itwero culo ajog i portal pa e-Tax"},
        {"query": "Ango ma mite pi cako tic i Uganda?", "domain": "business",
         "reference": "Pi cako tic, imite TIN, licence pa tic"},
    ],
}


def compute_rouge_l(prediction: str, reference: str) -> float:
    """Compute ROUGE-L F1 score between prediction and reference."""
    if not prediction or not reference:
        return 0.0

    pred_tokens = prediction.lower().split()
    ref_tokens = reference.lower().split()

    if not pred_tokens or not ref_tokens:
        return 0.0

    # LCS computation
    m, n = len(ref_tokens), len(pred_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i - 1] == pred_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs_len = dp[m][n]

    precision = lcs_len / n if n > 0 else 0
    recall = lcs_len / m if m > 0 else 0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def compute_bleu_1(prediction: str, reference: str) -> float:
    """Compute unigram BLEU (BLEU-1) with brevity penalty."""
    if not prediction or not reference:
        return 0.0

    pred_tokens = prediction.lower().split()
    ref_tokens = reference.lower().split()
    if not pred_tokens:
        return 0.0

    ref_counts: dict[str, int] = {}
    for t in ref_tokens:
        ref_counts[t] = ref_counts.get(t, 0) + 1

    matches = 0
    for t in pred_tokens:
        if ref_counts.get(t, 0) > 0:
            matches += 1
            ref_counts[t] -= 1

    precision = matches / len(pred_tokens)

    # Brevity penalty
    bp = min(1.0, len(pred_tokens) / max(len(ref_tokens), 1))
    return bp * precision


def score_response_quality(response: str, query: str, lang: str) -> dict:
    """Score response quality on multiple dimensions."""
    scores = {}

    # Length check (too short = bad, too long = verbose)
    word_count = len(response.split())
    if word_count < 5:
        scores["length"] = 0.2
    elif word_count < 15:
        scores["length"] = 0.5
    elif word_count > 500:
        scores["length"] = 0.6
    else:
        scores["length"] = 1.0

    # Language consistency — does the response contain words from the query language?
    query_words = set(query.lower().split())
    response_words = set(response.lower().split())
    overlap = len(query_words & response_words)
    scores["language_consistency"] = min(1.0, overlap / max(len(query_words), 1))

    # Domain relevance — check for URA/tax keywords
    tax_keywords = {"ura", "tax", "tin", "vat", "efris", "omusolo", "kodi", "ajog",
                    "register", "portal", "e-tax", "payment", "filing"}
    tax_hits = len(tax_keywords & response_words)
    scores["domain_relevance"] = min(1.0, tax_hits / 2)

    # Non-empty check
    scores["non_empty"] = 1.0 if response.strip() else 0.0

    # Overall
    scores["overall"] = sum(scores.values()) / len(scores)
    return scores


def evaluate_model(model, tokenizer, prompts: list[dict], lang: str, label: str) -> list[dict]:
    """Run evaluation prompts through a model and score results."""
    import torch

    results = []
    for i, prompt_data in enumerate(prompts):
        query = prompt_data["query"]
        reference = prompt_data["reference"]

        messages = [{"role": "user", "content": query}]
        try:
            input_ids = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, return_tensors="pt",
                enable_thinking=False,
            ).to(model.device)

            t0 = time.perf_counter()
            with torch.no_grad():
                outputs = model.generate(
                    input_ids, max_new_tokens=200,
                    temperature=0.3, do_sample=True,
                    pad_token_id=tokenizer.eos_token_id,
                )
            latency = time.perf_counter() - t0

            response = tokenizer.decode(
                outputs[0][input_ids.shape[1]:], skip_special_tokens=True
            ).strip()
        except Exception as e:
            logger.warning("[%s] Generation failed for prompt %d: %s", lang, i, e)
            response = ""
            latency = 0.0

        rouge_l = compute_rouge_l(response, reference)
        bleu_1 = compute_bleu_1(response, reference)
        quality = score_response_quality(response, query, lang)

        result = {
            "query": query,
            "reference": reference,
            "response": response[:500],
            "domain": prompt_data["domain"],
            "rouge_l": round(rouge_l, 4),
            "bleu_1": round(bleu_1, 4),
            "quality": quality,
            "latency_s": round(latency, 2),
            "model_label": label,
        }
        results.append(result)
        logger.info(
            "  [%s/%s] %d/%d ROUGE-L=%.3f BLEU-1=%.3f quality=%.3f latency=%.1fs",
            lang, label, i + 1, len(prompts),
            rouge_l, bleu_1, quality["overall"], latency,
        )

    return results


def evaluate_language(lang: str, max_samples: int = 50):
    """Evaluate base vs fine-tuned model for one language."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    lang_name = LANG_NAMES.get(lang, lang)
    adapter_dir = ADAPTER_DIRS.get(lang)

    logger.info("=" * 60)
    logger.info("EVALUATING: %s (%s)", lang_name, lang)
    logger.info("=" * 60)

    prompts = EVAL_PROMPTS.get(lang, [])[:max_samples]
    if not prompts:
        logger.error("[%s] No evaluation prompts available", lang)
        return None

    # Load base model
    logger.info("[%s] Loading base model: %s", lang, MODEL_NAME)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    base_model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME, torch_dtype=torch.float16, device_map="auto",
    )
    base_model.eval()

    # Evaluate base model
    logger.info("[%s] Evaluating BASE model (%d prompts)...", lang, len(prompts))
    base_results = evaluate_model(base_model, tokenizer, prompts, lang, "base")

    # Evaluate fine-tuned model (if adapter exists)
    ft_results = []
    if adapter_dir and adapter_dir.exists():
        logger.info("[%s] Loading fine-tuned adapter from %s", lang, adapter_dir)
        try:
            from peft import PeftModel
            ft_model = PeftModel.from_pretrained(base_model, str(adapter_dir))
            ft_model = ft_model.merge_and_unload()
            ft_model.eval()

            logger.info("[%s] Evaluating FINE-TUNED model (%d prompts)...", lang, len(prompts))
            ft_results = evaluate_model(ft_model, tokenizer, prompts, lang, "fine-tuned")

            del ft_model
            torch.cuda.empty_cache()
        except Exception as e:
            logger.error("[%s] Failed to load adapter: %s", lang, e)
    else:
        logger.warning("[%s] No adapter found at %s — skipping fine-tuned eval", lang, adapter_dir)

    del base_model
    torch.cuda.empty_cache()

    # Compute summary metrics
    def summarize(results: list[dict]) -> dict:
        if not results:
            return {}
        return {
            "rouge_l_mean": round(sum(r["rouge_l"] for r in results) / len(results), 4),
            "bleu_1_mean": round(sum(r["bleu_1"] for r in results) / len(results), 4),
            "quality_mean": round(
                sum(r["quality"]["overall"] for r in results) / len(results), 4
            ),
            "latency_mean_s": round(
                sum(r["latency_s"] for r in results) / len(results), 2
            ),
            "n_samples": len(results),
        }

    base_summary = summarize(base_results)
    ft_summary = summarize(ft_results)

    # Compute improvement
    improvement = {}
    if base_summary and ft_summary:
        for key in ("rouge_l_mean", "bleu_1_mean", "quality_mean"):
            base_val = base_summary.get(key, 0)
            ft_val = ft_summary.get(key, 0)
            if base_val > 0:
                improvement[key] = round((ft_val - base_val) / base_val * 100, 1)
            else:
                improvement[key] = 0.0

    evaluation = {
        "language": lang,
        "language_name": lang_name,
        "base_model": MODEL_NAME,
        "adapter_dir": str(adapter_dir) if adapter_dir else None,
        "base_summary": base_summary,
        "fine_tuned_summary": ft_summary,
        "improvement_pct": improvement,
        "base_results": base_results,
        "fine_tuned_results": ft_results,
    }

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"eval_lora_qwen_{lang}.json"
    with open(out_path, "w") as f:
        json.dump(evaluation, f, indent=2, ensure_ascii=False)
    logger.info("[%s] Results saved to %s", lang, out_path)

    # Print summary
    logger.info("\n--- %s SUMMARY ---", lang_name.upper())
    logger.info("  Base:       ROUGE-L=%.3f  BLEU-1=%.3f  Quality=%.3f",
                base_summary.get("rouge_l_mean", 0),
                base_summary.get("bleu_1_mean", 0),
                base_summary.get("quality_mean", 0))
    if ft_summary:
        logger.info("  Fine-tuned: ROUGE-L=%.3f  BLEU-1=%.3f  Quality=%.3f",
                    ft_summary.get("rouge_l_mean", 0),
                    ft_summary.get("bleu_1_mean", 0),
                    ft_summary.get("quality_mean", 0))
        logger.info("  Improvement: ROUGE-L=%+.1f%%  BLEU-1=%+.1f%%  Quality=%+.1f%%",
                    improvement.get("rouge_l_mean", 0),
                    improvement.get("bleu_1_mean", 0),
                    improvement.get("quality_mean", 0))

    return evaluation


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Qwen3-8B LoRA adapters per language"
    )
    parser.add_argument(
        "--languages", nargs="+", default=["lg", "sw", "nyn", "ach"],
    )
    parser.add_argument("--max-samples", type=int, default=50)
    args = parser.parse_args()

    all_results = {}
    for lang in args.languages:
        if lang not in LANG_NAMES:
            logger.error("Unknown language: %s", lang)
            continue
        result = evaluate_language(lang, args.max_samples)
        if result:
            all_results[lang] = result

    # Save combined results
    if all_results:
        combined_path = RESULTS_DIR / "eval_lora_qwen_all.json"
        summary = {}
        for lang, result in all_results.items():
            summary[lang] = {
                "base": result.get("base_summary", {}),
                "fine_tuned": result.get("fine_tuned_summary", {}),
                "improvement_pct": result.get("improvement_pct", {}),
            }
        with open(combined_path, "w") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info("\nCombined summary saved to %s", combined_path)

    logger.info("\n" + "=" * 60)
    logger.info("EVALUATION COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
