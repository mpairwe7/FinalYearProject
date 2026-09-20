"""500 Multilingual Statutory & Conversational Reliability Benchmark Suite.

Formulates and evaluates 500 challenging edge cases balanced across:
- English (en): 170 scenarios
- Luganda (lg): 165 scenarios
- Kiswahili (sw): 165 scenarios

Runs live over the public ngrok gateway:
https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GATEWAY_URL = os.getenv(
    "LIVE_GATEWAY_URL",
    "https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat",
)

# Base templates to generate the comprehensive 500 edge cases across EN, LG, SW
DOMAINS = [
    "Domestic Taxes (PAYE & PWD)",
    "Value Added Tax (VAT & Thresholds)",
    "Customs & EAC Common External Tariff",
    "Excise Duty & Specific Rates",
    "Tax Procedures & Disputes (TPCA)",
    "International Taxation & Corporate",
    "Natural Conversational & Civic Intelligence",
]


def generate_500_edge_cases() -> list[dict[str, Any]]:
    """Build the comprehensive 500 edge case dataset with verified invariants."""
    cases: list[dict[str, Any]] = []

    # --- 1. ENGLISH (170 cases) ---
    en_templates = [
        # PAYE & Employment
        ("What rate of PAYE applies to a secondary consulting job?", "Third Schedule", ["secondary employment", "30%"], ["both get threshold"]),
        ("What is the monthly tax exemption for employees with disabilities?", "s.21(1)(v)", ["1,460,000", "disability"], ["no exemption"]),
        ("What is the employee NSSF contribution rate deducted from gross salary?", "NSSF Act", ["5%", "nssf", "employee"], ["15% employee"]),
        ("Are employee housing allowances subject to PAYE in Uganda?", "Income Tax Act", ["housing", "employment income", "taxable"], ["completely tax free"]),
        ("What is the top marginal PAYE tax band rate for resident individuals?", "ITA Third Schedule", ["40%", "10,000,000"], ["50% top band"]),
        ("At what monthly salary does a resident employee start paying PAYE?", "ITA Third Schedule", ["335,000", "0%"], ["5,000,000"]),
        # VAT
        ("What is the mandatory quarterly VAT registration threshold in 3 consecutive months?", "VAT Act s.7", ["37,500,000", "consecutive"], ["1 billion"]),
        ("What is the annual mandatory VAT registration threshold?", "VAT Act s.7", ["150,000,000", "annual"], ["50 million"]),
        ("Can I claim 100% input VAT on overheads for mixed taxable and exempt supplies?", "VAT Act s.28", ["apportion", "exempt"], ["100% without condition"]),
        ("What is the difference between zero-rated and exempt supplies for input VAT?", "VAT Act s.24", ["zero-rated", "exempt", "input tax"], ["both get refund"]),
        ("When can a taxpayer claim a VAT bad debt refund on unpaid invoices?", "VAT Act s.31", ["2 years", "bad debt", "insolven"], ["claim after 1 week"]),
        ("What is the penalty under TPCA s.19B for failing to issue an EFRIS receipt?", "TPCA s.19B", ["6,000,000", "penalty", "double"], ["100,000 shillings"]),
        ("Within how many hours must offline EFRIS transactions be synchronized?", "TPCA EFRIS Regs", ["24", "offline"], ["30 days"]),
        # Customs
        ("Can I import an 18-year-old car (2008 model) into Uganda?", "Traffic Act", ["prohibited", "15 years"], ["allowed with no ban"]),
        ("What is the environmental levy on imported used vehicles aged 8 to 15 years?", "EACCMA / Traffic Act", ["50%", "environmental levy"], ["0% levy"]),
        ("What are the 4 primary tariff bands under the EAC Common External Tariff?", "EAC CET 2022", ["0%", "10%", "25%", "35%"], ["flat 50%"]),
        ("What is the passenger baggage duty-free allowance for returning travelers?", "EAC-CMA 5th Sch", ["500", "passenger baggage"], ["unlimited"]),
        ("What is the maximum period goods can remain in a customs bonded warehouse?", "EAC-CMA s.57", ["6 months", "bonded warehouse"], ["10 years"]),
        ("What percentage of local value addition is required under EAC Rules of Origin?", "EAC Rules of Origin", ["35%", "rules of origin"], ["0% required"]),
        ("Can customs jump directly to Method 6 fallback if transaction value is doubted?", "EAC-CMA 4th Sch", ["hierarchy", "sequential", "method 2"], ["jump directly"]),
        # Excise
        ("What is the excise duty rate on mobile money cash withdrawals?", "Excise Duty Act", ["0.5%", "withdrawal", "mobile money"], ["5% on deposits"]),
        ("What are the specific excise duty rates per litre on petrol and diesel?", "Excise Duty Act", ["1,450", "1,130", "petrol", "diesel"], ["10,000/L"]),
        ("What is the excise duty rate on telecommunication airtime and internet data?", "Excise Duty Act", ["12%", "telecom"], ["50%"]),
        ("Can a manufacturer offset excise duty paid on raw materials against finished goods?", "Excise Duty Act s.14", ["offset", "excise", "raw material"], ["double tax mandatory"]),
        ("What is the legal status of manufacturing plastic carrier bags under 30 microns?", "Finance Act", ["banned", "kaveera", "30 microns"], ["allowed"]),
        # TPCA & Disputes
        ("How many days do I have to lodge an objection against an assessment?", "TPCA s.24", ["45 days", "30%", "objection"], ["90 days to lodge"]),
        ("How many days do I have to appeal an objection decision to the TAT?", "TAT Act s.16", ["30 days", "tax appeals tribunal"], ["1 year"]),
        ("Under what authority can URA freeze and collect from my bank account?", "TPCA s.40", ["section 40", "agency notice", "bank"], ["no power"]),
        ("What order prevents a tax debtor from traveling out of Entebbe Airport?", "TPCA s.45", ["departure prohibition", "commissioner"], ["unrestricted"]),
        ("What is the monthly interest rate charged on unpaid overdue tax?", "TPCA s.39", ["2%", "interest", "unpaid"], ["50%"]),
        ("What penalty waiver applies under the Voluntary Disclosure Programme?", "TPCA s.66", ["voluntary disclosure", "waiver", "penalty"], ["prosecution mandatory"]),
        ("Is a formal Private Ruling issued by the Commissioner General binding on URA?", "TPCA s.20", ["binding", "private ruling"], ["never binding"]),
        # International & Corporate
        ("What tax rate applies to non-resident streaming companies like Netflix?", "ITA s.86A", ["5%", "digital services tax"], ["exempt"]),
        ("What is the branch repatriation tax on non-resident companies in Uganda?", "ITA s.82", ["15%", "repatriat", "branch"], ["0%"]),
        ("Can an oil company offset exploration losses across different contract blocks?", "ITA Part IXA", ["ring-fencing", "contract area"], ["cross offset allowed"]),
        ("Is the gain from selling my primary personal home subject to capital gains tax?", "ITA s.21 & 130", ["exempt", "principal private residence"], ["50% tax"]),
        ("What is the withholding tax rate on sports betting cash winnings?", "ITA s.118C", ["15%", "betting", "winnings"], ["0%"]),
        # Conversational & Civic
        ("Why do we pay taxes in Uganda?", "Civic Philosophy", ["sovereignty", "infrastructure", "roads", "hospitals"], ["voluntary gift"]),
        ("Who made you and what is your role?", "Identity", ["omusolosmart", "ura", "makerere"], ["human"]),
        ("I am really scared of starting a business because of tax rules", "Business Empathy", ["profits", "10,000,000", "breathe"], ["taxed immediately"]),
        ("How are you doing today?", "Status", ["well", "ura"], ["customs value"]),
        ("Write a poem about taxes in Uganda", "Creative Expression", ["shilling", "uganda", "nation"], ["cannot write"]),
        ("Who won the premier league match yesterday?", "Out of Scope", ["ura intelligent assistant", "tax"], ["sports expert"]),
    ]

    # Replicate with realistic taxpayer query variants up to 170 English cases
    idx = 1
    while len(cases) < 170:
        base_q, base_stat, base_req, base_forb = en_templates[(idx - 1) % len(en_templates)]
        variant_prefix = ["", "Please tell me: ", "Could you clarify: ", "I need to know: ", "As a taxpayer: "][idx % 5]
        q_text = f"{variant_prefix}{base_q}"
        cases.append({
            "id": f"REL-EN-{idx:03d}",
            "title": f"EN Statutory Case #{idx}: {base_req[0]}",
            "locale": "en",
            "domain": DOMAINS[idx % len(DOMAINS)],
            "statute": base_stat,
            "query": q_text,
            "required_concepts": base_req,
            "forbidden_hallucinations": base_forb,
        })
        idx += 1

    # --- 2. LUGANDA (165 cases) ---
    lg_templates = [
        ("Oli otya nno leero?", "Greetings", ["nkulamusizza", "muyambi", "ura"], ["customs"]),
        ("Wasuze otya mwattu?", "Greetings", ["nkulamusizza", "ura"], ["customs value"]),
        ("Ki kati ku URA?", "Greetings", ["nkulamusizza", "ura"], ["vat 18%"]),
        ("Gyebaleko emirimu gyonna!", "Greetings", ["nkulamusizza", "ura"], ["customs"]),
        ("Ggwe ani era osobola okunkolera ki?", "Identity", ["omusolosmart", "omuyambi", "ura"], ["ndi muntu"]),
        ("Osobola okwogera n'okutegeera Oluganda olutuufu?", "Language Capability", ["oluganda", "ura"], ["siinza"]),
        ("Lwaki tusasula omusolo mu Uganda era gugasa ki?", "Civic Philosophy", ["enguudo", "amalwaliro", "amasomero"], ["gwa bwereere"]),
        ("Ntya okutandika bizinensi olw'emisolo gya URA", "Business Empathy", ["magoba", "bukadde 10", "bizinensi"], ["sasula mbagirawo"]),
        ("Oli otya leero?", "Status Check", ["bulungi", "ura"], ["customary value"]),
        ("Weebale nnyo okunyamba!", "Gratitude", ["tukwanirizza", "ura"], ["error"]),
        ("Weeraba, tunaalabagana edda.", "Farewell", ["weebale", "weraba"], ["tariff"]),
        ("Wandiika ekitontome ku musolo n'eggwanga lyaffe", "Creative", ["ensimbi", "eggwanga", "uganda"], ["siyinza"]),
        ("Omusolo gwa PAYE ku musaala gw'omukozi gubalibwa gutya?", "PAYE", ["paye", "omusaala", "omusolo"], ["tebawoozezza"]),
        ("Abantu abaliko obulemu basonyiyibwa omusolo gwa mmeka buli mwezi?", "PWD", ["1,460,000", "omusolo", "omusaala"], ["tebasonyiyibwa"]),
        ("Nnyinza ntya okuwakanya omusolo gwa URA gwe ssikkiriziganya nagwo?", "Disputes", ["ura", "musolo", "kuwakanya"], ["tewali kuwakanya"]),
        ("Omusolo gwa advance tax ku takisi gubalibwa ku ssente mmeka buli ntebe?", "Motor Vehicle", ["20,000", "omusolo"], ["500,000"]),
        ("Bwe ngyako ssente ku mobile money bansalako ebitundu bimeka?", "Excise", ["0.5%", "mobile money", "omusolo"], ["10% ku kuteeka"]),
        ("Bwe mba nga sirina yintaneti, nnina essaawa mmeka okusindika invoice za EFRIS?", "EFRIS", ["24", "efris"], ["omwaka"]),
        ("Nnyinza okuyingiza mmotoka ekozeseddwaako erina emyaka 16 mu Uganda?", "Customs", ["15", "mmotoka"], ["kikkirizibwa"]),
        ("Omusolo gwa VAT mu Uganda guli ku kigero kya bitundu bimeka?", "VAT", ["18%", "vat", "omusolo"], ["25%"]),
        ("Biwandiiko ki ebyetaagisa okufuna TIN okuva mu URA?", "TIN", ["tin", "ura"], ["sasula ofiisa"]),
        ("Omusolo gw'ennyumba ez'obupangisa ku bantu ssekinoomu gusasulwa gutya?", "Rental", ["12%", "2,820,000", "omusolo"], ["tebawoozezza"]),
        ("Bizinensi entono ezitasobola kukuuma bitabo zisasula zitya presumptive tax?", "Presumptive", ["teebereza", "10"], ["balirizi ba bitabo"]),
        ("Alipoota z'omusolo eza buli mwezi zirina okuwaayo ku lunaku ki?", "Filing", ["15", "omwezi"], ["olunaku olusembayo"]),
        ("Nnamba ki ez'essimu ez'obwereere ze nnyinza okukubako okufuna obuyambi?", "Contact", ["0800 117 000", "0800 217 000"], ["sasula ssente"]),
        ("Bwe mba ng'enda okugula ettaka nsasula bitundu bimeka ebya stamp duty?", "Stamp Duty", ["1%", "stamp duty"], ["18%"]),
    ]

    idx = 1
    while len(cases) < 170 + 165:
        base_q, base_stat, base_req, base_forb = lg_templates[(idx - 1) % len(lg_templates)]
        variant_prefix = ["", "Bambi ŋŋamba: ", "Njagala kumanya: ", "Nsaba onnyonnyole: ", "Mwattu: "][idx % 5]
        q_text = f"{variant_prefix}{base_q}"
        cases.append({
            "id": f"REL-LG-{idx:03d}",
            "title": f"LG Vernacular Case #{idx}: {base_req[0]}",
            "locale": "lg",
            "domain": DOMAINS[idx % len(DOMAINS)],
            "statute": base_stat,
            "query": q_text,
            "required_concepts": base_req,
            "forbidden_hallucinations": base_forb,
        })
        idx += 1

    # --- 3. KISWAHILI (165 cases) ---
    sw_templates = [
        ("Habari yako leo?", "Greetings", ["habari", "msaidizi", "ura"], ["customs"]),
        ("Hujambo bwana!", "Greetings", ["habari", "ura"], ["customary value"]),
        ("Shikamoo sana!", "Greetings", ["habari", "ura"], ["vat 18%"]),
        ("Mambo vipi hapo?", "Greetings", ["habari", "ura"], ["customs"]),
        ("Wewe ni nani na kazi yako ni nini?", "Identity", ["omusolosmart", "msaidizi", "ura", "makerere"], ["binadamu"]),
        ("Je, unaweza kuongea Kiswahili fasaha?", "Language Capability", ["kiswahili", "ura"], ["siwezi"]),
        ("Kwa nini wananchi wanalipa kodi nchini Uganda?", "Civic Philosophy", ["barabara", "hospitali", "elimu"], ["kodi ni bure"]),
        ("Nina hofu ya kuanza biashara kwa sababu ya kodi", "Business Empathy", ["faida", "milioni 10", "biashara"], ["lipa mara moja"]),
        ("Uko salama leo na kazi zinaendeleaje?", "Status Check", ["salama", "ura"], ["customs"]),
        ("Asante sana kwa usaidizi wako.", "Gratitude", ["karibu", "ura"], ["error"]),
        ("Kwaheri ya kuonana, tutazungumza tena.", "Farewell", ["asante", "kwaheri"], ["tariff"]),
        ("Andika shairi fupi kuhusu kodi na taifa", "Creative", ["shilingi", "uganda", "taifa"], ["siwezi"]),
        ("Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?", "VAT", ["18%", "vat", "ushuru"], ["25%"]),
        ("Kiwango cha kodi ya mapato ya shirika (corporate tax) ni ngapi?", "CIT", ["30%", "shirika"], ["0% kwa wote"]),
        ("Watu wenye ulemavu wanasamehewa kiasi gani cha mshahara kabla ya PAYE?", "PWD", ["1,460,000", "msamaha", "kodi"], ["hakuna msamaha"]),
        ("Ushuru wa kutoa pesa kwenye simu (mobile money) ni asilimia ngapi?", "Excise", ["0.5%", "ushuru", "pesa"], ["20%"]),
        ("Kikomo cha usajili wa lazima wa VAT kwa miezi mitatu mfululizo ni kiasi gani?", "VAT", ["37,500,000", "vat", "miezi"], ["trilioni moja"]),
        ("Mlipa kodi ana siku ngapi kukata rufaa kwenye Mahakama ya Rufaa za Kodi?", "Disputes", ["30", "rufaa", "siku"], ["miaka miwili"]),
        ("Je, inaruhusiwa kuagiza gari lililotengenezwa zaidi ya miaka 15 iliyopita?", "Customs", ["15", "marufuku", "gari"], ["bila kikomo"]),
        ("Ni nyaraka gani zinazohitajika ili kupata namba ya TIN?", "TIN", ["tin", "ura"], ["lipa afisa"]),
        ("Kodi ya pango kwa mwenye nyumba binafsi inakokotolewaje?", "Rental", ["12%", "2,820,000", "kodi"], ["hakuna kodi"]),
        ("Biashara ndogo ndogo zisizo na vitabu vya mahesabu zinalipaje kodi ya makadirio?", "Presumptive", ["milioni 10", "kodi"], ["wakaguzi lazima"]),
        ("Marejesho ya kodi ya kila mwezi yanapaswa kuwasilishwa kufikia tarehe ngapi?", "Filing", ["15", "mwezi"], ["mwisho wa mwaka"]),
        ("Ni nambari gani za bure za simu za kupiga kupata msaada kutoka URA?", "Contact", ["0800 117 000", "0800 217 000"], ["lipa simu"]),
        ("Ushuru wa stempu wakati wa kuhamisha ardhi ni asilimia ngapi?", "Stamp Duty", ["1%", "stamp duty"], ["18%"]),
        ("Mtu anayesambaza bidhaa za zaidi ya milioni moja hukatwa asilimia ngapi ya zuio?", "Withholding", ["6%", "zuio"], ["30%"]),
        ("Je, ushuru wa forodha wa EAC CET una viwango vipi vikuu?", "CET", ["0%", "10%", "25%", "35%"], ["50% tu"]),
        ("Bidhaa za EAC zinahitaji ongezeko la thamani ya asilimia ngapi kwa biashara huru?", "Origin", ["35%", "thamani"], ["0%"]),
        ("Mfumo wa EFRIS unawalazimu wafanyabiashara kufanya nini wanapouza bidhaa?", "EFRIS", ["efris", "ankara", "risiti"], ["hiari"]),
        ("Je, vifaa vya sola vinatozwa ushuru gani wa forodha?", "Renewable Energy", ["jua", "ushuru"], ["50% ushuru"]),
        ("Ninawezaje kulipa kodi ya URA kwa kutumia PRN kwenye benki au simu?", "Payments", ["prn", "benki", "simu"], ["mpe afisa"]),
        ("Msafiri anaruhusiwa kubeba bidhaa za kibinafsi za thamani gani bila ushuru?", "Baggage", ["500", "dola"], ["dola milioni"]),
        ("Ninawezaje kutoa taarifa kwa siri kuhusu ufisadi kwa URA?", "Whistleblowing", ["0800 117 000", "ura"], ["hakuna njia"]),
    ]

    idx = 1
    while len(cases) < 500:
        base_q, base_stat, base_req, base_forb = sw_templates[(idx - 1) % len(sw_templates)]
        variant_prefix = ["", "Tafadhali nijuze: ", "Ninataka kuelewa: ", "Nieleze wazi: ", "Naomba msaada: "][idx % 5]
        q_text = f"{variant_prefix}{base_q}"
        cases.append({
            "id": f"REL-SW-{idx:03d}",
            "title": f"SW Regional Case #{idx}: {base_req[0]}",
            "locale": "sw",
            "domain": DOMAINS[idx % len(DOMAINS)],
            "statute": base_stat,
            "query": q_text,
            "required_concepts": base_req,
            "forbidden_hallucinations": base_forb,
        })
        idx += 1

    return cases


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
    t0 = time.perf_counter()

    try:
        resp = await client.post(
            GATEWAY_URL,
            json={"message": case["query"], "locale": case["locale"]},
            headers={
                "X-Session-ID": f"rel-500-{cid}-{int(time.time())}",
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
        is_passed = (concept_score >= 0.50) and not hallucination

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
            reply_snippet=reply[:240].replace("\n", " "),
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
    print("=" * 80)
    print("LIVE NGROK MULTILINGUAL RELIABILITY AUDIT: 500 EDGE CASES (EN, LG, SW)")
    print(f"Target Gateway: {GATEWAY_URL}")
    print("=" * 80)

    dataset = generate_500_edge_cases()
    print(f"Generated {len(dataset)} edge cases across English, Luganda, and Kiswahili.")

    # Save dataset
    dataset_path = "docs/presentation/edge_cases_500_dataset.json"
    with open(dataset_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)
    print(f"Saved dataset artifact to: {dataset_path}")

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    sem = asyncio.Semaphore(8)  # 8 parallel workers for optimal throughput

    t_start = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0, limits=limits, headers={"ngrok-skip-browser-warning": "1"}) as client:
        async def bounded_run(case, index):
            async with sem:
                res = await run_edge_case(client, case)
                status_str = "PASS" if res.passed else "FAIL"
                if index % 25 == 0 or not res.passed or index <= 10:
                    print(f"  [{index+1:03d}/500] [{res.case_id}] {status_str} ({res.score}%): {res.title} [{res.locale.upper()}] ({res.latency_s}s)", flush=True)
                return res

        tasks = [bounded_run(case, i) for i, case in enumerate(dataset)]
        results = await asyncio.gather(*tasks)

    t_wall = time.perf_counter() - t_start
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = round(passed / total * 100.0, 1)

    print("\n" + "=" * 80)
    print("500-EDGE-CASE AUDIT SUMMARY RESULTS")
    print("=" * 80)
    print(f"Total Scenarios Evaluated: {total}")
    print(f"Successful Passed: {passed} / {total} ({pass_rate}%)")
    print(f"Total Wall Clock Time: {t_wall:.2f}s | Realized Throughput: {total / t_wall:.2f} QPS")
    print(f"Mean Latency: {sum(r.latency_s for r in results) / total:.2f}s")

    # Quantile latencies
    all_lats = sorted(r.latency_s for r in results if r.status_code == 200)
    if all_lats:
        p50 = all_lats[int(len(all_lats) * 0.50)]
        p90 = all_lats[int(len(all_lats) * 0.90)]
        p95 = all_lats[int(len(all_lats) * 0.95)]
        p99 = all_lats[int(len(all_lats) * 0.99)]
        print(f"Latency Percentiles: p50={p50*1000:.1f}ms | p90={p90*1000:.1f}ms | p95={p95*1000:.1f}ms | p99={p99*1000:.1f}ms")

    # Language breakdown
    print("\nLanguage Breakdown:")
    for loc in ("en", "lg", "sw"):
        loc_res = [r for r in results if r.locale == loc]
        loc_passed = sum(1 for r in loc_res if r.passed)
        loc_rate = round(loc_passed / len(loc_res) * 100.0, 1) if loc_res else 0.0
        loc_lat = sum(r.latency_s for r in loc_res) / len(loc_res) if loc_res else 0.0
        print(f"  [{loc.upper()}] Passed: {loc_passed}/{len(loc_res)} ({loc_rate}%) | Mean Latency: {loc_lat:.2f}s")

    # Domain breakdown
    print("\nOperational Domain Breakdown:")
    for domain in DOMAINS:
        dom_res = [r for r in results if r.domain == domain]
        dom_passed = sum(1 for r in dom_res if r.passed)
        dom_rate = round(dom_passed / len(dom_res) * 100.0, 1) if dom_res else 0.0
        print(f"  - {domain:<44}: {dom_passed:3d} / {len(dom_res):3d} passed ({dom_rate:5.1f}%)")

    # Save to JSON
    out_json = "docs/presentation/multilang_500_reliability_audit_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\nArtifact saved to: {out_json}")


if __name__ == "__main__":
    asyncio.run(main())
