"""Phase 0A — can the receptionist tell English, Luganda and Swahili apart by ear?

Scores every clip in ``evals/language_id/manifest.jsonl`` (built by
``evals/language_id/build_dataset.py``) with up to five methods and writes
``evals/reports/language_id_<date>.json`` plus a markdown summary next to it.

| id | method                                                                 |
|----|------------------------------------------------------------------------|
| A  | ``SpeechModel.identify_language`` — SALT language token, one decoder step |
| B  | unforced SALT decode (no prompt) → ``query.detect_language`` on the text |
| C  | A, overruled by the text when unsure (``receptionist.language.fuse``)   |
| D  | ``facebook/mms-lid-126`` restricted to eng/lug/swh (CC-BY-NC, demo only)|
| E  | decode forced as each language, keep the best mean token log-prob       |
| R  | the runtime decision: A, the sentinel's forced-language decode when the |
|    | utterance is short or unsure, then a fresh ``LanguagePolicy``'s verdict |
|    | on a call's first content utterance ("none" leaves the call in English) |

A is the method the runtime uses, so it is called through the production
code path (breaker, executor, deadline included) rather than re-implemented.

Run inside the ``app-api:gpu`` image on a free GPU — see
``evals/language_id/README.md`` for the exact command.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))

logger = logging.getLogger("lid.eval")

LANGS = ("en", "sw", "lg")
MMS_CODES = {"en": "eng", "sw": "swh", "lg": "lug"}


def _read_pcm16(path: Path) -> bytes:
    import soundfile as sf

    samples, sr = sf.read(path, dtype="int16")
    if sr != 16000:
        raise ValueError(f"{path}: expected 16 kHz, got {sr}")
    return np.asarray(samples, dtype=np.int16).tobytes()


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    return round(float(np.percentile(values, q)), 1)


class Methods:
    def __init__(self, speech: Any, want: set[str]) -> None:
        self.speech = speech
        self.want = want
        self._mms: tuple[Any, Any, dict[str, int]] | None = None
        if "D" in want:
            self._load_mms()

    # -- A --------------------------------------------------------------
    def salt_token(self, pcm: bytes) -> dict[str, Any]:
        res = self.speech.identify_language(pcm, 16000, LANGS)
        return {"top": res.top, "probs": res.probs, "latency_ms": res.latency_ms, "error": res.error}

    # -- B --------------------------------------------------------------
    def _decode(self, pcm: bytes, language: str | None) -> tuple[str, float, float]:
        """Plain SALT decode, no domain prompt. Returns (text, mean_logprob, ms)."""
        import torch
        from app.speech_service import SALT_LANGUAGE_TOKEN_IDS

        model, processor = self.speech._whisper_salt
        t0 = time.perf_counter()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        feats = processor.feature_extractor(samples, sampling_rate=16000, return_tensors="pt").input_features
        feats = feats.to(model.device, dtype=model.dtype)
        forced = None
        if language is not None:
            forced = processor.tokenizer.decode([SALT_LANGUAGE_TOKEN_IDS[language]])
        with torch.no_grad():
            # Whisper speech recognition: audio features in, a transcript out —
            # no instructions are followed, as in speech_service's decodes.
            # nosemgrep: ura-llm01-raw-user-input-to-llm
            out = model.generate(
                feats, language=forced, max_new_tokens=160,
                return_dict_in_generate=True, output_scores=True,
            )
            scores = model.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
        special = set(processor.tokenizer.all_special_ids)
        gen = out.sequences[0, -len(out.scores):].tolist()
        lps = [float(lp) for tid, lp in zip(gen, scores[0].tolist()) if tid not in special]
        text = processor.batch_decode(out.sequences, skip_special_tokens=True)[0].strip()
        mean_lp = sum(lps) / len(lps) if lps else float("-inf")
        return text, mean_lp, (time.perf_counter() - t0) * 1000

    def text_lid(self, pcm: bytes) -> dict[str, Any]:
        from app.query import detect_language

        text, _, ms = self._decode(pcm, None)
        top = detect_language(text, default_lang="en") if text else "en"
        return {"top": top if top in LANGS else f"other:{top}", "text": text, "latency_ms": round(ms, 1)}

    # -- C --------------------------------------------------------------
    @staticmethod
    def fusion(a: dict[str, Any], b: dict[str, Any], duration_s: float) -> dict[str, Any]:
        from app.receptionist.language import LanguageVote, fuse

        if not a["top"]:
            return {"top": "", "probs": {}}
        vote = fuse(LanguageVote(a["probs"], a["top"], duration_s, b.get("text", "")))
        return {"top": vote.top, "probs": vote.probs}

    # -- D --------------------------------------------------------------
    def _load_mms(self) -> None:
        import torch
        from transformers import AutoFeatureExtractor, Wav2Vec2ForSequenceClassification

        repo = "facebook/mms-lid-126"
        extractor = AutoFeatureExtractor.from_pretrained(repo)
        model = Wav2Vec2ForSequenceClassification.from_pretrained(repo, torch_dtype=torch.float16).to("cuda:0").eval()
        # This checkpoint's config carries id2label only.
        label2id = {code: int(idx) for idx, code in model.config.id2label.items()}
        ids = {lang: label2id[code] for lang, code in MMS_CODES.items()}
        self._mms = (extractor, model, ids)

    def mms(self, pcm: bytes) -> dict[str, Any]:
        import torch

        assert self._mms is not None
        extractor, model, ids = self._mms
        t0 = time.perf_counter()
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        inputs = extractor(samples, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = model(inputs.input_values.to("cuda:0", dtype=torch.float16)).logits[0].float()
        picked = torch.softmax(logits[[ids[lang] for lang in LANGS]], dim=-1).tolist()
        probs = {lang: round(p, 4) for lang, p in zip(LANGS, picked)}
        return {"top": max(probs, key=probs.__getitem__), "probs": probs,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

    # -- R --------------------------------------------------------------
    def runtime(self, pcm: bytes, a: dict[str, Any], duration_s: float) -> dict[str, Any]:
        """What a call does with this utterance, via the production modules.

        Mirrors ``LanguageSentinel._build_vote`` (decode the text in the most
        likely language when the utterance is short or the vote unsure) and
        asks a fresh policy — call just opened in English — what happens.
        """
        from app.receptionist.language import LanguagePolicy, LanguageVote, PolicyConfig, fuse
        from app.receptionist.sentinel import TEXT_DECODE_MAX_S
        from app.speech_service import pcm16_to_wav

        config = PolicyConfig.from_env()
        t0 = time.perf_counter()
        probs, top = a.get("probs") or {}, a.get("top") or ""
        text = ""
        if duration_s <= TEXT_DECODE_MAX_S or probs.get(top, 0.0) < config.switch_confidence:
            res = self.speech.transcribe(pcm16_to_wav(pcm), 16000, top or "en", False)
            text = (res.text or "").strip()
        vote = LanguageVote(probs, top, duration_s, text)
        if os.getenv("RECEPTIONIST_LID_METHOD", "salt_token") == "fusion":
            vote = fuse(vote)
        decision = LanguagePolicy(active="en", config=config).observe(vote)
        language = decision.target if decision.action in ("lock", "switch") else "en"
        return {"top": language, "decision": decision.action, "reason": decision.reason, "text": text,
                "probs": {language: decision.confidence} if decision.action != "none" else {},
                "latency_ms": round(a.get("latency_ms", 0.0) + (time.perf_counter() - t0) * 1000, 1)}

    # -- E --------------------------------------------------------------
    def dual_decode(self, pcm: bytes) -> dict[str, Any]:
        t0 = time.perf_counter()
        lps = {lang: self._decode(pcm, lang)[1] for lang in LANGS}
        return {"top": max(lps, key=lps.__getitem__), "mean_logprob": {k: round(v, 3) for k, v in lps.items()},
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _summarise(rows: list[dict[str, Any]], method: str, policy: dict[str, float]) -> dict[str, Any]:
    scored = [r for r in rows if method in r["pred"]]
    if not scored:
        return {}

    def acc(subset: list[dict[str, Any]]) -> float | None:
        if not subset:
            return None
        return round(sum(r["pred"][method]["top"] == r["expected"] for r in subset) / len(subset), 4)

    conf: dict[str, Counter[str]] = defaultdict(Counter)
    for r in scored:
        if r["duration_s"] >= 1.5:
            conf[r["expected"]][r["pred"][method]["top"] or "none"] += 1

    per_class_ge15 = {lang: acc([r for r in scored if r["expected"] == lang and r["duration_s"] >= 1.5]) for lang in LANGS}
    per_class_ge3 = {lang: acc([r for r in scored if r["expected"] == lang and r["duration_s"] >= 3.0]) for lang in LANGS}
    buckets = {
        f"{b}/{ch}": acc([r for r in scored if r["bucket"] == b and r["channel"] == ch])
        for b in ("<1.5s", "1.5-4s", ">4s") for ch in ("16k", "8k")
    }
    per_source = {src: acc([r for r in scored if r["source"] == src and r["duration_s"] >= 1.5])
                  for src in sorted({r["source"] for r in scored})}

    en_long = [r for r in scored if r["expected"] == "en" and r["duration_s"] >= 1.5]
    raw_en_lg = sum(r["pred"][method]["top"] == "lg" for r in en_long)
    # The policy only moves an unlocked call off English on a vote at or above
    # the accumulate threshold — that gated rate is what a caller would feel.
    gated_en_lg = sum(
        r["pred"][method]["top"] == "lg"
        and r["pred"][method].get("probs", {}).get("lg", 1.0) >= policy["hysteresis_confidence"]
        for r in en_long
    )
    latencies = [r["pred"][method]["latency_ms"] for r in scored if r["pred"][method].get("latency_ms") is not None]
    mixed = [r for r in scored if r["label"].startswith("mixed")]
    return {
        "n": len(scored),
        "accuracy_ge_1_5s": acc([r for r in scored if r["duration_s"] >= 1.5]),
        "accuracy_ge_3s": acc([r for r in scored if r["duration_s"] >= 3.0]),
        "per_class_ge_1_5s": per_class_ge15,
        "per_class_ge_3s": per_class_ge3,
        "per_bucket_channel": buckets,
        "per_source_ge_1_5s": per_source,
        "confusion_ge_1_5s": {k: dict(v) for k, v in conf.items()},
        "en_to_lg_false_switch_raw": round(raw_en_lg / len(en_long), 4) if en_long else None,
        "en_to_lg_false_switch_policy_gated": round(gated_en_lg / len(en_long), 4) if en_long else None,
        "mixed_to_lg": acc([r for r in mixed if r["duration_s"] >= 1.5]),
        "latency_ms_p50": _pct(latencies, 50),
        "latency_ms_p95": _pct(latencies, 95),
    }


def _gate(summary: dict[str, Any]) -> str:
    """The plan's go/no-go table, applied mechanically to one method."""
    if not summary:
        return "not_run"

    def meets(per_class: dict[str, float | None]) -> bool:
        return all(v is not None and v >= 0.95 for v in per_class.values())

    fs = summary["en_to_lg_false_switch_policy_gated"] or 0.0
    mixed_ok = summary["mixed_to_lg"] is None or summary["mixed_to_lg"] >= 0.85
    fast = summary["latency_ms_p95"] <= 150
    if meets(summary["per_class_ge_1_5s"]) and mixed_ok and fs <= 0.03 and fast:
        return "GO"
    if meets(summary["per_class_ge_3s"]) and mixed_ok and fs <= 0.03 and fast:
        return "PARTIAL"
    return "FAIL"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--manifest", type=Path, default=ROOT / "evals" / "language_id" / "manifest.jsonl")
    ap.add_argument("--data", type=Path, default=ROOT / "evals" / "language_id" / "data")
    ap.add_argument("--methods", default="A,B,C,E", help="comma list of A-E, R (D downloads a 1B-param model)")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N clips (smoke run)")
    ap.add_argument("--out", type=Path, default=ROOT / "evals" / "reports")
    ap.add_argument("--suffix", default="", help="appended to the report name, e.g. _mixed")
    args = ap.parse_args()
    want = {m.strip().upper() for m in args.methods.split(",") if m.strip()}

    from app.receptionist.language import PolicyConfig
    from app.speech_service import SpeechModel

    speech = SpeechModel()
    if speech._whisper_salt is None:
        raise SystemExit("Whisper-SALT did not load — check WHISPER_SALT_DEVICE / HF access")
    speech.identify_language(b"\x00\x00" * 16000)  # warm the encoder before timing anything
    methods = Methods(speech, want)
    policy = PolicyConfig.from_env()

    rows = [json.loads(line) for line in args.manifest.open(encoding="utf-8")]
    if args.limit:
        rows = rows[: args.limit]
    t_start = time.time()
    for i, row in enumerate(rows):
        pcm = _read_pcm16(args.data / row["path"])
        row["expected"] = "lg" if row["label"].startswith("mixed") else row["label"]
        pred: dict[str, dict[str, Any]] = {}
        if want & {"A", "C", "R"}:
            pred["A"] = methods.salt_token(pcm)
        if want & {"B", "C"}:
            pred["B"] = methods.text_lid(pcm)
        if "C" in want:
            pred["C"] = methods.fusion(pred["A"], pred["B"], row["duration_s"])
            pred["C"]["latency_ms"] = pred["A"]["latency_ms"]
        if "D" in want:
            pred["D"] = methods.mms(pcm)
        if "R" in want:
            pred["R"] = methods.runtime(pcm, pred["A"], row["duration_s"])
        if "E" in want:
            pred["E"] = methods.dual_decode(pcm)
        row["pred"] = pred
        if (i + 1) % 100 == 0:
            logger.info("%d/%d clips (%.0fs)", i + 1, len(rows), time.time() - t_start)

    summaries = {m: _summarise(rows, m, {"hysteresis_confidence": policy.hysteresis_confidence})
                 for m in sorted(want)}
    b_latency = [r["pred"]["B"]["latency_ms"] for r in rows if "B" in r["pred"]]
    notes: dict[str, str] = {}
    if "C" in want and b_latency:
        notes["C_latency"] = ("C is reported at A's latency: the runtime only decodes text when A is unsure; "
                              f"that decode costs B's latency (p50 {_pct(b_latency, 50)} ms, "
                              f"p95 {_pct(b_latency, 95)} ms).")
    report = {
        "date": dt.date.today().isoformat(),
        "manifest": str(args.manifest.resolve().relative_to(ROOT)),
        "clips": len(rows),
        "policy": {"min_speech_s": policy.min_speech_s, "switch_confidence": policy.switch_confidence,
                   "hysteresis_confidence": policy.hysteresis_confidence},
        "notes": notes,
        "methods": summaries,
        "gate": {m: _gate(s) for m, s in summaries.items()},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = report["date"] + args.suffix
    json_path = args.out / f"language_id_{stamp}.json"
    json_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    # Every clip's prediction: ~1 MB per run, so beside the report and git-ignored.
    clips = [{k: r[k] for k in ("path", "label", "source", "channel", "variant", "duration_s", "pred")} for r in rows]
    (args.out / f"language_id_{stamp}_clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
    (args.out / f"language_id_{stamp}.md").write_text(_markdown(report), encoding="utf-8")
    logger.info("wrote %s", json_path)
    for m, s in summaries.items():
        logger.info("%s: gate=%s acc>=1.5s=%s per_class=%s en->lg=%s p95=%sms", m, report["gate"][m],
                    s.get("accuracy_ge_1_5s"), s.get("per_class_ge_1_5s"),
                    s.get("en_to_lg_false_switch_policy_gated"), s.get("latency_ms_p95"))


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Language identification — {report['date']}",
        "",
        f"{report['clips']} clips from `{report['manifest']}`. Generated by `scripts/eval_language_id.py`.",
        "",
        "| method | gate | acc ≥1.5 s | en | sw | lg | acc ≥3 s | en→lg (gated) | mixed→lg | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for m, s in report["methods"].items():
        if not s:
            continue
        pc = s["per_class_ge_1_5s"]
        lines.append(
            f"| {m} | {report['gate'][m]} | {s['accuracy_ge_1_5s']} | {pc['en']} | {pc['sw']} | {pc['lg']} | "
            f"{s['accuracy_ge_3s']} | {s['en_to_lg_false_switch_policy_gated']} | {s['mixed_to_lg']} | "
            f"{s['latency_ms_p50']} | {s['latency_ms_p95']} |"
        )
    lines += ["", "## Accuracy by duration bucket and channel", ""]
    for m, s in report["methods"].items():
        if s:
            lines.append(f"- **{m}**: " + ", ".join(f"{k} {v}" for k, v in s["per_bucket_channel"].items()))
    lines += ["", "## Per source (clips ≥ 1.5 s)", ""]
    for m, s in report["methods"].items():
        if s:
            lines.append(f"- **{m}**: " + ", ".join(f"{k} {v}" for k, v in s["per_source_ge_1_5s"].items()))
    lines += ["", "## Confusion (clips ≥ 1.5 s; rows = truth)", ""]
    for m, s in report["methods"].items():
        if s:
            lines.append(f"- **{m}**: `{json.dumps(s['confusion_ge_1_5s'])}`")
    if report["notes"]:
        lines += ["", "## Notes", ""] + [f"- {v}" for v in report["notes"].values()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
