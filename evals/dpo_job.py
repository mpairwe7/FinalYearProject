"""Preference fine-tune scaffold (G29). Does not train a model.

Exports pairs, then exits. A real Axolotl/DPO run is refused unless
``EVAL_GATE_OK=1`` and ``DPO_RUN=1`` are both set after a measured eval.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.export_preferences import build_bilevel_preference_bundle, export_pairs  # noqa: E402


def main() -> int:
    dest = Path(os.getenv("PREFERENCE_EXPORT_PATH") or "Data/eval/preferences.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = export_pairs(dest)
    rows = [
        json.loads(line)
        for line in dest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if dest.is_file() else []
    bundle = build_bilevel_preference_bundle(rows)
    bundle_path = dest.with_suffix(".bilevel_bundle.json")
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"exported {count} pairs to {dest} (bi-level bundle: "
        f"{len(bundle['dpo_length_debiased_replay'])} DPO+replay pairs, "
        f"{len(bundle['retrieval_and_router_preferences'])} retrieval/router supervision items)"
    )
    if os.getenv("DPO_RUN", "").lower() in ("1", "true", "yes"):
        if os.getenv("EVAL_GATE_OK", "").lower() not in ("1", "true", "yes"):
            print("refusing DPO_RUN without EVAL_GATE_OK")
            return 2
        print("EVAL_GATE_OK set, but no trainer is wired. Stop.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
