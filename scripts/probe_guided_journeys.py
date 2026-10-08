#!/usr/bin/env python3
"""Guided-journey probes: does each task prompt land where it should?

Sends a fixed set of taxpayer prompts to a running URA assistant and checks
where each one lands: the guided flow it should start, the step-by-step offer
an answer should carry, the officer handoff, the crisis reply, the reply
language. Every case records its latency. The exit status is non-zero when any
expectation fails, so the script doubles as a canary after a deploy.

Procedure, pitfalls and the 2026-09-29 baseline:
docs/runbooks/guided-journey-probes.md

Usage::

    python3 scripts/probe_guided_journeys.py \\
        --base https://<ngrok-domain>/api \\
        --out docs/Reports/data/guided_journey_probes_<date>.json

``--base`` is the API root: ``https://<tunnel>/api`` through the web app's
proxy, ``http://localhost:8083`` for the local api container, or
``https://<space>.hf.space`` for the Hugging Face Space (no ``/api``).

Side effect: the escalation cases open real tickets in that deployment's
officer queue. Run it against test stacks, not production.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

STRESS_OPENER = "I understand this can feel stressful"


@dataclass
class Case:
    """One conversation: the turns sent, and what the last reply must show."""

    name: str
    turns: list[str]
    workflow: str | None = None  # flow the last turn must start
    offer: str | None = None  # next action the last reply must carry
    retrieval_mode: str | None = None
    escalated: bool | None = None
    locale: str | None = None
    officer: bool | None = None  # an officer offered ("Talk to an officer") or already brought in
    reply_contains: list[str] = field(default_factory=list)
    reply_lacks: list[str] = field(default_factory=list)


CASES: list[Case] = [
    Case("how-to return answered with guide offer", ["How do I file my return?"],
         escalated=False, offer="Guide me step by step through Return Filing"),
    Case("help-me return starts flow, no stress opener", ["Help me file my return"],
         workflow="Return Filing", reply_lacks=[STRESS_OPENER]),
    Case("VAT walkthrough starts return flow", ["Walk me through filing my VAT return"],
         workflow="Return Filing"),
    Case("help-me TIN starts flow, no stress opener", ["Help me register for a TIN"],
         workflow="TIN Registration Guide", reply_lacks=[STRESS_OPENER]),
    Case("PRN guide starts payment flow", ["Guide me to generate a PRN"], workflow="Payment Assistance"),
    Case("customs guide starts flow", ["Guide me through customs clearance"], workflow="Customs Clearance"),
    Case("objection help starts flow", ["Help me file an objection"], workflow="Objection or Dispute"),
    Case("PAYE calculator", ["How much PAYE will I pay?"], workflow="PAYE Calculator"),
    Case("TCC need answered with guide offer", ["I need a tax clearance certificate"],
         offer="Guide me step by step through Tax Clearance Certificate"),
    Case("TCC guide starts journey", ["Guide me through getting a tax clearance certificate"],
         workflow="Tax Clearance Certificate"),
    Case("vehicle how-to answered with guide offer", ["How do I register my motor vehicle?"],
         offer="Guide me step by step through Motor Vehicle Registration"),
    Case("imported car guide starts journey", ["Guide me through registering my imported car"],
         workflow="Motor Vehicle Registration"),
    Case("expired PRN answered, not escalated", ["My PRN has expired, what do I do?"], escalated=False),
    Case("explicit human request escalates", ["I want to talk to a person"], escalated=True),
    Case("account-state question escalates", ["What is my balance?"], escalated=True),
    Case("crisis gets crisis lines", ["I can't pay this tax, I want to kill myself"],
         retrieval_mode="crisis_support", reply_contains=["999", "0800 21 21 21"]),
    Case("feeling-only turn gets a clarifying question", ["This is useless"],
         retrieval_mode="clarification", offer="File a return"),
    Case("contraction still reads as a feeling", ["I'm confused"], retrieval_mode="clarification"),
    Case("a past event does not start a flow", ["I filed my return yesterday but the portal shows an error"],
         reply_lacks=["I can guide you through the Return Filing process"]),
    Case("nil return starts return filing", ["Help me submit the nil return for my company"],
         workflow="Return Filing"),
    Case("asking for another flow inside one switches to it",
         ["Guide me through getting a tax clearance certificate", "help me file my return"],
         workflow="Return Filing"),
    Case("account-locked plea still escalates", ["Please help me, my account is locked"], escalated=True),
    Case("sustained frustration brings in an officer",
         ["This is useless", "It still does not work", "It still does not work"],
         officer=True),
    Case("Luganda TIN question answered in Luganda", ["Nnina okwewandiisa otya okufuna TIN mu Uganda?"],
         locale="lg", reply_lacks=["Happy to help"]),
    Case("Kiswahili TIN question answered in Kiswahili",
         ["Nitasajili vipi ili kupata namba ya TIN nchini Uganda?"],
         locale="sw", reply_lacks=["Happy to help"]),
]


def _post(base: str, message: str, conversation_id: str, timeout: float) -> dict[str, Any]:
    parsed_base = urllib.parse.urlsplit(base)
    if (
        parsed_base.scheme not in ("http", "https")
        or not parsed_base.netloc
        or parsed_base.username is not None
        or parsed_base.password is not None
    ):
        raise ValueError("--base must be an http(s) URL without embedded credentials")
    response = httpx.post(
        f"{base.rstrip('/')}/v1/chat",
        json={"message": message, "conversation_id": conversation_id},
        headers={"ngrok-skip-browser-warning": "1"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _check(case: Case, body: dict[str, Any]) -> list[str]:
    """Every unmet expectation, in words."""
    misses: list[str] = []
    workflow = (body.get("workflow") or {}).get("name")
    actions = [str(a) for a in body.get("next_actions") or []]
    reply = str(body.get("reply") or "")
    if case.workflow is not None and workflow != case.workflow:
        misses.append(f"workflow {workflow!r} != {case.workflow!r}")
    if case.offer is not None and case.offer not in actions:
        misses.append(f"no next action {case.offer!r}")
    if case.retrieval_mode is not None and body.get("retrieval_mode") != case.retrieval_mode:
        misses.append(f"retrieval_mode {body.get('retrieval_mode')!r} != {case.retrieval_mode!r}")
    if case.escalated is not None and bool(body.get("escalation_required")) != case.escalated:
        misses.append(f"escalation_required {bool(body.get('escalation_required'))} != {case.escalated}")
    if case.officer and not ("Talk to an officer" in actions or body.get("escalation_required")):
        misses.append("no officer offered and not escalated")
    if case.locale is not None and body.get("locale") != case.locale:
        misses.append(f"locale {body.get('locale')!r} != {case.locale!r}")
    misses += [f"reply lacks {text!r}" for text in case.reply_contains if text not in reply]
    misses += [f"reply contains {text!r}" for text in case.reply_lacks if text in reply]
    return misses


def run(base: str, timeout: float) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in CASES:
        conversation_id = f"probe-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        try:
            body: dict[str, Any] = {}
            for turn in case.turns:
                body = _post(base, turn, conversation_id, timeout)
            misses = _check(case, body)
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            body, misses = {}, [f"{type(exc).__name__}: {str(exc)[:120]}"]
        results.append(
            {
                **asdict(case),
                "passed": not misses,
                "misses": misses,
                "seconds": round(time.monotonic() - started, 1),
                "observed": {
                    "retrieval_mode": body.get("retrieval_mode"),
                    "workflow": (body.get("workflow") or {}).get("name"),
                    "escalation_required": body.get("escalation_required"),
                    "locale": body.get("locale"),
                    "next_actions": body.get("next_actions"),
                    "reply_start": str(body.get("reply") or "")[:160],
                },
            }
        )
        mark = "PASS" if not misses else "FAIL"
        print(f"{mark} {results[-1]['seconds']:>6.1f}s  {case.name}" + (f"  -- {'; '.join(misses)}" if misses else ""))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="API root, e.g. https://<tunnel>/api")
    parser.add_argument("--out", type=Path, help="write the JSON record here")
    parser.add_argument("--timeout", type=float, default=180.0, help="seconds per request")
    args = parser.parse_args()

    results = run(args.base, args.timeout)
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} cases passed against {args.base}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"base": args.base, "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": results},
                       indent=1, ensure_ascii=False)
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
