"""Automated FAQ translation pipeline (2026).

Reads the 47 gold-standard CSV FAQs from ``Data/dataset/`` and translates
each Q/A pair into target languages (sw, nyn, ach) using the MADLAD-400
MT backbone. Outputs translated CSVs in the same schema so
``loaders.load_csv_faqs`` consumes them without modification.

Usage::

    python -m ml.scripts.data_aug.faq_translator \\
        --csv-dir Data/dataset \\
        --output-dir Data/dataset/translated \\
        --target-langs sw nyn ach \\
        --model google/madlad400-3b-mt

The output structure mirrors the input::

    Data/dataset/translated/
    ├── sw/
    │   ├── ura_vat_faqs.csv
    │   ├── ura_customs_valuation_faqs.csv
    │   └── ...
    ├── nyn/
    │   └── ...
    └── ach/
        └── ...

Quality notes:
- MADLAD-400 is Apache-2.0 licensed and supports 400+ languages.
- Runyankole and Acholi are low-resource; expect lower quality.
- A human review pass is recommended for the first batch.
- Quality gates in training_config.yaml will filter poor translations.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# MADLAD-400 uses BCP-47-style language tags with >> prefix.
MADLAD_LANG_TAGS = {
    "en": "en",
    "lg": "lug",  # Luganda
    "sw": "swh",  # Swahili
    "nyn": "nyn",  # Runyankole
    "ach": "ach",  # Acholi
}


def _load_mt_model(model_name: str):
    """Load MADLAD-400 translation model. Returns (model, tokenizer)."""
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    log.info("Loading MT model: %s", model_name)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    return model, tokenizer


def _translate_batch(
    texts: list[str],
    target_lang: str,
    model,
    tokenizer,
    max_length: int = 512,
) -> list[str]:
    """Translate a batch of texts using MADLAD-400."""
    import torch

    tag = MADLAD_LANG_TAGS.get(target_lang, target_lang)
    # MADLAD-400 prompt format: <2xx> source text
    prefixed = [f"<2{tag}> {t}" for t in texts]

    inputs = tokenizer(
        prefixed,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_length,
    )

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_length,
            num_beams=4,
            early_stopping=True,
        )

    return tokenizer.batch_decode(outputs, skip_special_tokens=True)


def translate_faqs(
    csv_dir: Path,
    target_langs: list[str],
    output_dir: Path,
    model_name: str = "google/madlad400-3b-mt",
    batch_size: int = 8,
    dry_run: bool = False,
) -> dict[str, int]:
    """Translate all FAQ CSVs into target languages.

    Returns ``{lang: count}`` of translated pairs.
    """
    csv_files = sorted(csv_dir.glob("ura_*_faqs.csv"))
    if not csv_files:
        log.warning("No FAQ CSVs found in %s", csv_dir)
        return {}

    log.info("Found %d FAQ CSVs to translate into %s", len(csv_files), target_langs)

    if dry_run:
        return {lang: 0 for lang in target_langs}

    model, tokenizer = _load_mt_model(model_name)
    results: dict[str, int] = {lang: 0 for lang in target_langs}

    for lang in target_langs:
        lang_dir = output_dir / lang
        lang_dir.mkdir(parents=True, exist_ok=True)

        for csv_file in csv_files:
            out_file = lang_dir / csv_file.name

            if out_file.exists():
                log.debug("Skipping %s/%s (already translated)", lang, csv_file.name)
                continue

            # Read source CSV.
            rows = _read_faq_csv(csv_file)
            if not rows:
                continue

            questions = [r["question"] for r in rows]
            answers = [r["answer"] for r in rows]

            # Translate in batches.
            translated_q = []
            translated_a = []
            for i in range(0, len(questions), batch_size):
                batch_q = questions[i : i + batch_size]
                batch_a = answers[i : i + batch_size]
                translated_q.extend(_translate_batch(batch_q, lang, model, tokenizer))
                translated_a.extend(_translate_batch(batch_a, lang, model, tokenizer))

            # Write translated CSV.
            with out_file.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["question", "answer"])
                writer.writeheader()
                for q, a in zip(translated_q, translated_a, strict=False):
                    writer.writerow({"question": q.strip(), "answer": a.strip()})

            count = len(translated_q)
            results[lang] += count
            log.info("Translated %s → %s: %d pairs", csv_file.name, lang, count)

    return results


def _read_faq_csv(path: Path) -> list[dict]:
    """Read a FAQ CSV, tolerating various column names."""
    rows = []
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with path.open(encoding=enc) as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    continue
                q_col = _find_col(reader.fieldnames, ("question", "q"))
                a_col = _find_col(reader.fieldnames, ("answer", "a", "response"))
                if not q_col or not a_col:
                    continue
                for row in reader:
                    q = (row.get(q_col) or "").strip()
                    a = (row.get(a_col) or "").strip()
                    if q and a:
                        rows.append({"question": q, "answer": a})
                return rows
        except (UnicodeDecodeError, csv.Error):
            continue
    return rows


def _find_col(fields: list[str], candidates: tuple[str, ...]) -> str | None:
    for f in fields:
        if f.lower().strip() in candidates:
            return f
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Translate URA FAQ CSVs")
    parser.add_argument("--csv-dir", type=Path, default=Path("Data/dataset"))
    parser.add_argument("--output-dir", type=Path, default=Path("Data/dataset/translated"))
    parser.add_argument("--target-langs", nargs="+", default=["sw", "nyn", "ach"])
    parser.add_argument("--model", default="google/madlad400-3b-mt")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    results = translate_faqs(
        csv_dir=args.csv_dir,
        target_langs=args.target_langs,
        output_dir=args.output_dir,
        model_name=args.model,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )

    for lang, count in results.items():
        log.info("Total %s: %d translated pairs", lang, count)


if __name__ == "__main__":
    main()
