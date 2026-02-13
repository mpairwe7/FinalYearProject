#!/usr/bin/env python3
"""
Export a TPU-friendly dataset from JSONL training data.

Outputs:
  - text_train.jsonl / text_val.jsonl
  - packed_train-*.jsonl / packed_val-*.jsonl
  - manifest.json

Packed files are fixed-length token blocks suitable for TPU/XLA training loops.
"""

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def normalize_whitespace(text: str) -> str:
    return " ".join(str(text).replace("\r", " ").replace("\n", " ").split()).strip()


def row_to_text(row: Dict[str, Any]) -> str:
    text = row.get("text")
    if text:
        return normalize_whitespace(text)

    question = row.get("question") or row.get("instruction")
    answer = row.get("answer") or row.get("output")
    if question and answer:
        q = normalize_whitespace(question)
        a = normalize_whitespace(answer)
        return (
            f"<start_of_turn>user\n{q}<end_of_turn>\n"
            f"<start_of_turn>model\n{a}<end_of_turn>"
        )

    messages = row.get("messages")
    if isinstance(messages, list) and messages:
        parts: List[str] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = normalize_whitespace(msg.get("role", "user") or "user")
            content = normalize_whitespace(msg.get("content", ""))
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts).strip()

    return ""


def find_first_existing(paths: List[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def load_unique_texts(path: Path, min_chars: int, max_chars: int, max_samples: int) -> List[str]:
    seen = set()
    texts: List[str] = []

    for row in read_jsonl(path):
        text = row_to_text(row)
        if not text:
            continue
        if len(text) < min_chars:
            continue
        if len(text) > max_chars:
            text = text[:max_chars]

        key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        texts.append(text)

        if max_samples > 0 and len(texts) >= max_samples:
            break

    return texts


def split_list(items: List[Any], val_ratio: float, seed: int) -> tuple[List[Any], List[Any]]:
    if not items:
        return [], []

    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)

    val_count = max(1, int(len(shuffled) * val_ratio))
    if val_count >= len(shuffled):
        val_count = max(1, len(shuffled) - 1)

    val_items = shuffled[:val_count]
    train_items = shuffled[val_count:]
    return train_items, val_items


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def write_sharded_jsonl(
    output_dir: Path,
    prefix: str,
    rows: List[Dict[str, Any]],
    shard_size: int,
) -> List[Path]:
    files: List[Path] = []
    if not rows:
        return files

    shard_count = math.ceil(len(rows) / shard_size)
    for idx in range(shard_count):
        start = idx * shard_size
        end = min(len(rows), start + shard_size)
        shard_rows = rows[start:end]
        shard_path = output_dir / f"{prefix}-{idx:05d}.jsonl"
        write_jsonl(shard_path, shard_rows)
        files.append(shard_path)
    return files


def tokenize_and_pack(
    texts: List[str],
    tokenizer_name: str,
    seq_len: int,
) -> tuple[List[List[int]], str, int]:
    mode = "hf_tokenizer"
    eos_id = None

    try:
        from transformers import AutoTokenizer  # type: ignore
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_name, use_fast=True)
        if tokenizer.eos_token_id is not None:
            eos_id = tokenizer.eos_token_id
        elif tokenizer.pad_token_id is not None:
            eos_id = tokenizer.pad_token_id
        else:
            eos_id = 1

        stream: List[int] = []
        for text in texts:
            input_ids = tokenizer(
                text,
                add_special_tokens=False,
                truncation=False,
            )["input_ids"]
            if not input_ids:
                continue
            stream.extend(input_ids)
            stream.append(eos_id)
    except Exception:
        mode = "whitespace_fallback"
        token_to_id: Dict[str, int] = {"<eos>": 1}
        next_id = 2
        stream = []
        eos_id = 1
        for text in texts:
            for token in text.split():
                if token not in token_to_id:
                    token_to_id[token] = next_id
                    next_id += 1
                stream.append(token_to_id[token])
            stream.append(eos_id)

    total_tokens = len(stream)
    packed: List[List[int]] = []
    for i in range(0, max(0, total_tokens - seq_len + 1), seq_len):
        packed.append(stream[i : i + seq_len])

    return packed, mode, total_tokens


def to_packed_rows(chunks: List[List[int]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for chunk in chunks:
        rows.append(
            {
                "input_ids": chunk,
                "attention_mask": [1] * len(chunk),
                "labels": chunk,
            }
        )
    return rows


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export TPU-ready packed dataset")
    parser.add_argument(
        "--input-jsonl",
        action="append",
        required=True,
        help="Input JSONL files in priority order; first existing file is used.",
    )
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--tokenizer", default="google/flan-t5-small")
    parser.add_argument("--seq-len", type=int, default=512)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=4096)
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--shard-size", type=int, default=2000)
    return parser.parse_args()


def main() -> int:
    args = build_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = [Path(p) for p in args.input_jsonl]
    selected_input = find_first_existing(inputs)
    if selected_input is None:
        print("No input JSONL file was found.")
        for candidate in inputs:
            print(f" - missing: {candidate}")
        return 1

    print(f"Using input file: {selected_input}")
    texts = load_unique_texts(
        selected_input,
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        max_samples=args.max_samples,
    )

    if not texts:
        print("No valid training texts were extracted from input.")
        return 1

    train_texts, val_texts = split_list(texts, args.val_ratio, args.seed)
    if not train_texts or not val_texts:
        print("Dataset split produced empty train or validation split.")
        return 1

    train_text_rows = [{"text": t} for t in train_texts]
    val_text_rows = [{"text": t} for t in val_texts]
    train_text_path = output_dir / "text_train.jsonl"
    val_text_path = output_dir / "text_val.jsonl"
    write_jsonl(train_text_path, train_text_rows)
    write_jsonl(val_text_path, val_text_rows)

    packed_train_chunks, tokenization_mode, train_total_tokens = tokenize_and_pack(
        train_texts, args.tokenizer, args.seq_len
    )
    packed_val_chunks, _, val_total_tokens = tokenize_and_pack(
        val_texts, args.tokenizer, args.seq_len
    )

    train_packed_rows = to_packed_rows(packed_train_chunks)
    val_packed_rows = to_packed_rows(packed_val_chunks)

    if not train_packed_rows or not val_packed_rows:
        print("Packing produced empty train or validation blocks. Lower --seq-len or add data.")
        return 1

    train_files = write_sharded_jsonl(
        output_dir=output_dir,
        prefix="packed_train",
        rows=train_packed_rows,
        shard_size=args.shard_size,
    )
    val_files = write_sharded_jsonl(
        output_dir=output_dir,
        prefix="packed_val",
        rows=val_packed_rows,
        shard_size=args.shard_size,
    )

    manifest = {
        "input_jsonl": str(selected_input),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "tokenizer": args.tokenizer,
        "tokenization_mode": tokenization_mode,
        "seq_len": args.seq_len,
        "val_ratio": args.val_ratio,
        "seed": args.seed,
        "counts": {
            "raw_unique_text_rows": len(texts),
            "train_text_rows": len(train_text_rows),
            "val_text_rows": len(val_text_rows),
            "train_total_tokens": train_total_tokens,
            "val_total_tokens": val_total_tokens,
            "train_packed_rows": len(train_packed_rows),
            "val_packed_rows": len(val_packed_rows),
            "train_shards": len(train_files),
            "val_shards": len(val_files),
        },
        "files": {
            "train_text": train_text_path.name,
            "val_text": val_text_path.name,
            "train_packed_shards": [p.name for p in train_files],
            "val_packed_shards": [p.name for p in val_files],
        },
    }

    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("TPU-ready export complete.")
    print(f" - text rows: train={len(train_text_rows)}, val={len(val_text_rows)}")
    print(f" - packed rows: train={len(train_packed_rows)}, val={len(val_packed_rows)}")
    print(f" - tokenization mode: {tokenization_mode}")
    print(f" - manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
