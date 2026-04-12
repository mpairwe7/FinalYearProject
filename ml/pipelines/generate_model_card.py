"""HF-Hub + EU-AI-Act-compliant model card generator and validator.

``export_mobile.py`` already produces a small mobile-runtime model card
(deployment settings, SHA, quantisation lineage). This module produces
the *larger* card that a governance review expects:

- Training data composition + per-source SHA
- Fine-tuning hyperparameters + LoRA config
- Aggregate **and** per-language / per-category evaluation
- Safety evaluation (refusal rate by redteam category, if present)
- Calibration report (ECE, reliability, recommended abstention threshold)
- Tokenizer audit (Luganda fertility)
- Mobile benchmark (tokens/sec, TTFT, peak RSS, model size)
- Intended use, out-of-scope use, limitations, known failure modes
- Reproducibility block (env snapshot, git SHA, how to re-run)

The card follows the HuggingFace Hub YAML-frontmatter convention so it
is directly uploadable to a Hub repo. It also satisfies EU AI Act
Art. 10 (data governance records) and Art. 13 (transparency / user
information) for limited-risk models.

Two entry points are exposed:

1. ``generate()``  — build the card from whatever artefact paths are
   provided. Missing artefacts degrade gracefully (the corresponding
   section shows "not available" rather than failing the whole build).
2. ``validate()`` — parse an existing MODEL_CARD.md and assert that
   every *required* section is present and non-empty. Used in CI as
   a governance gate so we never ship a card that is silently missing
   a required field after a template change.

CLI usage:

    python -m ml.pipelines.generate_model_card \\
        --mobile-manifest artifacts/mobile/mobile_manifest.json \\
        --rag-eval Results/rag_evaluation_results.json \\
        --calibration Results/calibration/calibration_report.json \\
        --tokenizer-audit Results/tokenizer_audit/tokenizer_audit.json \\
        --benchmark artifacts/benchmark/benchmark.json \\
        --safety Results/safety_evaluation_results.json \\
        --output artifacts/mobile/MODEL_CARD.md

    # Validate an existing card (exit non-zero if broken)
    python -m ml.pipelines.generate_model_card \\
        --validate artifacts/mobile/MODEL_CARD.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ml.scripts.repro import env_snapshot  # noqa: E402

log = logging.getLogger("generate_model_card")


# ---------------------------------------------------------------------------
# Required sections — the validator treats these as load-bearing.
# ---------------------------------------------------------------------------
REQUIRED_SECTIONS: tuple[str, ...] = (
    "Model Details",
    "Intended Use",
    "Out-of-Scope Use",
    "Training Data",
    "Training Procedure",
    "Evaluation",
    "Safety Evaluation",
    "Calibration",
    "Tokenizer Audit",
    "Mobile Benchmark",
    "Limitations",
    "Reproducibility",
    "License",
)


# ---------------------------------------------------------------------------
# Loaders (JSON artefacts are optional — missing files degrade gracefully)
# ---------------------------------------------------------------------------
def _load_json(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        log.warning("Artefact not found: %s", p)
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in %s: %s", p, exc)
        return None


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------
def _na(value: Any, fmt: str = "{}") -> str:
    if value is None or value == "":
        return "_not available_"
    try:
        return fmt.format(value)
    except (TypeError, ValueError):
        return str(value)


def _yaml_frontmatter(mobile_manifest: dict[str, Any] | None) -> str:
    base_model = (mobile_manifest or {}).get("base_model") or "google/gemma-2-2b-it"
    quant = (mobile_manifest or {}).get("quantization") or "Q4_K_M"
    lines = [
        "---",
        "language:",
        "- en",
        "- lg",
        "license: apache-2.0",
        f"base_model: {base_model}",
        "library_name: gguf",
        "pipeline_tag: text-generation",
        "tags:",
        "- ura",
        "- taxation",
        "- uganda",
        "- mobile",
        "- gguf",
        f"- {quant.lower()}",
        "- luganda",
        "---",
        "",
    ]
    return "\n".join(lines)


def _section_model_details(mobile: dict[str, Any] | None) -> str:
    m = mobile or {}
    lineage = m.get("lineage") or {}
    git = m.get("git") or {}
    return (
        "## Model Details\n\n"
        f"- **Name:** `ura-gemma-2b`\n"
        f"- **Base model:** `{_na(m.get('base_model'))}`\n"
        f"- **Architecture:** Gemma-2 decoder-only transformer, 2.6B params\n"
        f"- **Quantisation:** `{_na(m.get('quantization'))}`\n"
        f"- **File:** `{_na(m.get('file_name'))}`\n"
        f"- **Size on disk:** {_na(m.get('size_mb'), '{:.1f} MB')}\n"
        f"- **SHA-256:** `{_na(m.get('sha256'))}`\n"
        f"- **Pipeline version:** `{_na(m.get('pipeline_version'))}`\n"
        f"- **Export git commit:** `{_na(git.get('commit'))}` "
        f"(dirty: `{_na(git.get('dirty'))}`)\n"
        f"- **Training lineage commit:** `{_na(lineage.get('git_commit'))}`\n"
    )


def _section_intended_use() -> str:
    return (
        "## Intended Use\n\n"
        "This model is the **offline mobile assistant** for the Uganda Revenue "
        "Authority (URA) Tax Assistant application. It is designed to:\n\n"
        "- Answer general questions about Ugandan tax law, VAT, PAYE, "
        "Withholding Tax, Customs duty, and related topics.\n"
        "- Respond in **English** and **Luganda**.\n"
        "- Run **fully on-device** via MediaPipe LLM Inference on Android "
        "(SDK ≥ 24) and iOS (≥ 16.0).\n"
        "- Provide **grounded** answers when the RAG retrieval layer "
        "supplies context; abstain or escalate when no context is "
        "available.\n"
    )


def _section_out_of_scope() -> str:
    return (
        "## Out-of-Scope Use\n\n"
        "This model must **not** be used for:\n\n"
        "- Personalised tax advice (binding computations, filings, or "
        "specific-taxpayer rulings).\n"
        "- Any form of tax-evasion, fraud, or money-laundering assistance.\n"
        "- Legal advice beyond publicly-available URA guidance.\n"
        "- Jurisdictions other than Uganda.\n"
        "- Real-time pricing / rate lookups where rates are not present in "
        "the retrieved context.\n\n"
        "Adversarial attempts to elicit such content are evaluated by the "
        "red-team suite (see *Safety Evaluation*).\n"
    )


def _section_training_data(mobile: dict[str, Any] | None) -> str:
    lineage = (mobile or {}).get("lineage") or {}
    dataset = lineage.get("dataset_metadata") or {}
    return (
        "## Training Data\n\n"
        "The training corpus is produced by the 2026 four-stage data "
        "augmentation pipeline (`ml/scripts/data_augmentation.py`), "
        "recorded with a per-row Pydantic schema, SHA-256 manifest, and "
        "an auto-generated *Data Card* (Croissant-compatible) that "
        "satisfies EU AI Act Art. 10.\n\n"
        "Sources:\n\n"
        "- **URA FAQ corpus** — curated Q&A pairs from the URA website and "
        "print materials.\n"
        "- **URA PDF corpus** — hierarchically chunked legal / policy PDFs "
        "with table-preserving extraction.\n"
        "- **Luganda parallel corpus** — English↔Luganda translation "
        "pairs tagged as `translation`.\n"
        "- **Teacher-generated Q&A** — synthetic Q&A produced by a large "
        "teacher model, grounded to the PDF corpus.\n"
        "- **Curated refusals** — hand-written safety examples covering "
        "out-of-scope and personalised-advice declines.\n\n"
        f"- **Training split:** `{_na(dataset.get('train_path'))}`\n"
        f"- **Train SHA-256:** `{_na(dataset.get('train_sha256'))}`\n"
        f"- **Manifest SHA-256:** `{_na(dataset.get('manifest_sha256'))}`\n"
        f"- **Data pipeline version:** `{_na(dataset.get('pipeline_version'))}`\n"
        "- **PII redaction:** phone, email, TIN, NIN, passport, and "
        "account-number patterns scrubbed before hashing.\n"
        "- **Deduplication:** exact hash + MinHash-LSH near-dedup at "
        "threshold 0.85 on the full `(user, assistant)` pair.\n"
        "- **Contamination scan:** rows that leak from train into "
        "val/test are dropped by the splitter.\n"
    )


def _section_training_procedure(mobile: dict[str, Any] | None) -> str:
    lineage = (mobile or {}).get("lineage") or {}
    lora = lineage.get("lora_config") or {}
    train = lineage.get("training_config") or {}
    return (
        "## Training Procedure\n\n"
        f"- **Recipe:** QLoRA 4-bit NF4 supervised fine-tune via "
        "[TRL](https://huggingface.co/docs/trl) `SFTTrainer`.\n"
        f"- **LoRA:** r={_na(lora.get('r'))}, α={_na(lora.get('lora_alpha'))}, "
        f"dropout={_na(lora.get('lora_dropout'))}, "
        f"RSLoRA={_na(lora.get('use_rslora'))}, "
        f"DoRA={_na(lora.get('use_dora'))}\n"
        f"- **Effective batch:** {_na(train.get('effective_batch_size'))}\n"
        f"- **Learning rate:** {_na(train.get('learning_rate'))}\n"
        f"- **Epochs:** {_na(train.get('num_epochs'))}\n"
        f"- **Seed:** {_na(train.get('seed'))}\n"
        "- **Chat template:** applied at training time via "
        "`tokenizer.apply_chat_template`; training examples are stored in "
        "OpenAI `messages` format (no hand-crafted templates in the JSONL).\n"
        "- **Quantisation-aware export:** optional imatrix calibration for "
        "sub-4-bit quants (`IQ3_M`, `IQ3_S`, `IQ2_M`) using a sample of the "
        "training corpus.\n"
    )


def _section_evaluation(rag_eval: dict[str, Any] | None) -> str:
    if not rag_eval:
        return "## Evaluation\n\n_not available_\n"

    def _mean(name: str) -> str:
        return _na((rag_eval.get(name) or {}).get("mean"), "{:.4f}")

    lines = [
        "## Evaluation\n",
        "Aggregate metrics on the RAG evaluation set "
        "(`Data/eval/rag_eval.jsonl`):\n",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Faithfulness | {_mean('faithfulness')} |",
        f"| Answer relevancy | {_mean('answer_relevancy')} |",
        f"| Context precision | {_mean('context_precision')} |",
        f"| Context recall | {_mean('context_recall')} |",
        f"| Groundedness | {_mean('groundedness')} |",
        f"| Citation accuracy | {_mean('citation_accuracy')} |",
        f"| Numerical accuracy | {_mean('numerical_accuracy')} |",
        f"| Law-citation accuracy | {_mean('law_citation_accuracy')} |",
        f"| Abstention precision | {_mean('abstention_precision')} |",
        "",
    ]

    slices = rag_eval.get("slices") or {}
    by_lang = slices.get("by_language") or {}
    if by_lang:
        lines.append("### By language\n")
        lines.append("| Language | Faithfulness | Numerical | Law cite | n |")
        lines.append("|----------|--------------|-----------|----------|---|")
        for lang, stats in by_lang.items():
            f = (stats.get("faithfulness") or {}).get("mean")
            n = (stats.get("numerical_accuracy") or {}).get("mean")
            lw = (stats.get("law_citation_accuracy") or {}).get("mean")
            size = (stats.get("faithfulness") or {}).get("n", 0)
            lines.append(
                f"| {lang} | {_na(f, '{:.4f}')} | {_na(n, '{:.4f}')} | "
                f"{_na(lw, '{:.4f}')} | {size} |"
            )
        lines.append("")

    by_cat = slices.get("by_category") or {}
    if by_cat:
        lines.append("### By category\n")
        lines.append("| Category | Faithfulness | Numerical | Law cite | n |")
        lines.append("|----------|--------------|-----------|----------|---|")
        for cat, stats in by_cat.items():
            f = (stats.get("faithfulness") or {}).get("mean")
            n = (stats.get("numerical_accuracy") or {}).get("mean")
            lw = (stats.get("law_citation_accuracy") or {}).get("mean")
            size = (stats.get("faithfulness") or {}).get("n", 0)
            lines.append(
                f"| {cat} | {_na(f, '{:.4f}')} | {_na(n, '{:.4f}')} | "
                f"{_na(lw, '{:.4f}')} | {size} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def _section_safety(safety: dict[str, Any] | None, rag_eval: dict[str, Any] | None) -> str:
    if not safety:
        fallback = ((rag_eval or {}).get("safety_probe_pass_rate") or {}).get("mean")
        if fallback is None:
            return "## Safety Evaluation\n\n_not available_\n"
        return (
            "## Safety Evaluation\n\n"
            f"- **Guardrail safety-probe pass rate:** "
            f"{_na(fallback, '{:.4f}')}\n"
            "- **Source:** built-in guardrail probe set in `evaluate_rag.py`.\n"
            "- **Note:** no standalone red-team corpus report available; "
            "see `Data/eval/redteam_corpus.jsonl` for the authoritative "
            "prompt set.\n"
        )
    summary = safety.get("summary") or safety
    lines = ["## Safety Evaluation\n"]
    if "refusal_rate" in summary:
        lines.append(f"- **Overall refusal rate:** {_na(summary.get('refusal_rate'), '{:.4f}')}")
    if "n_prompts" in summary:
        lines.append(f"- **Prompts evaluated:** {summary['n_prompts']}")
    by_cat = safety.get("by_category") or {}
    if by_cat:
        lines.append("")
        lines.append("| Category | n | Refusal rate |")
        lines.append("|----------|---|--------------|")
        for cat, stats in by_cat.items():
            lines.append(
                f"| {cat} | {stats.get('n', 0)} | "
                f"{_na(stats.get('refusal_rate'), '{:.4f}')} |"
            )
    lines.append("")
    return "\n".join(lines) + "\n"


def _section_calibration(calibration: dict[str, Any] | None) -> str:
    if not calibration:
        return "## Calibration\n\n_not available_\n"
    s = calibration.get("summary") or {}
    rec = calibration.get("recommended_threshold") or {}
    lines = [
        "## Calibration\n",
        f"- **Samples:** {_na(s.get('n_samples'))}",
        f"- **Accuracy:** {_na(s.get('accuracy'), '{:.4f}')}",
        f"- **Mean confidence:** {_na(s.get('mean_confidence'), '{:.4f}')}",
        f"- **Expected Calibration Error (ECE):** "
        f"{_na(s.get('ece'), '{:.4f}')}",
        f"- **Maximum Calibration Error (MCE):** "
        f"{_na(s.get('mce'), '{:.4f}')}",
        f"- **Brier score:** {_na(s.get('brier'), '{:.4f}')}",
    ]
    if s.get("temperature") is not None:
        lines.append(
            f"- **Temperature (post-scaling):** "
            f"{_na(s.get('temperature'), '{:.4f}')} "
            f"→ ECE {_na(s.get('ece_post_scaling'), '{:.4f}')}"
        )
    if rec.get("threshold") is not None:
        lines.append(
            f"- **Recommended abstention threshold:** "
            f"{rec['threshold']:.2f} → coverage "
            f"{rec.get('coverage_at_threshold', 0):.2f}, "
            f"accuracy {rec.get('accuracy_at_threshold', 0):.3f}"
        )
    else:
        lines.append("- **Recommended abstention threshold:** _none within target_")
    return "\n".join(lines) + "\n"


def _section_tokenizer(tok: dict[str, Any] | None) -> str:
    if not tok:
        return "## Tokenizer Audit\n\n_not available_\n"
    corpora = tok.get("corpora") or {}
    cmp_ = tok.get("comparison") or {}
    lines = [
        "## Tokenizer Audit\n",
        f"- **Tokenizer:** `{_na(tok.get('tokenizer'))}`"
        + ("  _(proxy)_" if tok.get("proxy") else ""),
        f"- **Luganda ÷ English fertility ratio:** "
        f"{_na(cmp_.get('fertility_ratio_lg_over_en'), '{:.4f}')} "
        f"(threshold ≤ {_na(cmp_.get('fertility_warn_threshold'), '{:.2f}')})",
        f"- **Verdict:** {cmp_.get('verdict', 'n/a')}",
        "",
        "| Lang | n_texts | tokens/char | fertility | unk_rate |",
        "|------|---------|-------------|-----------|----------|",
    ]
    for lang, stats in corpora.items():
        lines.append(
            f"| {lang} | {stats.get('n_texts', 0)} | "
            f"{_na(stats.get('tokens_per_char'), '{:.4f}')} | "
            f"{_na(stats.get('fertility'), '{:.4f}')} | "
            f"{_na(stats.get('unk_rate'), '{:.4f}')} |"
        )
    return "\n".join(lines) + "\n"


def _section_benchmark(bench: dict[str, Any] | None) -> str:
    if not bench:
        return "## Mobile Benchmark\n\n_not available_\n"
    run = bench.get("run") or {}
    agg = (bench.get("aggregate") or {}).get("overall") or {}
    by_cls = (bench.get("aggregate") or {}).get("by_class") or {}
    tps = agg.get("tokens_per_sec") or {}
    ttft = agg.get("ttft_ms") or {}
    note = (
        "> ⚠ Synthetic benchmark — not a real device measurement.\n\n"
        if bench.get("synthetic")
        else ""
    )
    lines = [
        "## Mobile Benchmark\n",
        note,
        f"- **Mode:** {_na(run.get('mode'))}",
        f"- **Model size:** {_na(run.get('model_size_mb'), '{:.1f} MB')}",
        f"- **Peak RSS:** {_na(run.get('peak_rss_mb'), '{:.1f} MB')}",
        f"- **Tokens/sec (mean / p50 / p95):** "
        f"{_na(tps.get('mean'), '{:.2f}')} / "
        f"{_na(tps.get('p50'), '{:.2f}')} / "
        f"{_na(tps.get('p95'), '{:.2f}')}",
        f"- **TTFT ms (mean / p50 / p95):** "
        f"{_na(ttft.get('mean'), '{:.1f}')} / "
        f"{_na(ttft.get('p50'), '{:.1f}')} / "
        f"{_na(ttft.get('p95'), '{:.1f}')}",
        "",
        "### Per prompt class",
        "| Class | tokens/sec mean | TTFT p95 (ms) |",
        "|-------|-----------------|----------------|",
    ]
    for cls, stats in by_cls.items():
        cls_tps = (stats.get("tokens_per_sec") or {}).get("mean")
        cls_ttft = (stats.get("ttft_ms") or {}).get("p95")
        lines.append(
            f"| {cls} | {_na(cls_tps, '{:.2f}')} | "
            f"{_na(cls_ttft, '{:.1f}')} |"
        )
    return "\n".join(lines) + "\n"


def _section_limitations(tok: dict[str, Any] | None) -> str:
    lg_warn = ""
    if tok:
        cmp_ = tok.get("comparison") or {}
        if cmp_.get("passed") is False:
            lg_warn = (
                "- **Luganda tokenizer coverage is thin** "
                f"(fertility ratio "
                f"{cmp_.get('fertility_ratio_lg_over_en', 'n/a')}"
                f" vs threshold {cmp_.get('fertility_warn_threshold', 'n/a')}). "
                "Luganda responses will be slower per character and "
                "may degrade quality after INT4 quantisation.\n"
            )
    return (
        "## Limitations\n\n"
        "- This is a **2.6B-parameter** model running at INT4 on a mobile "
        "device. It will hallucinate on questions outside its training "
        "distribution — the backend RAG layer and the guardrails are "
        "the primary safety barrier, not the model itself.\n"
        "- Coverage of **case-law and Finance-Act amendments** is limited "
        "to documents present in the training corpus at build time. The "
        "model has no internet access on-device.\n"
        "- **Numerical reasoning** (multi-step tax computations) is "
        "intentionally delegated to the deterministic calculator tool; "
        "direct LLM arithmetic is not relied on in production.\n"
        f"{lg_warn}"
        "- The red-team suite covers prompt-injection, role-play, "
        "hypothetical-reframing, and encoding attacks but does not cover "
        "every adversarial pattern. Continuous updates are tracked in "
        "`Data/eval/redteam_corpus.jsonl`.\n"
    )


def _section_reproducibility(env: dict[str, Any], mobile: dict[str, Any] | None) -> str:
    git = env.get("git") or {}
    pkgs = env.get("packages") or {}
    plat = env.get("platform") or {}
    lineage = (mobile or {}).get("lineage") or {}
    return (
        "## Reproducibility\n\n"
        "```bash\n"
        f"git checkout {git.get('sha') or 'HEAD'}\n"
        "python ml/scripts/run_training_pipeline.sh \\\n"
        "    --target mobile_gemma_2b \\\n"
        "    --epochs 3\n"
        "python ml/scripts/export_mobile.py \\\n"
        f"    --quant {(mobile or {}).get('quantization') or 'Q4_K_M'}\n"
        "```\n\n"
        f"- **Card generated:** `{env.get('timestamp_utc')}`\n"
        f"- **Git SHA:** `{git.get('sha')}` "
        f"(branch `{git.get('branch')}`, dirty: `{git.get('dirty')}`)\n"
        f"- **Training lineage commit:** `{lineage.get('git_commit', 'unknown')}`\n"
        f"- **Python:** {plat.get('python')} on {plat.get('system')} "
        f"{plat.get('release')} / {plat.get('machine')}\n"
        f"- **transformers:** {pkgs.get('transformers')}, "
        f"**peft:** {pkgs.get('peft')}, **trl:** {pkgs.get('trl')}, "
        f"**torch:** {pkgs.get('torch')}, **gguf:** {pkgs.get('gguf')}\n"
    )


def _section_license() -> str:
    return (
        "## License\n\n"
        "Released under the **Apache-2.0** license, consistent with the "
        "upstream Gemma-2-2B base model's Gemma Terms of Use. "
        "Fine-tuned weights are derivative; downstream users must comply "
        "with both licenses.\n"
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate(
    *,
    mobile_manifest: dict[str, Any] | None = None,
    rag_eval: dict[str, Any] | None = None,
    safety_eval: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    tokenizer_audit: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> str:
    """Render the full model card as a single markdown string."""
    env = env_snapshot(script="generate_model_card")
    now = _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
    parts: list[str] = [
        _yaml_frontmatter(mobile_manifest),
        "# URA Tax Assistant — Production Model Card\n",
        f"*Auto-generated by `ml/pipelines/generate_model_card.py` at {now}.*\n",
        _section_model_details(mobile_manifest),
        _section_intended_use(),
        _section_out_of_scope(),
        _section_training_data(mobile_manifest),
        _section_training_procedure(mobile_manifest),
        _section_evaluation(rag_eval),
        _section_safety(safety_eval, rag_eval),
        _section_calibration(calibration),
        _section_tokenizer(tokenizer_audit),
        _section_benchmark(benchmark),
        _section_limitations(tokenizer_audit),
        _section_reproducibility(env, mobile_manifest),
        _section_license(),
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def validate(card: str) -> dict[str, Any]:
    """Return a structured report of missing / empty required sections.

    ``card`` is the full markdown text. We consider a section *present*
    iff the ``##`` heading exists AND the body between it and the next
    ``##`` has non-empty, non-placeholder content.
    """
    present: dict[str, bool] = {}
    headings = [m for m in _SECTION_RE.finditer(card)]
    for i, match in enumerate(headings):
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(card)
        body = card[start:end].strip()
        is_empty = not body or body in {"_not available_", "not available", "TBD"}
        name = match.group(1).strip()
        present[name] = not is_empty

    missing: list[str] = []
    empty: list[str] = []
    for req in REQUIRED_SECTIONS:
        if req not in present:
            missing.append(req)
        elif not present[req]:
            empty.append(req)

    return {
        "passed": not missing and not empty,
        "required": list(REQUIRED_SECTIONS),
        "missing": missing,
        "empty": empty,
        "found_sections": list(present.keys()),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Generate or validate the production model card"
    )
    parser.add_argument("--mobile-manifest", type=Path, default=None)
    parser.add_argument("--rag-eval", type=Path, default=None)
    parser.add_argument("--safety", type=Path, default=None)
    parser.add_argument("--calibration", type=Path, default=None)
    parser.add_argument("--tokenizer-audit", type=Path, default=None)
    parser.add_argument("--benchmark", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/mobile/MODEL_CARD.md"),
    )
    parser.add_argument(
        "--validate",
        type=Path,
        default=None,
        help="Validate an existing MODEL_CARD.md instead of generating",
    )
    args = parser.parse_args()

    if args.validate is not None:
        if not args.validate.exists():
            log.error("Card not found: %s", args.validate)
            sys.exit(1)
        result = validate(args.validate.read_text())
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["passed"] else 2)

    card = generate(
        mobile_manifest=_load_json(args.mobile_manifest),
        rag_eval=_load_json(args.rag_eval),
        safety_eval=_load_json(args.safety),
        calibration=_load_json(args.calibration),
        tokenizer_audit=_load_json(args.tokenizer_audit),
        benchmark=_load_json(args.benchmark),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(card)

    # Sanity check: validate what we just wrote so the pipeline fails
    # immediately if the template is broken.
    check = validate(card)
    print("=" * 60)
    print("MODEL CARD GENERATION")
    print("=" * 60)
    print(f"  written:  {args.output}")
    print(f"  sections: {len(check['found_sections'])}")
    if not check["passed"]:
        print("  ❌ self-validation FAILED:")
        if check["missing"]:
            print(f"     missing: {check['missing']}")
        if check["empty"]:
            print(f"     empty:   {check['empty']}")
        sys.exit(3)
    print("  ✅ self-validation passed")


if __name__ == "__main__":
    main()
