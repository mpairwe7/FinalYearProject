"""Unit tests for the 2026 data augmentation pipeline (``ml/scripts/data_aug``).

Exercises the schema, text utilities, loaders, dedup, quality filter,
splitter, formatters, provenance, and the full orchestrator with a small
temp directory of synthetic inputs. Every test is pure-stdlib + pytest and
uses ``tempfile`` — no network, no HF download, no real PDFs.

The CI ``lint-and-test`` job runs ``pytest tests/`` so these checks gate
every push that touches the pipeline package.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# schema.py
# ---------------------------------------------------------------------------


class TestSchema:
    def test_message_rejects_empty_content(self):
        from ml.scripts.data_aug.schema import Message

        with pytest.raises(Exception):
            Message(role="user", content="")

    def test_message_rejects_whitespace_only(self):
        from ml.scripts.data_aug.schema import Message

        with pytest.raises(Exception):
            Message(role="user", content="   ")

    def test_example_must_end_on_assistant(self):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
        )

        with pytest.raises(Exception):
            TrainingExample(
                messages=[
                    Message(role="user", content="q"),
                    Message(role="user", content="q2"),
                ],
                metadata=Metadata(
                    source="x",
                    source_type=SourceType.CSV_FAQ,
                    task=TaskType.QA,
                    content_hash="a" * 16,
                ),
            )

    def test_example_requires_user_turn(self):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
        )

        with pytest.raises(Exception):
            TrainingExample(
                messages=[
                    Message(role="system", content="s"),
                    Message(role="assistant", content="a"),
                ],
                metadata=Metadata(
                    source="x",
                    source_type=SourceType.CSV_FAQ,
                    task=TaskType.QA,
                    content_hash="a" * 16,
                ),
            )

    def test_content_hash_is_normalised(self):
        from ml.scripts.data_aug.schema import content_hash

        assert content_hash("VAT rate?", "18%") == content_hash(
            "  vat rate?  \n", "18%"
        )
        assert content_hash("VAT rate?", "18%") != content_hash("VAT rate?", "20%")

    def test_to_row_flattens_metadata(self):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
            content_hash,
        )

        ex = TrainingExample(
            messages=[
                Message(role="user", content="Q"),
                Message(role="assistant", content="A"),
            ],
            metadata=Metadata(
                source="f.csv",
                source_type=SourceType.CSV_FAQ,
                task=TaskType.QA,
                language="en",
                tag="vat",
                content_hash=content_hash("Q", "A"),
            ),
        )
        row = ex.to_row()
        assert set(row.keys()) >= {
            "messages", "source", "source_type", "task", "language", "tag",
            "content_hash",
        }
        assert row["source_type"] == "csv_faq"
        assert row["task"] == "qa"


# ---------------------------------------------------------------------------
# text_utils.py
# ---------------------------------------------------------------------------


class TestTextUtils:
    def test_clean_collapses_whitespace(self):
        from ml.scripts.data_aug.text_utils import clean_text

        assert clean_text("  hello\u00a0 \t world  ") == "hello world"

    def test_clean_unspaces_letters(self):
        from ml.scripts.data_aug.text_utils import clean_text

        # "T A X A T I O N" → "TAXATION"
        assert "TAXATION" in clean_text("T A X A T I O N handbook")

    def test_redact_pii_phone(self):
        from ml.scripts.data_aug.text_utils import redact_pii

        text, r = redact_pii("Call me on 0712345678 please")
        assert "[PHONE]" in text
        assert "0712345678" not in text
        assert r.phone == 1

    def test_redact_pii_email(self):
        from ml.scripts.data_aug.text_utils import redact_pii

        text, r = redact_pii("Write to alice@example.com")
        assert "[EMAIL]" in text
        assert r.email == 1

    def test_redact_pii_tin(self):
        from ml.scripts.data_aug.text_utils import redact_pii

        text, r = redact_pii("TIN 1012345678 is my number")
        assert "[TIN]" in text
        assert r.tin == 1

    def test_redact_pii_url(self):
        from ml.scripts.data_aug.text_utils import redact_pii

        text, r = redact_pii("Visit https://ura.go.ug/about for info")
        assert "[URL]" in text
        assert r.url == 1

    def test_repetition_ratio(self):
        from ml.scripts.data_aug.text_utils import repetition_ratio

        unique = "The quick brown fox jumps over the lazy dog today now"
        assert repetition_ratio(unique, n=4) == 0.0
        bomb = ("this is a test " * 8).strip()
        assert repetition_ratio(bomb, n=4) > 0.5

    def test_symbol_ratio(self):
        from ml.scripts.data_aug.text_utils import symbol_ratio

        assert symbol_ratio("hello world") < 0.1
        assert symbol_ratio("###########!!!!!!!!") > 0.8


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _write_csv_faqs(tmpdir: Path) -> Path:
    csv_path = tmpdir / "ura_test_faqs.csv"
    csv_path.write_text(
        "question,answer\n"
        '"What is VAT?","The standard VAT rate in Uganda is 18 percent."\n'
        '"How do I file returns?","Log into the URA portal and submit monthly."\n'
        '"What is a TIN?","A Tax Identification Number is a unique identifier."\n'
        '"When is the deadline?","The VAT deadline is the 15th of the following month."\n'
        '"Short","x"\n',  # Will be dropped by the _make_example length check.
        encoding="utf-8",
    )
    return csv_path


class TestLoaders:
    def test_load_csv_faqs_basic(self):
        from ml.scripts.data_aug.loaders import load_csv_faqs

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write_csv_faqs(tmp)
            examples = list(load_csv_faqs(tmp))
            # 5 rows in CSV; one ("Short", "x") is below min length and is dropped.
            assert len(examples) == 4
            assert all(ex.metadata.source_type.value == "csv_faq" for ex in examples)
            assert all(ex.metadata.task.value == "qa" for ex in examples)
            # System prompt is injected
            assert examples[0].messages[0].role == "system"

    def test_load_refusals_curated(self):
        from ml.scripts.data_aug.loaders import load_refusal_examples

        examples = list(load_refusal_examples())
        assert len(examples) >= 5
        assert all(ex.metadata.source_type.value == "refusal" for ex in examples)
        assert all(ex.metadata.task.value == "refusal" for ex in examples)


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def _make(self, q, a, source_type_value="csv_faq"):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
            content_hash,
        )

        return TrainingExample(
            messages=[
                Message(role="user", content=q),
                Message(role="assistant", content=a),
            ],
            metadata=Metadata(
                source="test",
                source_type=SourceType(source_type_value),
                task=TaskType.QA,
                content_hash=content_hash(q, a),
            ),
        )

    def test_exact_dedup(self):
        from ml.scripts.data_aug.dedup import DedupStats, exact_dedup

        items = [
            self._make("Q1", "A1"),
            self._make("Q1", "A1"),  # dup
            self._make("Q2", "A2"),
            self._make("  q1  ", "A1"),  # dup (normalised)
        ]
        stats = DedupStats()
        kept = list(exact_dedup(items, stats))
        assert len(kept) == 2
        assert stats.exact_removed == 2

    def test_dedup_all_end_to_end(self):
        from ml.scripts.data_aug.dedup import dedup_all

        items = [
            self._make("VAT?", "18%"),
            self._make("VAT?", "18%"),
            self._make("NIN?", "National ID"),
        ]
        kept, stats = dedup_all(items)
        assert len(kept) == 2
        assert stats.output_count == 2


# ---------------------------------------------------------------------------
# Quality filter
# ---------------------------------------------------------------------------


class TestQuality:
    def _make(self, q, a):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
            content_hash,
        )

        return TrainingExample(
            messages=[
                Message(role="user", content=q),
                Message(role="assistant", content=a),
            ],
            metadata=Metadata(
                source="t",
                source_type=SourceType.CSV_FAQ,
                task=TaskType.QA,
                content_hash=content_hash(q, a),
            ),
        )

    def test_filter_drops_short_response(self):
        from ml.scripts.data_aug.quality import QualityConfig, filter_quality

        items = [
            self._make(
                "What is VAT?",
                "The VAT rate is 18 percent in Uganda.",
            ),
            self._make("What is VAT?", "tiny"),  # 1 word
        ]
        cfg = QualityConfig(tokenizer_model=None, min_tokens=2)
        kept, stats = filter_quality(items, cfg)
        assert len(kept) == 1
        assert stats.dropped_short_response == 1

    def test_filter_writes_token_count(self):
        from ml.scripts.data_aug.quality import QualityConfig, filter_quality

        items = [
            self._make(
                "What is the VAT rate?",
                "The standard VAT rate in Uganda is eighteen percent.",
            ),
        ]
        cfg = QualityConfig(tokenizer_model=None)
        kept, _ = filter_quality(items, cfg)
        assert kept[0].metadata.token_count is not None
        assert kept[0].metadata.token_count > 0

    def test_source_cap_applies(self):
        from ml.scripts.data_aug.quality import QualityConfig, filter_quality

        items = [
            self._make(f"question {i}?", f"answer number {i} is here always")
            for i in range(10)
        ]
        cfg = QualityConfig(
            tokenizer_model=None, source_caps={"csv_faq": 3}, min_tokens=2
        )
        kept, stats = filter_quality(items, cfg)
        assert len(kept) == 3
        assert stats.dropped_capped == 7


# ---------------------------------------------------------------------------
# Splitter
# ---------------------------------------------------------------------------


class TestSplitter:
    def _examples(self, n: int, tag: str = "t"):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
            content_hash,
        )

        return [
            TrainingExample(
                messages=[
                    Message(role="user", content=f"Q{i}"),
                    Message(role="assistant", content=f"A{i}"),
                ],
                metadata=Metadata(
                    source="t",
                    source_type=SourceType.CSV_FAQ,
                    task=TaskType.QA,
                    tag=tag,
                    content_hash=content_hash(f"Q{i}", f"A{i}"),
                ),
            )
            for i in range(n)
        ]

    def test_deterministic_split(self):
        from ml.scripts.data_aug.splitters import SplitConfig, stratified_split

        items = self._examples(100)
        cfg = SplitConfig(val_ratio=0.1, test_ratio=0.1, seed=42)
        t1, v1, te1, _ = stratified_split(items, cfg)
        t2, v2, te2, _ = stratified_split(items, cfg)
        hashes_t1 = [ex.metadata.content_hash for ex in t1]
        hashes_t2 = [ex.metadata.content_hash for ex in t2]
        assert hashes_t1 == hashes_t2

    def test_split_sums_to_total(self):
        from ml.scripts.data_aug.splitters import SplitConfig, stratified_split

        items = self._examples(50)
        train, val, test, stats = stratified_split(
            items, SplitConfig(val_ratio=0.1, test_ratio=0.1)
        )
        assert len(train) + len(val) + len(test) <= len(items)
        assert stats.contamination_leaked == 0

    def test_small_strata_goes_to_train(self):
        from ml.scripts.data_aug.splitters import SplitConfig, stratified_split

        items = self._examples(2)  # too small to split
        train, val, test, _ = stratified_split(items, SplitConfig())
        assert len(train) == 2
        assert len(val) == 0
        assert len(test) == 0


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class TestFormatters:
    def _examples(self):
        from ml.scripts.data_aug.schema import (
            Message,
            Metadata,
            SourceType,
            TaskType,
            TrainingExample,
            content_hash,
        )

        return [
            TrainingExample(
                messages=[
                    Message(role="system", content="You are the URA Tax Assistant."),
                    Message(role="user", content="What is VAT?"),
                    Message(role="assistant", content="Eighteen percent."),
                ],
                metadata=Metadata(
                    source="t", source_type=SourceType.CSV_FAQ, task=TaskType.QA,
                    content_hash=content_hash("What is VAT?", "Eighteen percent."),
                ),
            )
        ]

    def test_messages_jsonl_roundtrip(self):
        from ml.scripts.data_aug.formatters import write_messages_jsonl

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "msg.jsonl"
            n = write_messages_jsonl(self._examples(), out)
            assert n == 1
            row = json.loads(out.read_text().splitlines()[0])
            assert row["messages"][0]["role"] == "system"
            assert row["messages"][-1]["role"] == "assistant"
            assert row["source_type"] == "csv_faq"

    def test_legacy_jsonl_format(self):
        from ml.scripts.data_aug.formatters import write_legacy_instruction_jsonl

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "legacy.jsonl"
            n = write_legacy_instruction_jsonl(self._examples(), out)
            assert n == 1
            row = json.loads(out.read_text().splitlines()[0])
            assert "instruction" in row
            assert "output" in row
            assert row["output"] == "Eighteen percent."


# ---------------------------------------------------------------------------
# End-to-end pipeline (in-memory, CSV-only)
# ---------------------------------------------------------------------------


class TestPipelineEndToEnd:
    def test_full_run_writes_manifest_and_splits(self):
        from ml.scripts.data_aug.pipeline import (
            PipelineConfig,
            QualityConfig,
            SplitConfig,
            run_pipeline,
        )

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            csv_dir = tmp / "csv"
            csv_dir.mkdir()
            # 24 rows so stratified split can allocate val + test
            csv_path = csv_dir / "ura_bulk_faqs.csv"
            lines = ["question,answer"]
            for i in range(24):
                lines.append(
                    f'"How is tax {i} computed?","Tax {i} is computed at '
                    f'a flat rate depending on the taxpayer category and income."'
                )
            csv_path.write_text("\n".join(lines), encoding="utf-8")

            out_dir = tmp / "out"
            cfg = PipelineConfig(
                csv_dir=csv_dir,
                output_dir=out_dir,
                emit_parquet=False,
                emit_pdf_corpus=False,
                emit_pdf_retrieval=False,
                include_refusals=True,
                quality=QualityConfig(tokenizer_model=None, min_tokens=4),
                split=SplitConfig(val_ratio=0.1, test_ratio=0.1, seed=42),
            )
            manifest = run_pipeline(cfg)

            # Manifest invariants
            assert manifest["pipeline_version"]
            assert manifest["schema_version"]
            split = manifest["stages"]["split"]
            assert split["contamination_leaked"] == 0
            assert split["train"] + split["val"] + split["test"] > 0

            # Files written
            assert (out_dir / "train.messages.jsonl").exists()
            assert (out_dir / "val.messages.jsonl").exists()
            assert (out_dir / "test.messages.jsonl").exists()
            assert (out_dir / "training_data.jsonl").exists()
            assert (out_dir / "training_data.messages.jsonl").exists()
            assert (out_dir / "manifest.json").exists()
            assert (out_dir / "DATA_CARD.md").exists()

            # Every row is valid messages JSONL
            for name in ("train", "val", "test"):
                p = out_dir / f"{name}.messages.jsonl"
                for line in p.read_text().splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    assert "messages" in row and isinstance(row["messages"], list)
                    assert row["messages"][-1]["role"] == "assistant"
                    assert row["content_hash"]

    def test_dry_run_writes_no_files(self):
        from ml.scripts.data_aug.pipeline import PipelineConfig, run_pipeline, QualityConfig

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            csv_dir = tmp / "csv"
            csv_dir.mkdir()
            _write_csv_faqs(csv_dir)
            out_dir = tmp / "out"
            cfg = PipelineConfig(
                csv_dir=csv_dir,
                output_dir=out_dir,
                emit_pdf_corpus=False,
                emit_pdf_retrieval=False,
                include_refusals=True,
                quality=QualityConfig(tokenizer_model=None, min_tokens=2),
                dry_run=True,
            )
            manifest = run_pipeline(cfg)
            assert "split" in manifest["stages"]
            # Output dir may exist (manifest step uses it) but should contain
            # no JSONL / Parquet data artefacts.
            if out_dir.exists():
                existing = [p.name for p in out_dir.iterdir()]
                assert all(
                    not (name.endswith(".jsonl") or name.endswith(".parquet"))
                    for name in existing
                ), f"dry-run wrote data files: {existing}"


# ---------------------------------------------------------------------------
# fine_tune_gemma integration (no model load, just format functions)
# ---------------------------------------------------------------------------


class TestFineTuneIntegration:
    def test_messages_to_gemma(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ft_gemma",
            str(PROJECT_ROOT / "ml" / "scripts" / "fine_tune_gemma.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        row = {
            "messages": [
                {"role": "system", "content": "You are URA."},
                {"role": "user", "content": "VAT?"},
                {"role": "assistant", "content": "18%"},
            ]
        }
        out = mod.format_for_gemma(row)
        assert "<start_of_turn>user" in out["text"]
        assert "<start_of_turn>model" in out["text"]
        # System prompt folded into user turn (matches apply_chat_template)
        assert "You are URA." in out["text"]
        assert "18%" in out["text"]

    def test_messages_to_llama_keeps_system(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ft_llama",
            str(PROJECT_ROOT / "ml" / "scripts" / "fine_tune_gemma.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        row = {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
            ]
        }
        out = mod.format_for_llama(row)
        assert "<|start_header_id|>system<|end_header_id|>" in out["text"]
        assert "<|start_header_id|>assistant<|end_header_id|>" in out["text"]

    def test_legacy_alpaca_still_works(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ft_legacy",
            str(PROJECT_ROOT / "ml" / "scripts" / "fine_tune_gemma.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        row = {"instruction": "Q?", "input": "", "output": "A."}
        out = mod.format_for_gemma(row)
        assert "Q?" in out["text"]
        assert "A." in out["text"]
