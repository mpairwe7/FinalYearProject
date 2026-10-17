"""Live runner for the complex-scenario benchmark (Phase A.4).

Sends each scenario's turns sequentially under a fresh ``conversation_id`` to
``POST <base-url>/api/v1/chat`` (falling back to ``/v1/chat`` when targeting the
FastAPI container directly) and scores every turn deterministically via
``evals.complex_scenarios.scoring``.

Held-out integrity rule:
- Every ``--split heldout`` run verifies ``heldout.jsonl`` against
  ``evals/complex_scenarios/HELDOUT.sha256`` and writes ``heldout_sha256`` into
  the output report JSON.
- Any ``--split heldout`` run whose ``--label`` is not ``baseline`` requires
  ``--baseline-report <path>`` and refuses to run unless the current digest of
  ``heldout.jsonl`` matches both ``HELDOUT.sha256`` and the baseline report's
  recorded ``heldout_sha256``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import statistics
import sys
import time
import urllib.parse
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))
sys.path.insert(0, str(ROOT))

from evals.complex_scenarios.scoring import default_tool_runner, score_scenario  # noqa: E402

SCENARIO_DIR = ROOT / "evals" / "complex_scenarios"
REPORTS_DIR = ROOT / "evals" / "reports"
DEFAULT_BASE_URL = "https://struttingly-nongeological-briella.ngrok-free.dev"


def file_sha256(path: Path) -> str:
    """Compute the lowercase hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_sha(digest: str | None) -> str:
    """Normalize a SHA-256 digest string by stripping prefix and hyphens."""
    return (digest or "").strip().removeprefix("sha256:").replace("-", "").lower()


def _format_sha(digest: str) -> str:
    """Format a SHA-256 hex digest with scheme prefix and 8-char groups."""
    raw = _norm_sha(digest)
    grouped = "-".join(raw[i : i + 8] for i in range(0, len(raw), 8))
    return f"sha256:{grouped}"


def verify_heldout_digest(
    heldout_path: Path,
    sha_file: Path,
    label: str,
    baseline_report_path: Path | None,
) -> str:
    """Verify ``heldout.jsonl`` against ``HELDOUT.sha256`` and the baseline report."""
    if not sha_file.exists():
        raise RuntimeError(f"Missing held-out digest file: {sha_file}")
    recorded_txt = _norm_sha(sha_file.read_text(encoding="utf-8").strip().split()[0])
    current_digest = file_sha256(heldout_path)
    if current_digest != recorded_txt:
        raise RuntimeError(
            f"heldout.jsonl sha256 ({current_digest}) does not match "
            f"HELDOUT.sha256 ({recorded_txt}). Held-out evaluation refused."
        )
    if label != "baseline":
        if baseline_report_path is None:
            raise RuntimeError(
                "Running --split heldout with --label != 'baseline' requires "
                "--baseline-report <path> to verify held-out digest continuity."
            )
        if not baseline_report_path.exists():
            raise RuntimeError(f"Baseline report not found: {baseline_report_path}")
        baseline_data = json.loads(baseline_report_path.read_text(encoding="utf-8"))
        baseline_digest = _norm_sha(baseline_data.get("heldout_sha256"))
        if baseline_digest != current_digest:
            raise RuntimeError(
                f"heldout.jsonl sha256 ({current_digest}) does not match baseline "
                f"report heldout_sha256 ({baseline_digest}). Comparison is void."
            )
    return _format_sha(current_digest)


def _validate_base_url(base_url: str) -> str:
    """Ensure ``base_url`` uses http or https scheme and return stripped base URL."""
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"base_url must be an http(s) URL, got {base_url!r}")
    return base_url.strip().rstrip("/")


def _post_chat_turn(
    base_url: str,
    conversation_id: str,
    session_id: str,
    message: str,
    timeout: float = 240.0,
) -> dict[str, Any]:
    """Send a single chat turn over HTTP(S) to ``/api/v1/chat`` or ``/v1/chat``."""
    base = _validate_base_url(base_url)
    body = {
        "message": message,
        "conversation_id": conversation_id,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "ngrok-skip-browser-warning": "1",
        "X-Session-ID": session_id,
        "User-Agent": "ura-complex-scenario-eval/1.0",
    }

    paths = ("/api/v1/chat", "/v1/chat")
    last_err: Exception | None = None
    with httpx.Client(timeout=timeout) as client:
        for idx, path in enumerate(paths):
            url = f"{base}{path}"
            started = time.perf_counter()
            try:
                resp = client.post(url, json=body, headers=headers)
                elapsed = round(time.perf_counter() - started, 3)
                if resp.status_code == 404 and idx == 0:
                    continue
                if resp.status_code >= 400:
                    return {
                        "reply": "",
                        "retrieval_mode": "http_error",
                        "error": f"HTTP {resp.status_code}: {resp.text[:400]}",
                        "latency_s": elapsed,
                    }
                data = resp.json() if resp.content else {}
                data["latency_s"] = elapsed
                return data
            except Exception as exc:
                last_err = exc
                elapsed = round(time.perf_counter() - started, 3)
                return {
                    "reply": "",
                    "retrieval_mode": "request_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_s": elapsed,
                }
    return {
        "reply": "",
        "retrieval_mode": "request_error",
        "error": str(last_err or "Unknown error"),
        "latency_s": 0.0,
    }


def _execute_scenario(base_url: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Execute all turns of ``scenario`` against ``base_url`` and score the replies."""
    conv_id = f"cs-{scenario['id'][:24]}-{uuid.uuid4().hex[:8]}"
    sess_id = f"sess-{uuid.uuid4().hex[:12]}"
    responses: list[dict[str, Any]] = []
    for turn in scenario.get("turns") or []:
        resp = _post_chat_turn(base_url, conv_id, sess_id, str(turn["message"]))
        responses.append(resp)

    scored = score_scenario(scenario, responses, tool_runner=default_tool_runner)
    for idx, t_score in enumerate(scored["turns"]):
        raw_resp = responses[idx] if idx < len(responses) else {}
        t_score["reply_preview"] = str(
            raw_resp.get("reply") or raw_resp.get("answer") or ""
        )[:500]
        t_score["sources_count"] = len(
            raw_resp.get("sources") or raw_resp.get("citations") or []
        )
        if raw_resp.get("debug"):
            t_score["debug"] = raw_resp["debug"]
        if raw_resp.get("error"):
            t_score["http_error"] = raw_resp["error"]
    return scored


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Compute linear-interpolated percentile ``pct`` from a sorted list of floats."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 3)
    return round(sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f]), 3)


def build_report(
    *,
    split: str,
    label: str,
    base_url: str,
    scenarios: list[dict[str, Any]],
    results: list[dict[str, Any]],
    heldout_sha256: str | None,
) -> dict[str, Any]:
    """Aggregate per-scenario scores into a structured benchmark evaluation report."""
    by_head: dict[str, list[float]] = defaultdict(list)
    latencies: list[float] = []
    total_turns = 0
    truncated_turns = 0
    route_ok_turns = 0
    route_checked_turns = 0
    premise_ok_turns = 0
    premise_checked_turns = 0
    fig_hits = 0
    fig_total = 0
    fig_tool_covered_hits = 0
    fig_tool_covered_total = 0
    non_sector_turns = 0
    non_sector_turns_with_sector_guide = 0
    critical_errors: list[dict[str, Any]] = []

    reviewed_count = sum(1 for s in scenarios if s.get("reviewed") is True)

    for scen, res in zip(scenarios, results):
        head = str(res.get("tax_head") or "unknown")
        by_head[head].append(float(res["score_100"]))
        for t_idx, t in enumerate(res.get("turns") or [], 1):
            total_turns += 1
            lat = float(t.get("latency_s") or 0.0)
            if lat > 0:
                latencies.append(lat)
            if not t.get("complete", True):
                truncated_turns += 1
            if t.get("route_ok") is not None:
                route_checked_turns += 1
                if t["route_ok"]:
                    route_ok_turns += 1
            if t.get("premise_ok") is not None:
                premise_checked_turns += 1
                if t["premise_ok"]:
                    premise_ok_turns += 1
            if t.get("sources_relevant") is not None and not scen.get("sector"):
                non_sector_turns += 1
                if t.get("sector_guide_hits"):
                    non_sector_turns_with_sector_guide += 1
            for fdet in t.get("figure_details") or []:
                status = fdet.get("status")
                if status == "fx_unavailable" and not fdet.get("required", True):
                    continue
                fig_total += 1
                if status == "hit":
                    fig_hits += 1
                if status in ("hit", "miss"):
                    fig_tool_covered_total += 1
                    if status == "hit":
                        fig_tool_covered_hits += 1
            if t.get("critical_fail"):
                critical_errors.append(
                    {
                        "scenario_id": res["id"],
                        "tax_head": head,
                        "turn": t_idx,
                        "forbidden_hits": t.get("forbidden_hits", []),
                        "reply_preview": t.get("reply_preview", "")[:240],
                    }
                )

    per_tax_head = {
        head: {
            "count": len(scores),
            "mean_score_100": round(statistics.mean(scores), 2) if scores else 0.0,
            "min_score_100": round(min(scores), 2) if scores else 0.0,
        }
        for head, scores in sorted(by_head.items())
    }

    overall_scores = [float(r["score_100"]) for r in results]
    sorted_lat = sorted(latencies)

    report: dict[str, Any] = {
        "split": split,
        "label": label,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "base_url": base_url,
        "scenario_count": len(scenarios),
        "turn_count": total_turns,
        "reviewed_scenarios": reviewed_count,
        "unreviewed_scenarios": len(scenarios) - reviewed_count,
        "overall_score_100": round(statistics.mean(overall_scores), 2) if overall_scores else 0.0,
        "metrics": {
            "mean_scenario_score_100": (
                round(statistics.mean(overall_scores), 2) if overall_scores else 0.0
            ),
            "critical_forbidden_hits": len(critical_errors),
            "figure_accuracy_all_pct": (
                round(100.0 * fig_hits / fig_total, 2) if fig_total else None
            ),
            "figure_accuracy_tool_covered_pct": (
                round(100.0 * fig_tool_covered_hits / fig_tool_covered_total, 2)
                if fig_tool_covered_total
                else None
            ),
            "premise_challenge_rate_pct": (
                round(100.0 * premise_ok_turns / premise_checked_turns, 2)
                if premise_checked_turns
                else None
            ),
            "route_correctness_pct": (
                round(100.0 * route_ok_turns / route_checked_turns, 2)
                if route_checked_turns
                else None
            ),
            "truncated_turns": truncated_turns,
            "truncation_rate_pct": (
                round(100.0 * truncated_turns / total_turns, 2) if total_turns else 0.0
            ),
            "sector_guide_citation_rate_non_sector_pct": (
                round(100.0 * non_sector_turns_with_sector_guide / non_sector_turns, 2)
                if non_sector_turns
                else 0.0
            ),
            "latency_p50_s": _percentile(sorted_lat, 50.0),
            "latency_p95_s": _percentile(sorted_lat, 95.0),
        },
        "per_tax_head": per_tax_head,
        "critical_errors": critical_errors,
        "scenarios": results,
    }
    if heldout_sha256 is not None:
        report["heldout_sha256"] = heldout_sha256
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the complex tax scenario benchmark runner."""
    parser = argparse.ArgumentParser(description="Run complex tax scenarios against a live API.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL of the API or frontend proxy (default: live ngrok URL).",
    )
    parser.add_argument(
        "--split",
        choices=("dev", "heldout"),
        required=True,
        help="Which scenario split to evaluate.",
    )
    parser.add_argument(
        "--label",
        default="baseline",
        help="Run label (e.g. baseline, final).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output report path (default: evals/reports/complex_<split>_<label>_<date>.json).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Number of scenarios to run concurrently (default: 2).",
    )
    parser.add_argument(
        "--baseline-report",
        default=None,
        help="Path to the committed baseline report (required for post-baseline heldout runs).",
    )
    args = parser.parse_args(argv)

    split_path = SCENARIO_DIR / f"{args.split}.jsonl"
    if not split_path.exists():
        raise SystemExit(f"Scenario file not found: {split_path}")

    heldout_digest: str | None = None
    if args.split == "heldout":
        sha_path = SCENARIO_DIR / "HELDOUT.sha256"
        baseline_path = Path(args.baseline_report) if args.baseline_report else None
        heldout_digest = verify_heldout_digest(
            split_path, sha_path, args.label, baseline_path
        )

    scenarios = [
        json.loads(line)
        for line in split_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    date_str = dt.date.today().isoformat()
    out_path = (
        Path(args.out)
        if args.out
        else REPORTS_DIR / f"complex_{args.split}_{args.label}_{date_str}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results_by_idx: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        future_map = {
            pool.submit(_execute_scenario, args.base_url, scen): idx
            for idx, scen in enumerate(scenarios)
        }
        for fut in concurrent.futures.as_completed(future_map):
            idx = future_map[fut]
            try:
                res = fut.result()
            except Exception as exc:  # keep the run and the report intact
                scen = scenarios[idx]
                res = {
                    "id": scen.get("id"),
                    "tax_head": scen.get("tax_head"),
                    "difficulty": scen.get("difficulty"),
                    "score": 0.0,
                    "score_100": 0.0,
                    "critical_fail": False,
                    "truncated_turns": 0,
                    "turns": [],
                    "runner_error": f"{type(exc).__name__}: {exc}",
                }
            results_by_idx[idx] = res
            print(
                f"[{len(results_by_idx)}/{len(scenarios)}] {res['id']} "
                f"({res['tax_head']}): {res['score_100']:.1f}/100",
                flush=True,
            )

    ordered_results = [results_by_idx[i] for i in range(len(scenarios))]
    report = build_report(
        split=args.split,
        label=args.label,
        base_url=args.base_url,
        scenarios=scenarios,
        results=ordered_results,
        heldout_sha256=heldout_digest,
    )
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    m = report["metrics"]
    print("\n=== Complex Scenario Evaluation Summary ===")
    print(f"Split: {args.split} | Label: {args.label} | Scenarios: {len(scenarios)} | Turns: {report['turn_count']}")
    print(f"Mean scenario score: {report['overall_score_100']:.2f} / 100")
    print(f"Route correctness:   {m['route_correctness_pct']}%")
    print(f"Premise challenge:   {m['premise_challenge_rate_pct']}%")
    print(f"Figures (covered):   {m['figure_accuracy_tool_covered_pct']}% (all: {m['figure_accuracy_all_pct']}%)")
    print(f"Critical forbidden:  {m['critical_forbidden_hits']}")
    print(f"Truncated turns:     {m['truncated_turns']} ({m['truncation_rate_pct']}%)")
    print(f"Sector guide cites:  {m['sector_guide_citation_rate_non_sector_pct']}%")
    print(f"Latency p50 / p95:   {m['latency_p50_s']}s / {m['latency_p95_s']}s")
    print(f"Saved report to:     {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
