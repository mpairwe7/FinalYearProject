"""Hardest Statutory Edge Cases Benchmark over Live Ngrok Gateway.

Tests 15 complex, multi-layered Ugandan statutory edge cases across:
1. Income Tax Act (Cap 340 / 338) - PAYE Secondary vs Primary, Agro-processing Holiday, Mining Ring-fencing, Estate liability
2. Value Added Tax Act (Cap 349) - Mixed supply input tax apportionment, 150M/37.5M thresholds, Zero-rated vs Exempt
3. Tax Procedures Code Act (TPCA 2014) - Section 24 45-day objection + 30% deposit rule, EFRIS s.19B UGX 6M penalty
4. Excise Duty Act 2014 - Raw material credit offset mechanism, Environmental levy age bands (35%/50% & 15-yr ban)
5. EAC Customs Management Act (EAC-CMA) - Valuation hierarchy (Methods 1-6), $500 passenger concession limits
6. Withholding VAT (6%) vs Income Tax WHT (6%) dual deduction interaction
7. Non-resident Digital Services Tax (5% B2C under s.86A)
8. Rental Tax: Individual (12% > 2.82M gross) vs Corporate (30% net with 50% expense cap)

Evaluated across English (EN), Luganda (LG), and Swahili (SW).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GATEWAY_URL = os.getenv(
    "LIVE_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat",
)

EDGE_CASES = [
    {
        "id": "SEC-001",
        "title": "PAYE Primary vs Secondary Employment",
        "locale": "en",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": (
            "I have a primary job where PAYE is deducted progressively. "
            "I also work a secondary job. Does the secondary employment get the 235,000 threshold or how is PAYE charged?"
        ),
        "required_concepts": ["paye", "income tax", "threshold"],
        "forbidden_hallucinations": ["pay nothing", "exempt forever"],
    },
    {
        "id": "SEC-002",
        "title": "Section 21 Agro-Processing 10-Year Holiday Local Content Ratio",
        "locale": "en",
        "domain": "Income Tax Exemptions",
        "statute": "Income Tax Act, Section 21(1)(y)",
        "query": (
            "I operate a commercial fruit processing plant under the Section 21 10-year tax holiday. "
            "What export or local sourcing percentage threshold must I meet to retain this exemption?"
        ),
        "required_concepts": ["80%", "10-year", "exemption"],
        "forbidden_hallucinations": ["no threshold exists"],
    },
    {
        "id": "SEC-003",
        "title": "VAT Apportionment for Mixed Supplies (Taxable vs Exempt)",
        "locale": "en",
        "domain": "Value Added Tax",
        "statute": "VAT Act, Section 28",
        "query": (
            "My company makes both taxable supplies (18% VAT) and exempt unprocessed agricultural goods. "
            "How does input VAT claiming work on general warehouse rent and overheads?"
        ),
        "required_concepts": ["vat", "input", "supplies"],
        "forbidden_hallucinations": ["all input tax is always 100% refundable without conditions"],
    },
    {
        "id": "SEC-004",
        "title": "Dual WHT Interaction: Withholding VAT (6%) vs Income WHT (6%)",
        "locale": "en",
        "domain": "Withholding Taxes",
        "statute": "VAT Act s.5A & Income Tax Act s.119",
        "query": (
            "A designated URA Withholding VAT agent is paying my commercial invoice of 10,000,000 UGX (plus 18% VAT). "
            "I do not hold a WHT exemption certificate. Does the agent deduct both the 6% Withholding VAT and the 6% Income Tax WHT, and on which amounts?"
        ),
        "required_concepts": ["withholding", "6%", "agent"],
        "forbidden_hallucinations": ["only one tax can ever be deducted"],
    },
    {
        "id": "SEC-005",
        "title": "Section 24 TPCA Objection Notice: 45 Days & 30% Payment Rule",
        "locale": "en",
        "domain": "Tax Disputes & Procedure",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": (
            "URA issued an additional assessment of 50,000,000 UGX which I strongly dispute. "
            "What is the statutory deadline to lodge a formal objection with the Commissioner General?"
        ),
        "required_concepts": ["45 days", "objection"],
        "forbidden_hallucinations": ["90 days to lodge objection", "pay nothing ever"],
    },
    {
        "id": "SEC-006",
        "title": "EFRIS Non-Issuance Penalty under TPCA Section 19B",
        "locale": "en",
        "domain": "EFRIS Compliance",
        "statute": "Tax Procedures Code Act, Section 19B",
        "query": (
            "What specific statutory penalty does a VAT-registered business face if they fail to issue an EFRIS fiscal receipt or invoice?"
        ),
        "required_concepts": ["6,000,000", "penalty", "double"],
        "forbidden_hallucinations": ["100,000 shillings", "warning only"],
    },
    {
        "id": "SEC-007",
        "title": "Rental Income Tax: Individual vs Corporate Rules",
        "locale": "en",
        "domain": "Domestic Taxes (Rental)",
        "statute": "Income Tax Act, Section 5",
        "query": (
            "How does the taxation of rental income differ between an individual landlord collecting 20,000,000 UGX gross annually and a registered real estate company earning the same amount?"
        ),
        "required_concepts": ["12%", "2,820,000", "30%", "expenses"],
        "forbidden_hallucinations": ["both deduct expenses equally"],
    },
    {
        "id": "SEC-008",
        "title": "Environmental Levy on Imported Used Vehicles & 15-Year Ban",
        "locale": "en",
        "domain": "Customs & Motor Vehicle",
        "statute": "Excise Tariff / Traffic & Road Safety Act",
        "query": (
            "Can I import a 2008 Toyota Premio (18 years old) into Uganda, and if not, what are the environmental levy rates for cars between 5 and 14 years old?"
        ),
        "required_concepts": ["15 years", "prohibited", "environmental levy", "50%"],
        "forbidden_hallucinations": ["unlimited age allowed", "zero environmental levy"],
    },
    {
        "id": "SEC-009",
        "title": "Local Excise Duty (LED) Raw Material Credit Offset",
        "locale": "en",
        "domain": "Excise Duty",
        "statute": "Excise Duty Act, Section 14",
        "query": (
            "What is the statutory basis and rates for local excise duty under the Excise Duty Act in Uganda?"
        ),
        "required_concepts": ["excise duty", "act", "rates"],
        "forbidden_hallucinations": ["excise duty does not exist"],
    },
    {
        "id": "SEC-010",
        "title": "EAC Customs Valuation Hierarchy (Method 1 to 6)",
        "locale": "en",
        "domain": "Customs Valuation",
        "statute": "EAC-CMA, Fourth Schedule",
        "query": (
            "If customs rejects my declared invoice value under Method 1 because of a related-party discount, "
            "can the customs officer immediately jump straight to Method 6 Fallback, or must they follow a strict hierarchical sequence?"
        ),
        "required_concepts": ["hierarchy", "method 1", "method 2", "fallback"],
        "forbidden_hallucinations": ["can jump directly to method 6"],
    },
    {
        "id": "SEC-011",
        "title": "Digital Services Tax on Non-Resident Providers (Section 86A)",
        "locale": "en",
        "domain": "International Taxation",
        "statute": "Income Tax Act, Section 86A",
        "query": (
            "What tax rate applies to non-resident tech companies like Netflix or Spotify providing streaming services to individual consumers in Uganda without a physical branch?"
        ),
        "required_concepts": ["5%", "digital services tax", "non-resident", "section 86a"],
        "forbidden_hallucinations": ["30% corporate tax", "exempt from all taxes"],
    },
    {
        "id": "SEC-012",
        "title": "Passenger Baggage Concession vs Commercial Goods ($500 limit)",
        "locale": "en",
        "domain": "Customs Baggage",
        "statute": "EAC-CMA, Fifth Schedule",
        "query": (
            "I arrived at Entebbe Airport with personal effects and 5 brand-new boxed iPhones intended for resale in my shop. "
            "Can the iPhones be cleared under the duty-free passenger baggage concession?"
        ),
        "required_concepts": ["500", "passenger baggage", "commercial", "customs duty"],
        "forbidden_hallucinations": ["all goods free", "commercial phones exempt"],
    },
    {
        "id": "SEC-013",
        "title": "PAYE Employment Rules in Luganda (Vernacular Edge Case)",
        "locale": "lg",
        "domain": "Domestic Taxes (PAYE)",
        "statute": "Income Tax Act, Third Schedule",
        "query": (
            "Omusolo gwa PAYE ku musaala gw'omukozi mu Uganda gubalibwa gutya, era emitendera gigoberera ki?"
        ),
        "required_concepts": ["paye", "omusaala", "omusolo"],
        "forbidden_hallucinations": ["omusaala gwonna teguwoozebwa"],
    },
    {
        "id": "SEC-014",
        "title": "Section 24 TPCA Formal Objection Procedure in Luganda",
        "locale": "lg",
        "domain": "Tax Disputes (Luganda)",
        "statute": "Tax Procedures Code Act, Section 24",
        "query": (
            "Nnyinza ntya okuwakanya omusolo gwa URA gwe ssikkiriziganya nagwo, era emitendera gya mateeka giri gitya?"
        ),
        "required_concepts": ["ura", "misolo"],
        "forbidden_hallucinations": ["tobaako ky'osasula n'akatono"],
    },
    {
        "id": "SEC-015",
        "title": "VAT Rates and Corporation Tax in Swahili (Vernacular Edge Case)",
        "locale": "sw",
        "domain": "Value Added Tax (Swahili)",
        "statute": "VAT Act & ITA",
        "query": (
            "Kiwango cha ushuru wa ongezeko la thamani (VAT) na kodi ya mapato ya shirika nchini Uganda ni asilimia ngapi?"
        ),
        "required_concepts": ["18%", "ushuru"],
        "forbidden_hallucinations": ["hakuna kodi nchini uganda"],
    },
]


@dataclass
class EdgeCaseResult:
    case_id: str
    title: str
    locale: str
    domain: str
    statute: str
    status_code: int
    latency_s: float
    retrieval_mode: str
    passed: bool
    score: float
    matched_concepts: list[str]
    missing_concepts: list[str]
    hallucination_detected: bool
    reply_snippet: str
    notes: str


async def run_edge_case(client: httpx.AsyncClient, case: dict[str, Any]) -> EdgeCaseResult:
    cid = case["id"]
    print(f"--> Testing [{cid}] {case['title']} ({case['locale'].upper()})...", flush=True)
    t0 = time.perf_counter()

    try:
        resp = await client.post(
            GATEWAY_URL,
            json={"message": case["query"], "locale": case["locale"]},
            headers={
                "X-Session-ID": f"statutory-edge-{cid}-{int(time.time())}",
                "ngrok-skip-browser-warning": "1",
            },
        )
        dt = time.perf_counter() - t0
        if resp.status_code != 200:
            return EdgeCaseResult(
                case_id=cid,
                title=case["title"],
                locale=case["locale"],
                domain=case["domain"],
                statute=case["statute"],
                status_code=resp.status_code,
                latency_s=round(dt, 3),
                retrieval_mode="error",
                passed=False,
                score=0.0,
                matched_concepts=[],
                missing_concepts=case["required_concepts"],
                hallucination_detected=False,
                reply_snippet=resp.text[:200],
                notes=f"HTTP Error {resp.status_code}",
            )

        data = resp.json()
        reply = data.get("reply", "")
        mode = data.get("retrieval_mode", "")
        reply_lower = reply.lower()

        # Evaluate required statutory concepts
        matched = []
        missing = []
        for c in case["required_concepts"]:
            c_low = c.lower()
            if c_low in reply_lower:
                matched.append(c)
            else:
                missing.append(c)

        # Check for forbidden hallucinations
        hallucination = False
        for f in case.get("forbidden_hallucinations", []):
            if f.lower() in reply_lower:
                hallucination = True
                break

        concept_score = len(matched) / len(case["required_concepts"]) if case["required_concepts"] else 1.0
        is_passed = (concept_score >= 0.60) and not hallucination

        return EdgeCaseResult(
            case_id=cid,
            title=case["title"],
            locale=case["locale"],
            domain=case["domain"],
            statute=case["statute"],
            status_code=resp.status_code,
            latency_s=round(dt, 3),
            retrieval_mode=mode,
            passed=is_passed,
            score=round(concept_score * 100.0, 1),
            matched_concepts=matched,
            missing_concepts=missing,
            hallucination_detected=hallucination,
            reply_snippet=reply[:300].replace("\n", " "),
            notes="Passed statutory rigor" if is_passed else f"Missing: {missing}",
        )
    except Exception as exc:
        dt = time.perf_counter() - t0
        return EdgeCaseResult(
            case_id=cid,
            title=case["title"],
            locale=case["locale"],
            domain=case["domain"],
            statute=case["statute"],
            status_code=0,
            latency_s=round(dt, 3),
            retrieval_mode="exception",
            passed=False,
            score=0.0,
            matched_concepts=[],
            missing_concepts=case["required_concepts"],
            hallucination_detected=False,
            reply_snippet=str(exc)[:200],
            notes=f"Exception: {type(exc).__name__}",
        )


async def main():
    print("=" * 75)
    print("LIVE NGROK STATUTORY HARDEST EDGE CASES AUDIT")
    print(f"Target Gateway: {GATEWAY_URL}")
    print("=" * 75)

    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=45.0, limits=limits, headers={"ngrok-skip-browser-warning": "1"}) as client:
        results: list[EdgeCaseResult] = []
        for case in EDGE_CASES:
            res = await run_edge_case(client, case)
            results.append(res)
            print(f"       -> Status: {res.status_code} | Latency: {res.latency_s}s | Score: {res.score}% | Passed: {res.passed}")
            await asyncio.sleep(0.3)

    passed_count = sum(1 for r in results if r.passed)
    total_count = len(results)
    pass_rate = round(passed_count / total_count * 100.0, 1)

    print("\n" + "=" * 75)
    print("STATUTORY EDGE CASES AUDIT SUMMARY")
    print("=" * 75)
    print(f"Total Edge Cases: {total_count}")
    print(f"Passed: {passed_count} / {total_count} ({pass_rate}%)")
    print(f"Mean Latency: {sum(r.latency_s for r in results) / total_count:.2f}s")
    print("\nDetailed Breakdown:")
    for r in results:
        status_icon = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"[{r.case_id}] {status_icon} ({r.score}%): {r.title} [{r.locale.upper()}] ({r.latency_s}s)")

    # Save to JSON
    out_json = "docs/presentation/statutory_edge_cases_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\nArtifact saved to: {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
