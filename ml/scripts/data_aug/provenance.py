"""Provenance manifest + auto-generated Data Card.

The manifest is the single-file record of what this pipeline produced:

    * config (all CLI flags at run-time)
    * input files with their SHA-256 hashes
    * per-stage statistics (ingest, dedup, quality, split)
    * output files with SHA-256 hashes and row counts
    * git commit SHA + dirty flag
    * schema version + pipeline version + timestamp

It satisfies:
    - EU AI Act Art. 10 data-governance records
    - HuggingFace Croissant metadata (basic fields)
    - DVC / MLflow reproducibility requirements

The Data Card is a human-readable Markdown render of the manifest,
intended to be committed alongside the dataset when published to HF Hub.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ml.scripts.data_aug.schema import SCHEMA_VERSION
from ml.scripts.data_aug.text_utils import file_sha256

log = logging.getLogger(__name__)

PIPELINE_VERSION = "2026.1.0"


# ---------------------------------------------------------------------------
# Git info
# ---------------------------------------------------------------------------


def _git_head(repo: Path) -> dict[str, Any]:
    """Return commit SHA + dirty-flag + branch, or empty if not a git repo."""

    def _run(cmd: list[str]) -> str:
        return subprocess.check_output(
            cmd, cwd=repo, stderr=subprocess.DEVNULL, text=True
        ).strip()

    info: dict[str, Any] = {}
    try:
        info["commit"] = _run(["git", "rev-parse", "HEAD"])
        info["branch"] = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        info["dirty"] = bool(_run(["git", "status", "--porcelain"]))
    except Exception:
        info = {"commit": None, "branch": None, "dirty": None}
    return info


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


@dataclass
class ManifestBuilder:
    """Accumulates state throughout a pipeline run, then materialises it."""

    config: dict[str, Any]
    repo_root: Path
    inputs: list[dict[str, Any]] = field(default_factory=list)
    stages: dict[str, Any] = field(default_factory=dict)
    outputs: list[dict[str, Any]] = field(default_factory=list)

    def record_input_file(self, path: Path, role: str) -> None:
        if not path.exists() or not path.is_file():
            return
        try:
            self.inputs.append(
                {
                    "role": role,
                    "path": str(path.relative_to(self.repo_root))
                    if path.is_absolute() and str(path).startswith(str(self.repo_root))
                    else str(path),
                    "bytes": path.stat().st_size,
                    "sha256": file_sha256(str(path)),
                }
            )
        except Exception as exc:
            log.debug("provenance: skipping input %s: %s", path, exc)

    def record_input_directory(self, path: Path, role: str, patterns: list[str]) -> None:
        if not path.exists() or not path.is_dir():
            return
        seen: set[Path] = set()
        for pat in patterns:
            for f in sorted(path.glob(pat)):
                if f in seen:
                    continue
                seen.add(f)
                self.record_input_file(f, role)

    def record_stage(self, name: str, payload: dict[str, Any]) -> None:
        self.stages[name] = payload

    def record_output(self, path: Path, role: str, rows: Optional[int] = None) -> None:
        if not path.exists() or not path.is_file():
            return
        self.outputs.append(
            {
                "role": role,
                "path": str(path.relative_to(self.repo_root))
                if path.is_absolute() and str(path).startswith(str(self.repo_root))
                else str(path),
                "rows": rows,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(str(path)),
            }
        )

    def build(self) -> dict[str, Any]:
        return {
            "pipeline_version": PIPELINE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "host": os.uname().nodename if hasattr(os, "uname") else None,
            "git": _git_head(self.repo_root),
            "config": self.config,
            "inputs": self.inputs,
            "stages": self.stages,
            "outputs": self.outputs,
        }

    def write(self, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(self.build(), indent=2) + "\n", encoding="utf-8")
        log.info("provenance: wrote manifest %s", out_path)
        return out_path


# ---------------------------------------------------------------------------
# Data card
# ---------------------------------------------------------------------------


DATA_CARD_TEMPLATE = """\
# URA Tax Assistant — Training Dataset

*Auto-generated by `ml/scripts/data_augmentation.py` — do not edit by hand.*

- **Pipeline version:** `{pipeline_version}`
- **Schema version:** `{schema_version}`
- **Generated at:** `{created_at_utc}`
- **Git commit:** `{git_commit}` (dirty: `{git_dirty}`) on `{git_branch}`

## Summary

| Split | Rows |
|-------|------|
| train | {train_rows} |
| val   | {val_rows} |
| test  | {test_rows} |

## Provenance

- Total input files tracked: **{input_count}**
- Total output artifacts: **{output_count}**

## Pipeline stages

```
{stages_block}
```

## Licensing & intended use

This dataset was assembled from:
- Public Uganda Revenue Authority FAQs and publications (tax guidance).
- Synthetic QA generated by a teacher LLM from URA PDFs.
- Curated refusal / safety examples.

**Intended use:** fine-tuning a domain-adapted assistant for Ugandan tax
questions. Not intended for personalised tax advice — model outputs must
be reviewed by a qualified tax professional before relying on them for
any decision. All personally identifiable information (TINs, NINs,
phone numbers, emails, account numbers) was regex-redacted during
ingestion.

## Reproducibility

```bash
git checkout {git_commit}
python ml/scripts/data_augmentation.py --config <saved config>
```

The manifest `manifest.json` accompanying this data card contains the
full list of input SHAs, per-stage statistics, and the exact CLI config
used to produce this dataset.
"""


def write_data_card(manifest: dict[str, Any], out_path: Path) -> Path:
    git = manifest.get("git") or {}
    stages = manifest.get("stages") or {}
    split = stages.get("split", {})
    stages_block = json.dumps(stages, indent=2)

    rendered = DATA_CARD_TEMPLATE.format(
        pipeline_version=manifest["pipeline_version"],
        schema_version=manifest["schema_version"],
        created_at_utc=manifest["created_at_utc"],
        git_commit=git.get("commit") or "unknown",
        git_dirty=git.get("dirty"),
        git_branch=git.get("branch") or "unknown",
        train_rows=split.get("train", "?"),
        val_rows=split.get("val", "?"),
        test_rows=split.get("test", "?"),
        input_count=len(manifest.get("inputs", [])),
        output_count=len(manifest.get("outputs", [])),
        stages_block=stages_block,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    log.info("provenance: wrote data card %s", out_path)
    return out_path
