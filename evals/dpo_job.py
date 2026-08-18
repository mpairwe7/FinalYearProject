"""Preference fine-tune scaffold (G29). Does not train a model.

Exports pairs, then exits. A real Axolotl/DPO run is refused unless
``EVAL_GATE_OK=1`` and ``DPO_RUN=1`` are both set after a measured eval.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.export_preferences import export_pairs  # noqa: E402


def main() -> int:
    dest = Path(os.getenv("PREFERENCE_EXPORT_PATH") or "Data/eval/preferences.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = export_pairs(dest)
    print(f"exported {count} pairs to {dest}")
    if os.getenv("DPO_RUN", "").lower() in ("1", "true", "yes"):
        if os.getenv("EVAL_GATE_OK", "").lower() not in ("1", "true", "yes"):
            print("refusing DPO_RUN without EVAL_GATE_OK")
            return 2
        print("EVAL_GATE_OK set, but no trainer is wired. Stop.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
