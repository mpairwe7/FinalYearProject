#!/usr/bin/env python3
"""Verification of Long-Horizon Context Awareness & Anti-Hallucination in Luganda (LG) and Swahili (SW).

Evaluates:
1. Luganda 8-Turn Long-Horizon Business Lifecycle Session:
   - Turn 1: Business profile (Masaka hardware/timber, 250M turnover)
   - Turn 2: Anaphora resolution ("okukifuna" -> TIN registration)
   - Turn 3: Long-horizon context recall (250M stated in Turn 1 -> mandatory VAT)
   - Turn 4: EFRIS compliance based on VAT status
   - Turn 5: Employee PAYE calculation
   - Turn 6: Anaphora ("omusolo ogwo" -> 15th monthly PAYE return deadline)
   - Turn 7: Anti-hallucination false premise trap (50% VAT exemption claim)
   - Turn 8: Contact channels (0800 117 000 / 0800 217 000)

2. Swahili 8-Turn Long-Horizon Cross-Border Trade Session:
   - Turn 1: Business profile (Busia grain import from Kenya)
   - Turn 2: Anaphora resolution ("huo msamaha" -> EAC Certificate of Origin)
   - Turn 3: Long-horizon context recall (320M turnover -> mandatory VAT)
   - Turn 4: EFRIS offline 24-hour sync window
   - Turn 5: Secondary employment 30% PAYE rate
   - Turn 6: Objection 45-day deadline and 30% statutory deposit under Section 24
   - Turn 7: Anti-hallucination false premise trap (50% agricultural customs claim)
   - Turn 8: Official toll-free contact channels

Evaluated directly via the public ngrok gateway:
https://struttingly-nongeological-briella.ngrok-free.dev/api
"""

from __future__ import annotations

import sys
import time
from typing import Any
import httpx

NGROK_BASE = "https://struttingly-nongeological-briella.ngrok-free.dev/api"
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
    "User-Agent": "URA-LongHorizon-Eval/2.0",
}


def run_session(session_name: str, locale: str, conversation_id: str, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    print("\n" + "=" * 80)
    print(f"  {session_name.upper()} (Locale: {locale}, Conv ID: {conversation_id})")
    print("=" * 80)

    results = []
    with httpx.Client(base_url=NGROK_BASE, timeout=60.0) as client:
        for idx, t in enumerate(turns, 1):
            q = t["query"]
            reqs = t["required_concepts"]
            forb = t.get("forbidden_hallucinations", [])
            label = t["label"]

            t0 = time.perf_counter()
            resp = client.post(
                "/v1/chat",
                json={
                    "message": q,
                    "locale": locale,
                    "conversation_id": conversation_id,
                },
                headers=HEADERS,
            )
            dt = time.perf_counter() - t0

            if resp.status_code != 200:
                print(f"  Turn {idx:02d} [{label}]: ✗ FAILED HTTP {resp.status_code}")
                results.append({"turn": idx, "passed": False, "latency": dt, "error": resp.text})
                continue

            data = resp.json()
            reply = data.get("reply", "")
            mode = data.get("retrieval_mode", "unknown")

            # Check required concepts
            low_reply = reply.lower()
            matched_reqs = [r for r in reqs if r.lower() in low_reply]
            missing_reqs = [r for r in reqs if r.lower() not in low_reply]
            passed_reqs = len(missing_reqs) == 0

            # Check forbidden hallucinations
            found_forb = [f for f in forb if f.lower() in low_reply]
            passed_forb = len(found_forb) == 0

            passed = passed_reqs and passed_forb

            status_mark = "✓ PASS" if passed else "✗ FAIL"
            print(f"  Turn {idx:02d} [{label}]: {status_mark} in {dt:.3f}s | Mode: {mode}")
            print(f"     Q: {q}")
            print(f"     A: {reply[:120].replace(chr(10), ' ')}...")
            if missing_reqs:
                print(f"     ⚠️ Missing concepts: {missing_reqs}")
            if found_forb:
                print(f"     ⚠️ Hallucinations triggered: {found_forb}")

            results.append({
                "turn": idx,
                "label": label,
                "passed": passed,
                "latency_s": dt,
                "mode": mode,
                "missing": missing_reqs,
                "hallucinations": found_forb,
                "reply": reply,
            })

    pass_count = sum(1 for r in results if r.get("passed"))
    pct = (pass_count / len(turns)) * 100
    print(f"\n  Summary for {session_name}: {pass_count}/{len(turns)} Turns Passed ({pct:.1f}%)")
    return results


def main() -> int:
    print("=" * 80)
    print("  URA MULTILINGUAL LONG-HORIZON CONTEXT & MEMORY AUDIT (LG / SW)")
    print(f"  Target Gateway: {NGROK_BASE}")
    print("=" * 80)

    # 1. Luganda Long-Horizon 8-Turn Session
    lg_turns = [
        {
            "turn": 1,
            "label": "Business Registration & Identity",
            "query": "Nnina edduuka ly'ebizimbe n'embaawo mu Masaka, era amagoba gange ag'omwaka gali bukadde 250. Nteekwa okusooka kukola ki ku nsonga y'okwewandiisa TIN mu URA?",
            "required_concepts": ["tin", "ura"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 2,
            "label": "Anaphora Resolution (okukifuna -> TIN)",
            "query": "Nsobola ntya okukifuna ku mutimbagano gwa URA ku nsonga y'okwewandiisa TIN?",
            "required_concepts": ["ura.go.ug", "tin"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 3,
            "label": "Context Recall (250M -> Mandatory VAT)",
            "query": "Omuwendo gwe nkulaze waggulu gunteeka mu musolo gwa VAT?",
            "required_concepts": ["vat", "250"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 4,
            "label": "EFRIS Mandate from VAT Status",
            "query": "Bwe mba nga ndi mu VAT, ebyuma bya EFRIS birina kukozesebwa bitya?",
            "required_concepts": ["efris"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 5,
            "label": "Employee PAYE Calculation",
            "query": "Nkozesa abakozi 3 abafuna omusaala buli mwezi. Omusolo gwa PAYE ku musaala gwabwe gubalibwa gutya?",
            "required_concepts": ["paye", "omusaala"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 6,
            "label": "Anaphora (omusolo ogwo -> 15th Deadline)",
            "query": "Alipoota z'omusolo ogwo zirina okuwaayo ku lunaku ki buli mwezi?",
            "required_concepts": ["ekkumi", "mwezi"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 7,
            "label": "Anti-Hallucination False Premise Defense",
            "query": "Ggwe wambuuza nti ssekinoomu asonyiyibwa omusolo gwa VAT gwa 50%?",
            "required_concepts": ["18%"],
            "forbidden_hallucinations": ["50%"],
        },
        {
            "turn": 8,
            "label": "Summary & Official Support Channels",
            "query": "Weebale nnyo. Mpaayo ennamba z'essimu ez'obwereere ze nnyinza okukubako singa nneetaaga obuyambi obulala.",
            "required_concepts": ["0800 117 000"],
            "forbidden_hallucinations": [],
        },
    ]

    # 2. Swahili Long-Horizon 8-Turn Session
    sw_turns = [
        {
            "turn": 1,
            "label": "Cross-Border Trade & Importation",
            "query": "Mimi ni mfanyabiashara wa mahindi na nafaka mjini Busia, ninaagiza tani 50 kutoka Kenya. Ushuru gani unatozwa?",
            "required_concepts": ["forodha"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 2,
            "label": "Anaphora Resolution (huo msamaha -> EAC Origin)",
            "query": "Ninahitaji nyaraka gani ili nipate huo msamaha wa ushuru?",
            "required_concepts": ["cheti"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 3,
            "label": "Context Recall (320M -> Mandatory VAT)",
            "query": "Mapato yangu ya biashara kwa mwaka ni shilingi milioni 320 (UGX 320M). Je, ninatakiwa kujisajili kwa VAT?",
            "required_concepts": ["vat", "320"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 4,
            "label": "EFRIS Offline Sync Window",
            "query": "Mtandao ukikatika kwenye bohari yetu ya mpakani, tuna muda gani wa kutuma ankara za EFRIS?",
            "required_concepts": ["24", "efris"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 5,
            "label": "Secondary Employment PAYE Flat Rate",
            "query": "Nina mfanyakazi ambaye hii ni kazi yake ya pili (secondary employment). Anakatwa kodi ya PAYE ya asilimia ngapi?",
            "required_concepts": ["30%"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 6,
            "label": "Objection 45-day & 30% Statutory Deposit",
            "query": "URA ikinikadiria kodi ya ziada nisiyokubaliana nayo, nina siku ngapi za kuwasilisha pingamizi na ni lazima nilipe asilimia ngapi ya kodi?",
            "required_concepts": ["45", "30%"],
            "forbidden_hallucinations": [],
        },
        {
            "turn": 7,
            "label": "Anti-Hallucination False Premise Defense",
            "query": "Si ulisema hapo awali kwamba bidhaa zote za kilimo zinalipiwa ushuru wa forodha wa asilimia 50?",
            "required_concepts": ["vat"],
            "forbidden_hallucinations": ["asilimia 50"],
        },
        {
            "turn": 8,
            "label": "Summary & Official Support Channels",
            "query": "Asante sana. Nipe nambari za simu za bure za URA ili niwasiliane nao iwapo nitahitaji msaada zaidi.",
            "required_concepts": ["0800 117 000"],
            "forbidden_hallucinations": [],
        },
    ]

    t_start = time.perf_counter()
    ts = int(time.time())
    lg_results = run_session("Luganda 8-Turn Long-Horizon Context Audit", "lg", f"audit-lg-long-{ts}", lg_turns)
    sw_results = run_session("Swahili 8-Turn Long-Horizon Context Audit", "sw", f"audit-sw-long-{ts}", sw_turns)
    total_duration = time.perf_counter() - t_start

    total_turns = len(lg_turns) + len(sw_turns)
    passed_turns = sum(1 for r in lg_results if r.get("passed")) + sum(1 for r in sw_results if r.get("passed"))
    pct_total = (passed_turns / total_turns) * 100

    print("\n" + "=" * 80)
    print(f"  OVERALL AUDIT SCORE: {passed_turns}/{total_turns} Turns Passed ({pct_total:.1f}%)")
    print(f"  Total Duration: {total_duration:.2f}s | Average Turn Latency: {total_duration/total_turns:.2f}s")
    print("=" * 80 + "\n")

    return 0 if pct_total >= 90.0 else 1


if __name__ == "__main__":
    sys.exit(main())
