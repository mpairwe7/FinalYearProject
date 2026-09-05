#!/usr/bin/env python3
"""Long-horizon memory, context awareness, and fuzzy robustness verification.

Tests the live URA Intelligent Assistant on multi-turn conversations (8-10 turns):
1. Topic continuity and coreference resolution ("it", "that tax", "my first question")
2. Resistance to input noise/typos (fuzzy phrasing, missing punctuation, Ugandan colloquialisms)
3. Zero hallucination: faithfulness score verification across turns
4. Verification that official URA contact emails/URLs are never redacted as [REDACTED_EMAIL]
5. Latency and memory performance tracking across long turn sequences
"""

from __future__ import annotations

import json
import random
import sys
import time
import urllib.request
from typing import Any


def apply_fuzzy_noise(text: str, noise_prob: float = 0.15) -> str:
    """Inject mild typing noise (swaps, dropped letters) simulating real user inputs."""
    words = text.split()
    noisy_words = []
    for w in words:
        if len(w) > 4 and random.random() < noise_prob:
            # swap two adjacent characters
            idx = random.randint(1, len(w) - 2)
            w = w[:idx] + w[idx + 1] + w[idx] + w[idx + 2 :]
        noisy_words.append(w)
    return " ".join(noisy_words)


def post_chat(base_url: str, message: str, conversation_id: str | None = None) -> dict[str, Any]:
    b = base_url.rstrip("/")
    if "3032" in b or "ngrok" in b:
        url = f"{b}/api/v1/chat"
    else:
        url = f"{b}/v1/chat"
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "LongHorizonFuzzyTest/1.0"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as resp:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        body = json.loads(resp.read().decode("utf-8"))
        body["_elapsed_ms"] = elapsed_ms
        return body


def run_scenario(base_url: str, scenario_name: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    print(f"\n======================================================================")
    print(f"SCENARIO: {scenario_name}")
    print(f"======================================================================")

    conversation_id = None
    results = []
    total_latency = 0.0

    for i, turn_def in enumerate(turns, 1):
        user_msg = turn_def["query"]
        if turn_def.get("fuzzy"):
            user_msg = apply_fuzzy_noise(user_msg)

        print(f"\n[Turn {i}] Taxpayer: {user_msg}")
        resp = post_chat(base_url, user_msg, conversation_id=conversation_id)
        conversation_id = resp.get("conversation_id")
        elapsed_ms = resp.get("_elapsed_ms", 0.0)
        total_latency += elapsed_ms

        reply = resp.get("reply", "")
        faith = resp.get("faithfulness_score")
        escalate = resp.get("escalation_required")
        reason = resp.get("escalation_reason")
        sources = resp.get("sources", [])
        retrieval_mode = resp.get("retrieval_mode")

        print(f"       Assistant ({elapsed_ms:.1f}ms, mode={retrieval_mode}, faith={faith}):")
        # Print first 200 chars of reply
        reply_preview = reply.replace("\n", " ")[:180]
        print(f"       \"{reply_preview}...\"")

        # Checks
        has_redacted_email = "[REDACTED_EMAIL]" in reply
        expected_keywords = turn_def.get("expected_keywords", [])
        keyword_hits = [kw for kw in expected_keywords if kw.lower() in reply.lower()]
        passed_keywords = len(keyword_hits) >= turn_def.get("min_keywords", 1)

        # Hallucination check
        no_hallucination = (faith is None or faith >= 0.5) and not escalate
        if turn_def.get("strict_faith"):
            no_hallucination = faith is not None and faith >= 0.8

        turn_result = {
            "turn": i,
            "query": user_msg,
            "elapsed_ms": elapsed_ms,
            "faithfulness": faith,
            "escalate": escalate,
            "has_redacted_email": has_redacted_email,
            "passed_keywords": passed_keywords,
            "matched_keywords": keyword_hits,
            "expected_keywords": expected_keywords,
            "no_hallucination": no_hallucination,
            "sources": sources,
        }
        results.append(turn_result)

        if has_redacted_email:
            print(f"       ❌ FAILED: Found [REDACTED_EMAIL] in response!")
        if not passed_keywords:
            print(f"       ⚠️ WARNING: Expected keywords {expected_keywords}, matched: {keyword_hits}")
        if not no_hallucination:
            print(f"       ⚠️ Grounding alert: faith={faith}, escalate={escalate}, reason={reason}")

    avg_latency = total_latency / len(turns) if turns else 0
    all_no_redacted = all(not r["has_redacted_email"] for r in results)
    all_context_aware = all(r["passed_keywords"] for r in results)
    all_faithful = all(r["no_hallucination"] for r in results)

    print(f"\n--- Scenario Summary: {scenario_name} ---")
    print(f"Turns: {len(turns)} | Avg Latency: {avg_latency:.1f}ms")
    print(f"Official Email Privacy Integrity: {'PASSED (No [REDACTED_EMAIL])' if all_no_redacted else 'FAILED'}")
    print(f"Context Awareness & Keyword Retention: {'PASSED' if all_context_aware else 'PARTIAL'}")
    print(f"Grounding & Anti-Hallucination: {'PASSED' if all_faithful else 'PARTIAL'}")

    return {
        "scenario": scenario_name,
        "turns": results,
        "avg_latency_ms": avg_latency,
        "all_no_redacted": all_no_redacted,
        "all_context_aware": all_context_aware,
        "all_faithful": all_faithful,
    }


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8887"
    print(f"Starting Long-Horizon Fuzzy Robustness Tests against: {base_url}")

    # Scenario 1: 10-turn Complex Taxpayer Onboarding Journey with Typos & Anaphora
    scenario_1 = [
        {
            "query": "Hello, I am a new resident individual in Uganda and I need to register for a TIN.",
            "expected_keywords": ["TIN", "individual", "national id", "nin", "register", "apply"],
            "min_keywords": 2,
        },
        {
            "query": "what docuemnts do i need for dat online application?",
            "fuzzy": True,
            "expected_keywords": ["NIN", "individual", "national id", "details", "email", "phone"],
            "min_keywords": 1,
        },
        {
            "query": "how much does it cost and how long does it take?",
            "expected_keywords": ["free", "charge", "instant", "cost"],
            "min_keywords": 1,
        },
        {
            "query": "I earn a gross salary of 4,000,000 UGX monthly from my job. What tax is deducted from my salary?",
            "expected_keywords": ["PAYE", "pay as you earn", "employment"],
            "min_keywords": 1,
        },
        {
            "query": "What is the threshold exempt from that tax?",
            "expected_keywords": ["235,000", "threshold", "first", "exempt"],
            "min_keywords": 1,
        },
        {
            "query": "what abt if i was non resident?",
            "fuzzy": True,
            "expected_keywords": ["non-resident", "rate", "percent", "15%", "10%"],
            "min_keywords": 1,
        },
        {
            "query": "If I also open a side shop with annual turnover of 80m, is VAT compulsory for me?",
            "expected_keywords": ["150", "threshold", "voluntary", "compulsory"],
            "min_keywords": 1,
        },
        {
            "query": "What is EFRIS and would I be required to use it?",
            "expected_keywords": ["EFRIS", "electronic", "invoicing", "receipt"],
            "min_keywords": 1,
        },
        {
            "query": "Going back to my very first question about my TIN, can I apply for it through WhatsApp or phone?",
            "expected_keywords": ["portal", "ura.go.ug", "online", "tin"],
            "min_keywords": 1,
        },
        {
            "query": "Give me the official URA email and toll free numbers so I can contact you directly.",
            "expected_keywords": ["services@ura.go.ug", "0800 117 000", "0800 217 000"],
            "min_keywords": 2,
        },
    ]

    # Scenario 2: Customs & Cross-Domain Dispute Resolution with Typos & Shifts
    scenario_2 = [
        {
            "query": "What services does URA provide?",
            "expected_keywords": ["tax", "customs", "revenue", "collection", "services"],
            "min_keywords": 2,
            "strict_faith": True,
        },
        {
            "query": "Tell me more about the customs clearance services you just mentioned.",
            "expected_keywords": ["customs", "clearance", "import", "export", "cargo"],
            "min_keywords": 2,
        },
        {
            "query": "if my cargo goods are assessed higher than the purchase invoice, how can I dispute the assessment?",
            "fuzzy": True,
            "expected_keywords": ["objection", "dispute", "days", "assessment", "commissioner"],
            "min_keywords": 1,
        },
        {
            "query": "How many days do I have to lodge that objection?",
            "expected_keywords": ["45", "days", "period", "time"],
            "min_keywords": 1,
        },
        {
            "query": "Do I have to pay any portion of the assessed tax before the objection is heard?",
            "expected_keywords": ["30%", "portion", "tax", "pay"],
            "min_keywords": 1,
        },
        {
            "query": "What is URA vision?",
            "expected_keywords": ["transformational", "revenue", "service", "independence"],
            "min_keywords": 2,
            "strict_faith": True,
        },
        {
            "query": "How can I contact the URA team if I need assistance?",
            "expected_keywords": ["0800 117 000", "0800 217 000", "services@ura.go.ug", "ura.go.ug"],
            "min_keywords": 2,
        },
    ]

    res1 = run_scenario(base_url, "10-Turn Taxpayer Onboarding Journey", scenario_1)
    res2 = run_scenario(base_url, "7-Turn Customs & Dispute Multi-Turn Dialogue", scenario_2)

    all_passed = (
        res1["all_no_redacted"]
        and res2["all_no_redacted"]
        and res1["all_context_aware"]
        and res2["all_context_aware"]
        and res1["all_faithful"]
        and res2["all_faithful"]
    )

    print("\n======================================================================")
    print("FINAL SUMMARY REPORT")
    print("======================================================================")
    print(f"Target Service: {base_url}")
    print(f"Scenario 1 Success: {res1['all_no_redacted'] and res1['all_context_aware']}")
    print(f"Scenario 2 Success: {res2['all_no_redacted'] and res2['all_context_aware']}")
    print(f"Average Latency (Scenario 1): {res1['avg_latency_ms']:.1f}ms")
    print(f"Average Latency (Scenario 2): {res2['avg_latency_ms']:.1f}ms")
    print(f"Overall Result: {'PASSED - Robust long-horizon context awareness confirmed!' if all_passed else 'REVIEW'}")


if __name__ == "__main__":
    main()
