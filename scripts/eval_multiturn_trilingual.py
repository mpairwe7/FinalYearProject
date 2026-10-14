"""Trilingual multi-turn measurement against a running API (G123).

Replays ``evals/multiturn_trilingual/scenarios.jsonl`` through
``POST /v1/chat/stream`` the way the web client talks to it. Each scenario
gets one conversation and one chat session. The client sends its current
language as a hint, or as the taxpayer's choice once they typed a request for
one or picked one. For every turn the report records:

* the answer language and the reason the server gives (``locale``,
  ``locale_source``) against the scenario's expectation;
* the language the reply is actually written in, by the word lists the call
  receptionist votes with (``app.receptionist.language.lexical_hits``). That
  is how an English template served for a Luganda turn shows up;
* the route (``retrieval_mode``), and the scenario's expected route where it
  names one (a refused injection is ``blocked``).

The deterministic counterpart, with no model, is
``App/backend/tests/test_multiturn_trilingual_eval.py``. This one measures
the deployed stack, so the reply-language column depends on Sunflower and the
translation tiers. It calls only the API it is pointed at. Run it inside
``app-api:gpu`` with ``--network host`` against the GPU stack:

    docker run --rm --network host -v "$PWD:/src" -w /src --entrypoint python \\
        app-api:gpu scripts/eval_multiturn_trilingual.py --base http://127.0.0.1:8083

Writes ``evals/reports/multiturn_trilingual_<date>.json``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))
from app.receptionist.language import lexical_hits  # noqa: E402

SCENARIOS = ROOT / "evals" / "multiturn_trilingual" / "scenarios.jsonl"
REPORTS = ROOT / "evals" / "reports"
EXPLICIT_SOURCES = {"explicit_request", "client_explicit"}


def reply_language(text: str) -> str:
    """The language most of the reply's known words belong to ("" if none)."""
    hits = lexical_hits(text or "")
    best = max(("en", "lg", "sw"), key=lambda lang: hits.get(lang, 0))
    return best if hits.get(best, 0) else ""


def stream_turn(client: httpx.Client, base: str, session: str, body: dict[str, Any]) -> dict[str, Any]:
    """One SSE turn: the reply text and the metadata event."""
    reply, metadata, event, data = "", {}, "message", []
    started = time.perf_counter()
    headers = {"X-Session-ID": session, "Accept": "text/event-stream"}
    with client.stream("POST", f"{base}/v1/chat/stream", json=body, headers=headers, timeout=300) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            line = raw.rstrip("\r")
            if line == "":
                payload = "\n".join(data)
                if event == "token":
                    reply += payload
                elif event == "revision":
                    reply = payload
                elif event == "metadata":
                    try:
                        metadata.update(json.loads(payload))
                    except json.JSONDecodeError:
                        pass
                event, data = "message", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
    return {"reply": reply, "metadata": metadata, "seconds": round(time.perf_counter() - started, 2)}


def run_scenario(client: httpx.Client, base: str, scenario: dict[str, Any]) -> list[dict[str, Any]]:
    if scenario.get("profile_locale"):
        # The profile fallback needs a signed-in taxpayer with personalization
        # consent and a saved language; the gate covers it.
        return [{"scenario": scenario["id"], "skipped": "needs a signed-in profile"}]
    session, conversation = f"eval-{uuid.uuid4().hex[:16]}", uuid.uuid4().hex
    start = scenario.get("client") or {}
    locale, explicit = str(start.get("locale") or "en"), bool(start.get("explicit"))
    rows = []
    for index, turn in enumerate(scenario["turns"], 1):
        expect = turn["expect"]
        body = {"message": turn["message"], "conversation_id": conversation, "locale": locale, "locale_explicit": explicit}
        try:
            out = stream_turn(client, base, session, body)
        except httpx.HTTPError as exc:
            rows.append({"scenario": scenario["id"], "turn": index, "error": type(exc).__name__})
            continue
        meta = out["metadata"]
        got_locale, got_source = str(meta.get("locale") or ""), str(meta.get("locale_source") or "")
        written_in = reply_language(out["reply"])
        row = {
            "scenario": scenario["id"],
            "turn": index,
            "expected": [expect["locale"], expect["source"]],
            "got": [got_locale, got_source],
            "decision_ok": [got_locale, got_source] == [expect["locale"], expect["source"]],
            "reply_language": written_in,
            "reply_language_ok": written_in in ("", got_locale),
            "retrieval_mode": meta.get("retrieval_mode"),
            "seconds": out["seconds"],
        }
        if not row["reply_language_ok"]:
            row["reply_hits"] = dict(lexical_hits(out["reply"]))
            row["reply_preview"] = out["reply"][:300]
        expected_mode = (expect.get("live") or {}).get("retrieval_mode")
        if expected_mode:
            row["route_ok"] = meta.get("retrieval_mode") == expected_mode
        rows.append(row)
        # The web client keeps a typed or picked language as the taxpayer's
        # choice, and any other answer language as a hint.
        if got_source == "explicit_request":
            locale, explicit = got_locale, True
        elif not explicit and got_locale:
            locale = got_locale
    return rows


def summarise(rows: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    languages = {s["id"]: s["languages"] for s in scenarios}
    per: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if "skipped" in row:
            per["skipped"]["scenarios"] += 1
            continue
        if "error" in row:
            per["errors"]["turns"] += 1
            continue
        for lang in set(languages[row["scenario"]]) | {"all"}:
            stats = per[lang]
            stats["turns"] += 1
            stats["decision_ok"] += int(row["decision_ok"])
            stats["reply_language_ok"] += int(row["reply_language_ok"])
            if "route_ok" in row:
                stats["routes_checked"] += 1
                stats["route_ok"] += int(row["route_ok"])
    return {lang: dict(stats) for lang, stats in per.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8083", help="API base URL")
    ap.add_argument("--only", default="", help="comma-separated substrings of scenario ids")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any answer-language decision differs")
    args = ap.parse_args()

    scenarios = [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        scenarios = [s for s in scenarios if any(w in s["id"] for w in wanted)]

    rows: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for scenario in scenarios:
            rows.extend(run_scenario(client, args.base.rstrip("/"), scenario))

    summary = summarise(rows, scenarios)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"multiturn_trilingual_{dt.date.today().isoformat()}.json"
    out.write_text(json.dumps({"base": args.base, "summary": summary, "turns": rows}, indent=2) + "\n")

    for row in rows:
        if "skipped" in row:
            print(f"{row['scenario']:<48} -  skipped: {row['skipped']}")
            continue
        if "error" in row:
            print(f"{row['scenario']:<48} {row['turn']}  ERROR {row['error']}")
            continue
        flags = "".join("." if ok else "x" for ok in (row["decision_ok"], row["reply_language_ok"], row.get("route_ok", True)))
        print(f"{row['scenario']:<48} {row['turn']}  {flags}  expected {'/'.join(row['expected']):<26} got {'/'.join(row['got']):<26} "
              f"reply in {row['reply_language'] or '-':<3} {row['retrieval_mode']}")
    for lang, stats in sorted(summary.items()):
        if lang in ("skipped", "errors"):
            print(f"[{lang}] {stats}")
            continue
        turns = stats.get("turns", 0) or 1
        print(f"[{lang}] decisions {stats.get('decision_ok', 0)}/{stats.get('turns', 0)} "
              f"({stats.get('decision_ok', 0) / turns:.0%}), reply language {stats.get('reply_language_ok', 0)}/{stats.get('turns', 0)}")
    print(f"report: {out.relative_to(ROOT)}")
    all_ok = all(row.get("decision_ok") for row in rows if "skipped" not in row)
    return 1 if args.strict and not all_ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
