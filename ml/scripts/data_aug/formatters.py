"""Output formats.

Primary format is ``messages`` JSONL (OpenAI chat format) because TRL's
``SFTTrainer >= 0.12`` handles it natively via ``tokenizer.apply_chat_template``
and applies completion-only loss masking automatically. This is the 2025-2026
industry standard for SFT datasets.

Secondary formats (opt-in):
    * legacy instruction-format JSONL (Alpaca compatible) — for any downstream
      consumer that is not yet updated.
    * Parquet — canonical HF ``datasets`` format, 3-5× smaller, supports
      streaming and column projection. Required for HF Hub uploads.

We deliberately do *not* bake model-specific chat templates
(``<start_of_turn>``, ``<|start_header_id|>``) into the dataset. That is the
trainer's responsibility.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional

from ml.scripts.data_aug.schema import TrainingExample

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSONL writers
# ---------------------------------------------------------------------------


def write_messages_jsonl(
    examples: Iterable[TrainingExample],
    out_path: Path,
) -> int:
    """Canonical messages-format JSONL. One row per example."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex.to_row(), ensure_ascii=False) + "\n")
            count += 1
    log.info("formatters: wrote %d rows to %s", count, out_path)
    return count


def write_legacy_instruction_jsonl(
    examples: Iterable[TrainingExample],
    out_path: Path,
) -> int:
    """Alpaca-style ``{instruction, input, output, source}`` JSONL.

    Kept for backwards compatibility with callers that haven't been updated
    to consume the messages format. The ``instruction`` column holds the
    first user turn, and ``output`` holds the last assistant turn.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for ex in examples:
            row = {
                "instruction": ex.user_text(),
                "input": "",
                "output": ex.assistant_text(),
                "source": ex.metadata.source,
                "source_type": ex.metadata.source_type.value,
                "task": ex.metadata.task.value,
                "language": ex.metadata.language,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    log.info("formatters: wrote %d legacy rows to %s", count, out_path)
    return count


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------


def _parquet_schema():
    """Build an explicit Parquet schema so columns have stable types across
    runs. Avoids Arrow inferring ``null`` types on the first batch when a
    nullable column happens to be all-null, which makes schema evolution
    painful at downstream consumers."""
    import pyarrow as pa  # type: ignore

    message_struct = pa.struct([
        pa.field("role", pa.string(), nullable=False),
        pa.field("content", pa.string(), nullable=False),
    ])
    return pa.schema([
        pa.field("messages", pa.list_(message_struct), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("source_type", pa.string(), nullable=False),
        pa.field("task", pa.string(), nullable=False),
        pa.field("language", pa.string(), nullable=False),
        pa.field("tag", pa.string(), nullable=True),
        pa.field("content_hash", pa.string(), nullable=False),
        pa.field("token_count", pa.int64(), nullable=True),
        pa.field("quality_score", pa.float64(), nullable=True),
        pa.field("chunk_id", pa.int64(), nullable=True),
        pa.field("doc_id", pa.string(), nullable=True),
        # ``extra`` is serialised to a JSON string so heterogeneous per-row
        # keys don't break Arrow's struct inference at scale.
        pa.field("extra", pa.string(), nullable=True),
    ])


def _row_for_parquet(ex: TrainingExample) -> dict[str, Any]:
    row = ex.to_row()
    row["extra"] = json.dumps(row.get("extra") or {}, ensure_ascii=False)
    return row


def write_parquet(
    examples: Iterable[TrainingExample],
    out_path: Path,
) -> Optional[int]:
    """Write a Parquet file with a stable, explicit schema.

    Returns ``None`` (no-op with warning) if ``pyarrow`` is missing, ``0``
    if the iterable is empty, else the number of rows written.
    """
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        log.warning("pyarrow not installed — skipping Parquet output for %s", out_path)
        return None

    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [_row_for_parquet(ex) for ex in examples]
    if not rows:
        log.warning("formatters: no rows, not writing %s", out_path)
        return 0

    schema = _parquet_schema()
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, out_path, compression="zstd")
    log.info("formatters: wrote %d rows to %s (parquet, zstd)", len(rows), out_path)
    return len(rows)
