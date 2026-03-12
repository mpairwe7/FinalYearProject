#!/usr/bin/env python3
"""Data augmentation and preparation pipeline for URA Chatbot fine-tuning.

Combines multiple data sources (CSV FAQs, PDFs, Luganda translations,
teacher QA pairs) into unified training formats for Gemma/Llama fine-tuning.

Output formats:
  - training_data.jsonl      — instruction format (Alpaca-compatible)
  - gemma_training.jsonl      — Gemma chat-template format
  - instruction_format.jsonl  — instruction + context format

Usage:
    python ml/scripts/data_augmentation.py \\
        --csv-dir Data/dataset \\
        --pdf-dir Data/pdfs \\
        --output artifacts/training_data.jsonl

    # Include Luganda data
    python ml/scripts/data_augmentation.py \\
        --csv-dir Data/dataset \\
        --pdf-dir Data/pdfs \\
        --luganda-dir Data/TTT \\
        --output artifacts/training_data.jsonl

Requirements:
    pip install pandas pymupdf4llm
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

# PDF extraction (optional)
try:
    import pymupdf4llm

    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Normalise whitespace and strip."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """Split text into overlapping word-level chunks."""
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk) > 50:
            chunks.append(chunk)
        start = end - overlap if end < len(words) else len(words)
    return chunks


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_csv_faqs(csv_dir: Path) -> list[dict[str, Any]]:
    """Load all URA FAQ CSVs as instruction examples."""
    import pandas as pd

    csv_files = sorted(csv_dir.glob("ura_*_faqs.csv"))
    if not csv_files:
        csv_files = sorted(csv_dir.glob("*.csv"))

    examples: list[dict[str, Any]] = []
    for csv_path in csv_files:
        try:
            df = pd.read_csv(csv_path, encoding="utf-8")
            # Normalise column names
            df.columns = [c.strip().lower() for c in df.columns]

            q_col = next((c for c in df.columns if "question" in c), None)
            a_col = next((c for c in df.columns if "answer" in c), None)

            if q_col is None or a_col is None:
                print(f"  Skipping {csv_path.name}: no question/answer columns")
                continue

            tag = csv_path.stem.replace("ura_", "").replace("_faqs", "")

            for _, row in df.iterrows():
                question = clean_text(str(row[q_col]))
                answer = clean_text(str(row[a_col]))
                if len(question) > 10 and len(answer) > 10:
                    examples.append(
                        {
                            "instruction": question,
                            "input": "",
                            "output": answer,
                            "source": csv_path.name,
                            "tag": tag,
                        }
                    )

            print(f"  {csv_path.name}: {sum(1 for e in examples if e['source'] == csv_path.name)} examples")
        except Exception as e:
            print(f"  Error loading {csv_path.name}: {e}")

    return examples


def load_pdf_chunks(
    pdf_dir: Path, chunk_size: int = 400, max_pdfs: int | None = None
) -> list[dict[str, Any]]:
    """Extract and chunk PDFs into instruction-style training examples."""
    if not PYMUPDF_AVAILABLE:
        print("  pymupdf4llm not installed, skipping PDF extraction")
        return []

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if max_pdfs:
        pdf_files = pdf_files[:max_pdfs]

    examples: list[dict[str, Any]] = []
    for pdf_path in pdf_files:
        try:
            md_text = pymupdf4llm.to_markdown(str(pdf_path), pages=None, show_progress=False)
            text = clean_text(str(md_text))
            if len(text) < 100:
                continue

            chunks = chunk_text(text, chunk_size)
            for i, chunk in enumerate(chunks):
                examples.append(
                    {
                        "instruction": "Summarize the following text about Uganda tax regulations:",
                        "input": chunk,
                        "output": chunk[:300],  # Self-supervised: chunk as its own summary seed
                        "source": pdf_path.name,
                        "chunk_id": i,
                    }
                )

            print(f"  {pdf_path.name}: {len(chunks)} chunks")
        except Exception as e:
            print(f"  Error processing {pdf_path.name}: {e}")

    return examples


def load_luganda_data(luganda_dir: Path) -> list[dict[str, Any]]:
    """Load Luganda translation pairs for multilingual support."""
    examples: list[dict[str, Any]] = []

    for path in sorted(luganda_dir.glob("*.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    # Accept various key patterns
                    en = record.get("english") or record.get("en") or record.get("question", "")
                    lg = record.get("luganda") or record.get("lg") or record.get("answer", "")
                    if en and lg:
                        examples.append(
                            {
                                "instruction": en.strip(),
                                "input": "",
                                "output": lg.strip(),
                                "source": path.name,
                                "locale": "lg",
                            }
                        )
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")

    # Also check for CSV files
    for path in sorted(luganda_dir.glob("*.csv")):
        try:
            with open(path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    en = row.get("english") or row.get("en") or row.get("English", "")
                    lg = row.get("luganda") or row.get("lg") or row.get("Luganda", "")
                    if en and lg:
                        examples.append(
                            {
                                "instruction": en.strip(),
                                "input": "",
                                "output": lg.strip(),
                                "source": path.name,
                                "locale": "lg",
                            }
                        )
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")

    if examples:
        print(f"  Loaded {len(examples)} Luganda translation pairs")
    return examples


def load_teacher_qa(artifacts_dir: Path) -> list[dict[str, Any]]:
    """Load teacher-generated QA pairs if available."""
    examples: list[dict[str, Any]] = []
    for name in ("teacher_qa.jsonl", "teacher_qa_gemma.jsonl"):
        path = artifacts_dir / name
        if not path.exists():
            # Check Data/teacher_qa/ (primary location from teacher_qa_generation.py)
            alt = PROJECT_ROOT / "Data" / "teacher_qa" / name
            if alt.exists():
                path = alt
            else:
                # Also check Data/artifacts/
                alt2 = PROJECT_ROOT / "Data" / "artifacts" / name
                if alt2.exists():
                    path = alt2
                else:
                    continue

        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)

                    # Handle both QA and instruction formats
                    if "question" in record and "answer" in record:
                        examples.append(
                            {
                                "instruction": record["question"],
                                "input": "",
                                "output": record["answer"],
                                "source": record.get("source_pdf", path.name),
                            }
                        )
                    elif "text" in record:
                        examples.append(
                            {
                                "text": record["text"],
                                "source": record.get("source", path.name),
                            }
                        )
            print(f"  {path.name}: {len(examples)} examples")
        except Exception as e:
            print(f"  Error loading {path.name}: {e}")

    return examples


# ---------------------------------------------------------------------------
# Export formatters
# ---------------------------------------------------------------------------

def to_gemma_format(example: dict[str, Any]) -> dict[str, str]:
    """Convert instruction example to Gemma chat-template format."""
    if "text" in example and "<start_of_turn>" in str(example.get("text", "")):
        return {"text": example["text"], "source": example.get("source", "")}

    instruction = example.get("instruction", "")
    input_text = example.get("input", "").strip()
    output = example.get("output", "")

    user_content = f"{instruction}\n\n{input_text}" if input_text else instruction
    text = (
        f"<start_of_turn>user\n{user_content.strip()}<end_of_turn>\n"
        f"<start_of_turn>model\n{output.strip()}<end_of_turn>"
    )
    return {"text": text, "source": example.get("source", "")}


def to_instruction_format(example: dict[str, Any]) -> dict[str, Any]:
    """Return clean instruction format (Alpaca-compatible)."""
    return {
        "instruction": example.get("instruction", ""),
        "input": example.get("input", ""),
        "output": example.get("output", ""),
        "source": example.get("source", ""),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare and augment training data for URA Chatbot fine-tuning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --csv-dir Data/dataset --pdf-dir Data/pdfs --output artifacts/training_data.jsonl
  %(prog)s --csv-dir Data/dataset --luganda-dir Data/TTT --output artifacts/training_data.jsonl
""",
    )

    parser.add_argument("--csv-dir", type=Path, required=True, help="Directory containing FAQ CSVs")
    parser.add_argument("--pdf-dir", type=Path, default=None, help="Directory containing PDFs")
    parser.add_argument("--luganda-dir", type=Path, default=None, help="Directory with Luganda data")
    parser.add_argument("--teacher-qa-dir", type=Path, default=None, help="Directory with teacher QA JSONL")
    parser.add_argument("--output", type=Path, required=True, help="Output JSONL path (instruction format)")
    parser.add_argument("--gemma-output", type=Path, default=None, help="Gemma chat-template JSONL output")
    parser.add_argument(
        "--instruction-output", type=Path, default=None, help="Instruction format JSONL output"
    )
    parser.add_argument("--chunk-size", type=int, default=400, help="Words per PDF chunk")
    parser.add_argument("--max-pdfs", type=int, default=None, help="Max PDFs to process")

    args = parser.parse_args()

    print("=" * 70)
    print("URA CHATBOT — DATA AUGMENTATION PIPELINE")
    print("=" * 70)

    all_examples: list[dict[str, Any]] = []

    # 1. CSV FAQs (primary source)
    print(f"\n[1/4] Loading CSV FAQs from {args.csv_dir}")
    if args.csv_dir.exists():
        csv_examples = load_csv_faqs(args.csv_dir)
        all_examples.extend(csv_examples)
        print(f"  Total FAQ examples: {len(csv_examples)}")
    else:
        print(f"  Directory not found: {args.csv_dir}")

    # 2. PDF documents
    print(f"\n[2/4] Extracting from PDFs")
    if args.pdf_dir and args.pdf_dir.exists():
        pdf_examples = load_pdf_chunks(args.pdf_dir, args.chunk_size, args.max_pdfs)
        all_examples.extend(pdf_examples)
        print(f"  Total PDF examples: {len(pdf_examples)}")
    else:
        print("  Skipped (no pdf-dir)")

    # 3. Luganda translations
    print(f"\n[3/4] Loading Luganda data")
    if args.luganda_dir and args.luganda_dir.exists():
        lg_examples = load_luganda_data(args.luganda_dir)
        all_examples.extend(lg_examples)
        print(f"  Total Luganda examples: {len(lg_examples)}")
    else:
        print("  Skipped (no luganda-dir)")

    # 4. Teacher QA pairs
    print(f"\n[4/4] Loading teacher QA data")
    teacher_dir = args.teacher_qa_dir or (PROJECT_ROOT / "Data" / "artifacts")
    teacher_examples = load_teacher_qa(teacher_dir)
    all_examples.extend(teacher_examples)
    print(f"  Total teacher QA examples: {len(teacher_examples)}")

    if not all_examples:
        print("\nNo training examples found!")
        sys.exit(1)

    # Deduplicate by instruction text
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ex in all_examples:
        key = ex.get("instruction", ex.get("text", ""))[:200]
        if key and key not in seen:
            seen.add(key)
            unique.append(ex)
    dedup_removed = len(all_examples) - len(unique)
    all_examples = unique

    print(f"\n{'=' * 70}")
    print(f"Total training examples: {len(all_examples)}")
    if dedup_removed:
        print(f"  Duplicates removed: {dedup_removed}")

    # Write outputs
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Main output (instruction format)
    with open(args.output, "w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(to_instruction_format(ex), ensure_ascii=False) + "\n")
    print(f"\nInstruction format: {args.output} ({len(all_examples)} examples)")

    # Gemma format
    gemma_path = args.gemma_output or args.output.with_name("gemma_training.jsonl")
    gemma_path.parent.mkdir(parents=True, exist_ok=True)
    with open(gemma_path, "w", encoding="utf-8") as f:
        for ex in all_examples:
            f.write(json.dumps(to_gemma_format(ex), ensure_ascii=False) + "\n")
    print(f"Gemma format:       {gemma_path} ({len(all_examples)} examples)")

    # Instruction format (explicit)
    if args.instruction_output:
        args.instruction_output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.instruction_output, "w", encoding="utf-8") as f:
            for ex in all_examples:
                f.write(json.dumps(to_instruction_format(ex), ensure_ascii=False) + "\n")
        print(f"Instruction output: {args.instruction_output}")

    # Summary by source
    sources: dict[str, int] = {}
    for ex in all_examples:
        src = ex.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    print(f"\nSources: {len(sources)} files")
    for src, count in sorted(sources.items(), key=lambda x: -x[1])[:10]:
        print(f"  {src}: {count}")

    print(f"\nData augmentation complete!")


if __name__ == "__main__":
    main()
