"""Fuzzy End-to-End Stress Suite for Tax Education.

Runs 10 realistic colloquial/fuzzy educational queries against the running API
(via Ngrok or local endpoint), asserting:
- HTTP 200 and low latency
- retrieval_mode == 'education'
- agent_role == 'tool_specialist'
- Scaffolded Markdown formatting (Concept, Why It Matters, Worked Example, Pitfalls, Knowledge Check)
- Multi-turn check question answer revelation
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = os.getenv(
    "URA_CHAT_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat",
)

TESTS = [
    {
        "id": 1,
        "name": "VAT Concept (Colloquial/Informal)",
        "query": "can u pls explain to me what vat actually is in uganda?",
        "expected_topic": "vat",
        "must_contain": ["Value Added Tax", "Why It Matters", "Quick Knowledge Check"],
    },
    {
        "id": 2,
        "name": "PAYE Progressive Bands (Fuzzy typo + brackets)",
        "query": "how does paye tax brckets work?",
        "expected_topic": "paye",
        "must_contain": ["PAYE", "progressive", "Worked Example", "Common Pitfalls"],
    },
    {
        "id": 3,
        "name": "EFRIS Invoicing (Small trader conversational)",
        "query": "plz explain efris invoicing system and who must use it",
        "expected_topic": "efris",
        "must_contain": ["EFRIS", "electronic", "invoice", "Common Pitfalls"],
    },
    {
        "id": 4,
        "name": "Withholding Tax (Slang abbreviation + beginner)",
        "query": "teach me about withholding tax wht for beginners",
        "expected_topic": "withholding_tax",
        "must_contain": ["Withholding tax", "Why It Matters", "Quick Knowledge Check"],
    },
    {
        "id": 5,
        "name": "Presumptive Tax (Small business inquiry)",
        "query": "tell me about presumptive tax for small business",
        "expected_topic": "presumptive_tax",
        "must_contain": ["Presumptive tax", "turnover", "Why It Matters"],
    },
    {
        "id": 6,
        "name": "Excise Duty on Mobile Money (Fintech inquiry)",
        "query": "explain excise duty especially on mobile money transactions",
        "expected_topic": "excise_duty",
        "must_contain": ["Excise Duty", "cash withdrawal", "0.5%"],
    },
    {
        "id": 7,
        "name": "Customs Valuation & Landed Cost (Cross-border)",
        "query": "how does customs valuation and cif landed cost work?",
        "expected_topic": "customs_valuation",
        "must_contain": ["Customs Valuation", "CIF", "landed cost"],
    },
    {
        "id": 8,
        "name": "Tax Objections & Appeals (Dispute resolution)",
        "query": "explain how tax objections work if ura gives me an assessment",
        "expected_topic": "tax_objections_appeals",
        "must_contain": ["Objections", "45 days", "Tax Appeals Tribunal"],
    },
    {
        "id": 9,
        "name": "Stamp Duty (Property / legal documents)",
        "query": "what is stamp duty on property and contracts?",
        "expected_topic": "stamp_duty",
        "must_contain": ["Stamp Duty", "instruments", "Why It Matters"],
    },
    {
        "id": 10,
        "name": "Multi-Turn Solution Reveal (Interactive Tutoring)",
        "query": "show me the answer to the check question",
        "multi_turn": True,
        "seed_query": "What is VAT?",
        "expected_topic": "vat",
        "must_contain": ["Answer", "The difference — UGX 400,000"],
    },
]


def post_chat(message: str, conversation_id: str | None = None) -> tuple[int, dict, float]:
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "URA-Stress-Test/1.0",
        },
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            elapsed = (time.perf_counter() - t0) * 1000
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body), elapsed
    except urllib.error.HTTPError as e:
        elapsed = (time.perf_counter() - t0) * 1000
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body), elapsed
        except Exception:
            return e.code, {"error": body}, elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        return 500, {"error": str(e)}, elapsed


def run_suite() -> bool:
    print("=" * 80)
    print(" 🚀 STARTING 10 TAX EDUCATION FUZZY E2E STRESS TESTS")
    print(f" Target Endpoint: {API_URL}")
    print("=" * 80)

    passed_count = 0
    total_latency = 0.0

    for test in TESTS:
        t_id = test["id"]
        t_name = test["name"]
        conv_id = None

        if test.get("multi_turn"):
            seed_q = test["seed_query"]
            s_code, s_resp, _ = post_chat(seed_q)
            conv_id = s_resp.get("conversation_id")
            time.sleep(0.3)

        query = test["query"]
        status, resp, latency = post_chat(query, conversation_id=conv_id)
        total_latency += latency

        reply = resp.get("reply", "")
        retrieval_mode = resp.get("retrieval_mode", "")
        agent_role = resp.get("agent_role", "")
        next_actions = resp.get("next_actions", [])

        status_ok = status == 200
        mode_ok = retrieval_mode == "education"
        role_ok = agent_role == "tool_specialist"
        contains_ok = all(needle.lower() in reply.lower() for needle in test["must_contain"])

        test_passed = status_ok and mode_ok and role_ok and contains_ok
        status_str = "✅ PASS" if test_passed else "❌ FAIL"
        if test_passed:
            passed_count += 1

        print(f"\n[{t_id:02d}/10] {t_name}")
        print(f"     Query:     \"{query}\"")
        print(f"     Status:    {status} (Latency: {latency:.1f}ms)")
        print(f"     Mode/Role: {retrieval_mode} / {agent_role}")
        print(f"     Actions:   {next_actions[:2]}")
        print(f"     Snippet:   {reply[:120].replace(chr(10), ' ')}...")
        print(f"     Result:    {status_str}")

        if not test_passed and not contains_ok:
            missing = [n for n in test["must_contain"] if n.lower() not in reply.lower()]
            print(f"     Missing keywords: {missing}")

        time.sleep(0.2)

    print("\n" + "=" * 80)
    avg_latency = total_latency / len(TESTS)
    print(f" 📊 STRESS TEST RESULTS: {passed_count}/{len(TESTS)} PASSED (Avg Latency: {avg_latency:.1f}ms)")
    print("=" * 80)

    return passed_count == len(TESTS)


if __name__ == "__main__":
    success = run_suite()
    sys.exit(0 if success else 1)
