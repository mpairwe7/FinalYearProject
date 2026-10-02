"""Round-trip intelligibility of Orpheus Luganda audio: Whisper-SALT CER.

A proxy, not a listening test: each clip from ``scripts/bench_orpheus_tts.py``
is transcribed back with Whisper-SALT forced to Luganda and compared with the
sentence it was asked to say (character error rate, case and punctuation
folded). What it can show is *relative*: whether one speaker, or FP8
quantization, makes the voice harder to understand than another. Whisper-SALT's
own Luganda WER (~14% on read speech) sits under every number here.

    python scripts/orpheus_intelligibility.py \\
        --run fp8=evals/orpheus_tts --run bf16=/path/to/bf16/run

Each ``--run`` is a directory holding ``samples/`` and ``speaker_key.json`` plus
``listening_sheet.csv`` (for the text). Writes
``evals/reports/orpheus_intelligibility_<date>.json``. Run inside app-api:gpu.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", text.lower())).strip()


def cer(ref: str, hyp: str) -> float:
    a, b = _norm(ref), _norm(hyp)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] / len(a)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--run", action="append", required=True, help="name=dir")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "reports")
    args = ap.parse_args()

    from app.speech_service import SpeechModel

    speech = SpeechModel()
    if speech._whisper_salt is None:
        raise SystemExit("Whisper-SALT did not load")

    report: dict[str, object] = {"date": dt.date.today().isoformat(), "runs": {}}
    for spec in args.run:
        name, _, folder = spec.partition("=")
        base = Path(folder)
        key = json.loads((base / "speaker_key.json").read_text())
        rows = list(csv.DictReader((base / "listening_sheet.csv").open(encoding="utf-8")))
        per_speaker: dict[str, list[float]] = {}
        details = []
        for row in rows:
            wav = (base / row["file"]).read_bytes()
            res = speech.transcribe(wav, 24000, "lg")
            score = cer(row["text"], res.text or "")
            spk = key[row["blind_id"]]
            per_speaker.setdefault(spk, []).append(score)
            details.append({"speaker": spk, "text": row["text"], "heard": res.text, "cer": round(score, 3)})
        all_scores = [d["cer"] for d in details]
        report["runs"][name] = {
            "clips": len(details),
            "cer_mean": round(statistics.mean(all_scores), 3),
            "cer_median": round(statistics.median(all_scores), 3),
            "by_speaker": {s: round(statistics.mean(v), 3) for s, v in sorted(per_speaker.items())},
            "details": details,
        }
        print(name, report["runs"][name]["cer_mean"], report["runs"][name]["by_speaker"])
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"orpheus_intelligibility_{report['date']}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print("wrote", path)


if __name__ == "__main__":
    main()
