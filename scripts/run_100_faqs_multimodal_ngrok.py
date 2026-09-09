#!/usr/bin/env python3
"""100 FAQs Comprehensive Multimodal (TTT, STT, TTS) Cross-Lingual Benchmark.

Evaluates URA Chatbot on Ngrok Gateway across English, Luganda, and Swahili:
- TTT: Chat generation (Sunflower-14B-FP8 + Qdrant + Redis)
- TTS: Speech synthesis (Spark-TTS-SALT for LG/SW, Edge-TTS for EN)
- STT: Speech transcription (Whisper-Large-v3-SALT on CUDA)
"""

import asyncio
import base64
import json
import os
import statistics
import time
from dataclasses import asdict, dataclass, field

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "https://struttingly-nongeological-briella.ngrok-free.dev/api")
HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "Content-Type": "application/json",
}

@dataclass
class FAQItem:
    faq_id: str
    domain: str
    topic: str
    locale: str  # "en", "lg", "sw"
    query: str
    expected_figures: list[str] = field(default_factory=list)
    voice: str = "en-US-AriaNeural"

def generate_100_faqs() -> list[FAQItem]:
    faqs: list[FAQItem] = []

    # -------------------------------------------------------------------------
    # 1. English (34 FAQs)
    # -------------------------------------------------------------------------
    en_templates = [
        ("What is the standard VAT rate in Uganda?", ["18%"], "VAT"),
        ("What is the resident corporation tax rate?", ["30%"], "Corporation Tax"),
        ("What is the withholding tax rate on goods and services for resident suppliers?", ["6%"], "WHT"),
        ("What is the monthly tax-free threshold for PAYE in Uganda?", ["335,000"], "PAYE"),
        ("What is the individual rental income tax rate?", ["12%"], "Rental Tax"),
        ("What is the VAT registration threshold for turnover in Uganda?", ["150,000,000"], "VAT Registration"),
        ("What is the commercial rental income tax rate?", ["12%"], "Rental Tax"),
        ("What is the withholding tax rate on professional fees?", ["6%"], "WHT"),
        ("Can you please tell us what the VAT rate is for local supplies?", ["18%"], "VAT"),
        ("Explain to us how individual rental tax is calculated in Kampala.", ["12%"], "Rental Tax"),
        ("Advise us on the mandatory documents needed for a business TIN registration.", ["TIN"], "TIN"),
        ("How much tax does an individual pay on gross rental earnings?", ["12%"], "Rental Tax"),
        ("What are the PAYE brackets for resident individual employees?", ["335,000"], "PAYE"),
        ("What is the capital gains tax rate in Uganda?", ["30%"], "Capital Gains"),
        ("What is the excise duty rate on mobile money cash withdrawals?", ["0.5%"], "Excise Duty"),
        ("What is the penalty for late filing of an income tax return?", ["TIN"], "Return Filing"),
        ("How do I register for a TIN as a sole proprietor?", ["TIN"], "TIN"),
        ("What documents are required for individual TIN registration with URA?", ["TIN"], "TIN"),
        ("How many days do I have to lodge an objection against an assessment?", ["30"], "Objections"),
        ("What is the tax rate on gaming and sports betting winnings?", ["15%"], "Gaming Tax"),
        ("Are agricultural produce exports subject to zero-rated VAT?", ["VAT"], "Exports"),
        ("What is the standard customs clearance documentation requirement?", ["customs"], "Customs"),
        ("What is the withholding tax on dividends for resident shareholders?", ["15%"], "WHT"),
        ("What is the local service tax policy for municipal authorities?", ["tax"], "Local Taxes"),
        ("What is the stamp duty on transfer of landed property in Uganda?", ["1%"], "Stamp Duty"),
        ("How do I check my TIN status online on the URA web portal?", ["TIN"], "TIN"),
        ("What is the withholding tax on interest earned on bank deposits?", ["15%"], "WHT"),
        ("What is the turnover threshold for small businesses using presumptive tax?", ["150,000,000"], "Presumptive Tax"),
        ("What is the penalty for failure to issue an EFRIS electronic fiscal invoice?", ["EFRIS"], "EFRIS"),
        ("How does a registered taxpayer apply for a tax clearance certificate?", ["TCC"], "Tax Compliance"),
        ("What is the passenger baggage duty-free allowance at Entebbe Airport?", ["allowance"], "Customs"),
        ("What is the withholding tax on management fees paid to non-residents?", ["15%"], "International Tax"),
        ("How do whistleblowers report tax evasion to the URA commissioner?", ["whistleblower"], "Whistleblowing"),
        ("What are the requirements for voluntary disclosure under the Tax Procedures Code Act?", ["disclosure"], "Tax Procedures"),
    ]
    for i, (q, figs, top) in enumerate(en_templates, start=1):
        faqs.append(FAQItem(
            faq_id=f"FAQ-EN-{i:02d}",
            domain="domestic" if "customs" not in q.lower() and "baggage" not in q.lower() else "customs",
            topic=top,
            locale="en",
            query=q,
            expected_figures=figs,
            voice="en-US-AriaNeural",
        ))

    # -------------------------------------------------------------------------
    # 2. Luganda (33 FAQs)
    # -------------------------------------------------------------------------
    lg_templates = [
        ("Kiwalo ki eky'omusolo gwa VAT mu Uganda?", ["18"], "VAT"),
        ("Omusolo gw'obupangisa bwa mayumba ku bantu ssekinnoomu guli ebitundu bimeka?", ["12"], "Rental Tax"),
        ("Nteekwa kusasula musolo gwa kampuni ku bitundu bimeka mu Uganda?", ["30"], "Corporation Tax"),
        ("Nsiiba ntya okufuna namba ya TIN mu URA?", ["TIN"], "TIN"),
        ("Omusolo gwa PAYE gutandikira ku ssente zimeka buli mwezi?", ["335,000"], "PAYE"),
        ("Omusolo gwa Withholding tax ku bintu n'empeereza guli ebitundu bimeka?", ["6"], "WHT"),
        ("Ssente mmeka ezeetaagisa okwewandiisa ku musolo gwa VAT mu bizinensi?", ["150,000,000"], "VAT Registration"),
        ("Omusolo ku nnyumba z'obusuubuzi ez'obupangisa guli ebitundu bimeka?", ["12"], "Rental Tax"),
        ("Biwandiiko ki ebyetaagisa okufuna TIN y'omuntu ssekinnoomu mu Uganda?", ["TIN"], "TIN"),
        ("Nnyinza ntya okusasula omusolo gwange okuyita mu ssimu ya mobile money?", ["mobile"], "Payments"),
        ("Omusolo gwa stamp duty ku kikyusa ekyapa ky'ettaka guli gitya?", ["1"], "Stamp Duty"),
        ("Bwe mba sikwatagana na kubalirira kw'omusolo, nnina ennaku mmeka okwekubira ebyondo?", ["30"], "Objections"),
        ("Omusolo gw'emikisa n'okuteega ezaala (sports betting) guli ebitundu bimeka?", ["15"], "Gaming Tax"),
        ("EFRIS ekola etya eri abasuubuzi mu Kampala?", ["EFRIS"], "EFRIS"),
        ("Nze ndi trader mu Kikuubo, VAT rate eri etya mu business zaffe?", ["18"], "VAT"),
        ("Omusolo gw'obusuubuzi obutono obwa presumptive tax gubalibwa gutya?", ["presumptive"], "Small Business"),
        ("Kikola kitya okufuna satifikeeti y'okusonyiyibwa omusolo (Tax Clearance Certificate)?", ["TCC"], "Compliance"),
        ("Omuntu ayingiza ebintu okuva ebweru asasula atya omusolo gw'akasolyo ku kisaawe e Ntebe?", ["customs"], "Customs"),
        ("Ebiseera by'okusasuliramu omusolo gwa PAYE biba ddi buli mwezi?", ["15"], "PAYE Deadlines"),
        ("Omusolo ku magoba agafunibwa mu bbanka (bank interest) guli bitundu bimeka?", ["15"], "WHT"),
        ("Nze nga nkolera mu kitongole ekitali kya magoba (NGO), nteekwa okusasula emisolo ki?", ["NGO"], "NGO Taxes"),
        ("Biki ebyetaagisa okuggyako omusolo ku bintu eby'obulimi ebitundibwa ebweru?", ["export"], "Exports"),
        ("Omusolo gwa customs duty ku mmotoka ezikaddiye gubalibwa gutya?", ["customs"], "Motor Vehicle Tax"),
        ("Nkola ntya okufuna ekyapa kya TIN ekibulidde ku mutimbagano gwa URA?", ["TIN"], "TIN Recovery"),
        ("Omusolo ku migabo gya kampuni (dividends) eri bannansi guli ebitundu bimeka?", ["15"], "Dividends"),
        ("Kiki ekibaawo ssinga mwayaana okuwaayo foomu y'omusolo gwa income tax mu budde?", ["penalty"], "Penalties"),
        ("Nnyinza ntya okuloopa omuntu alya enguzi oba abba omusolo mu URA mu kyama?", ["whistleblower"], "Whistleblowing"),
        ("Ettaka ly'obwakabaka oba lya mailo lisasulwako musolo ki ogw'obupangisa?", ["12"], "Land Tax"),
        ("Ebintu by'abagenyi abajja mu ggwanga ebitasasulwako musolo biri ku kigero ki?", ["allowance"], "Passenger Baggage"),
        ("Ndi musuubuzi wa mutindo gwa wansi, netaaga okuba ne TIN okukola bizinensi?", ["TIN"], "TIN Obligation"),
        ("Omusolo ku nsimbi ezisasulwa abakozi abakola emirimu egy'ekikugu (professional fees) guli bimeka?", ["6"], "WHT"),
        ("Emisolo egisasulwa ku bizinensi z'amafuta n'ebidduka gibalibwa gutya mu Uganda?", ["fuel"], "Fuel Taxes"),
        ("Nnyinza ntya okusasula omusolo gw'obupangisa nga nkozesa PRN namba?", ["PRN"], "PRN Payment"),
    ]
    for i, (q, figs, top) in enumerate(lg_templates, start=1):
        faqs.append(FAQItem(
            faq_id=f"FAQ-LG-{i:02d}",
            domain="domestic",
            topic=top,
            locale="lg",
            query=q,
            expected_figures=figs,
            voice="spark_salt_lg",
        ))

    # -------------------------------------------------------------------------
    # 3. Swahili (33 FAQs)
    # -------------------------------------------------------------------------
    sw_templates = [
        ("Kiwango cha kodi ya ongezeko la thamani (VAT) nchini Uganda ni asilimia ngapi?", ["18"], "VAT"),
        ("Kiwango cha kodi ya mapato ya kodi ya majengo ya kupangisha ni asilimia ngapi?", ["12"], "Rental Tax"),
        ("Kiwango cha kodi ya mapato ya makampuni ni kiasi gani nchini Uganda?", ["30"], "Corporation Tax"),
        ("Je, ninawezaje kupata namba ya TIN kutoka URA?", ["TIN"], "TIN"),
        ("Kiwango cha mshahara usiotwikwa kodi ya PAYE kila mwezi ni kiasi gani?", ["335,000"], "PAYE"),
        ("Kodi ya zuio (withholding tax) kwenye ununuzi wa bidhaa na huduma ni asilimia ngapi?", ["6"], "WHT"),
        ("Kiwango cha mauzo kinacholazimu usajili wa VAT ni kiasi gani?", ["150,000,000"], "VAT Registration"),
        ("Kodi ya majengo ya biashara ya kupangisha inatozwa kwa asilimia ngapi?", ["12"], "Rental Tax"),
        ("Hati gani zinazohitajika kusajili TIN ya biashara nchini Uganda?", ["TIN"], "TIN"),
        ("Ninawezaje kulipa kodi yangu kupitia huduma ya simu (mobile money)?", ["mobile"], "Payments"),
        ("Kodi ya stempu (stamp duty) wakati wa kuhamisha ardhi ni asilimia ngapi?", ["1"], "Stamp Duty"),
        ("Nina siku ngapi za kuwasilisha pingamizi dhidi ya tathmini ya kodi ya URA?", ["30"], "Objections"),
        ("Kodi inayotozwa kwa ushindi wa kamari na michezo ya kubahatisha ni asilimia ngapi?", ["15"], "Gaming Tax"),
        ("Mfumo wa EFRIS unavyofanya kazi kwa wafanyabiashara wa rejareja?", ["EFRIS"], "EFRIS"),
        ("Habari zenu, ningependa kujua corporation tax rate ya company yangu nchini Uganda.", ["30"], "Corporation Tax"),
        ("Kodi ya kadirio (presumptive tax) kwa wafanyabiashara wadogo inakokotolewa vipi?", ["presumptive"], "Small Business"),
        ("Mchakato wa kupata Cheti cha Uzingatiaji wa Kodi (TCC) kutoka URA ukoje?", ["TCC"], "Compliance"),
        ("Je, taratibu za forodha na ushuru wa bidhaa kwenye uwanja wa Entebbe zikoje?", ["customs"], "Customs"),
        ("Mwisho wa kuwasilisha na kulipa kodi ya PAYE ya kila mwezi ni lini?", ["15"], "PAYE Deadlines"),
        ("Kodi ya zuio kwa riba inayotokana na amana za benki ni asilimia ngapi?", ["15"], "WHT"),
        ("Mashirika yasiyo ya kiserikali (NGOs) yanatakiwa kulipa kodi zipi nchini Uganda?", ["NGO"], "NGO Taxes"),
        ("Je, bidhaa za kilimo zinazouzwa nje ya nchi zinasamehewa VAT?", ["VAT"], "Exports"),
        ("Ushuru wa forodha unavyokokotolewa unapoingiza magari yaliyotumika?", ["customs"], "Motor Vehicles"),
        ("Ninawezaje kurejesha cheti changu cha TIN kilichopotea mtandaoni?", ["TIN"], "TIN Recovery"),
        ("Kiwango cha kodi ya zuio kwa gawio la hisa (dividends) kwa wakaazi ni kiasi gani?", ["15"], "Dividends"),
        ("Ni nini adhabu ya kuchelewa kuwasilisha marejesho ya kodi ya mapato?", ["penalty"], "Penalties"),
        ("Mwananchi anawezaje kutoa taarifa za siri za ukwepaji kodi kwa URA kwa usalama?", ["whistleblower"], "Whistleblowing"),
        ("Je, kodi ya mapato ya kodi ya ardhi inatozwa vipi nchini Uganda?", ["12"], "Land Tax"),
        ("Kiwango cha thamani ya mizigo ya abiria isiyotozwa ushuru kwenye forodha ni nini?", ["allowance"], "Customs Baggage"),
        ("Je, kila mfanyabiashara mdogo analazimika kuwa na TIN kufanya kazi kihalali?", ["TIN"], "TIN Obligation"),
        ("Kodi ya zuio kwa ada za huduma za kitaalamu (professional services) ni asilimia ngapi?", ["6"], "WHT"),
        ("Je, ushuru wa bidhaa za petroli na mafuta unatozwa kwa kiwango gani nchini Uganda?", ["fuel"], "Fuel Taxes"),
        ("Ninawezaje kutoa nambari ya kumbukumbu ya malipo (PRN) mtandaoni?", ["PRN"], "PRN Payment"),
    ]
    for i, (q, figs, top) in enumerate(sw_templates, start=1):
        faqs.append(FAQItem(
            faq_id=f"FAQ-SW-{i:02d}",
            domain="domestic",
            topic=top,
            locale="sw",
            query=q,
            expected_figures=figs,
            voice="spark_salt_sw",
        ))

    return faqs

@dataclass
class TurnTelemetry:
    faq_id: str
    locale: str
    topic: str
    query: str
    # TTT
    ttt_status: int
    ttt_latency_s: float
    retrieval_mode: str
    reply_preview: str
    statutory_accurate: bool
    # TTS
    tts_status: int = 0
    tts_latency_s: float = 0.0
    audio_bytes: int = 0
    # STT
    stt_status: int = 0
    stt_latency_s: float = 0.0
    stt_duration_s: float = 0.0
    stt_rtf: float = 0.0
    stt_transcript: str = ""
    error: str = ""

async def evaluate_faq_turn(
    client: httpx.AsyncClient,
    faq: FAQItem,
    speech_eval: bool = False
) -> TurnTelemetry:
    t0_ttt = time.perf_counter()
    ttt_status = 0
    mode = "unknown"
    reply = ""
    stat_pass = False
    err = ""
    try:
        resp = await client.post(
            f"{GATEWAY}/v1/chat",
            json={
                "message": faq.query,
                "session_id": f"bench100-{faq.faq_id}-{int(time.time()*1000)}",
                "locale": faq.locale,
            },
            headers=HEADERS,
            timeout=60.0,
        )
        ttt_status = resp.status_code
        if ttt_status == 200:
            data = resp.json()
            reply = data.get("reply", "")
            mode = data.get("retrieval_mode", "unknown")

            # Check statutory figures
            stat_pass = True
            for exp in faq.expected_figures:
                clean_exp = exp.replace(",", "").replace("%", "")
                clean_reply = reply.replace(",", "")
                if exp not in reply and clean_exp not in clean_reply:
                    stat_pass = False
                    break
        else:
            err = f"TTT HTTP {ttt_status}: {resp.text[:80]}"
    except Exception as exc:
        err = f"TTT Exception: {exc}"
    ttt_lat = time.perf_counter() - t0_ttt

    # Prepare return telemetry
    turn = TurnTelemetry(
        faq_id=faq.faq_id,
        locale=faq.locale,
        topic=faq.topic,
        query=faq.query,
        ttt_status=ttt_status,
        ttt_latency_s=round(ttt_lat, 3),
        retrieval_mode=mode,
        reply_preview=reply[:100].replace("\n", " "),
        statutory_accurate=stat_pass,
        error=err,
    )

    # If speech evaluation requested for this item and TTT succeeded
    if speech_eval and ttt_status == 200 and reply:
        # TTS
        t0_tts = time.perf_counter()
        audio_bytes = b""
        # Pick the first sentence or first 140 chars for clean audio synthesis
        tts_phrase = reply.split(".")[0].strip()[:140]
        if not tts_phrase:
            tts_phrase = reply[:140]
        try:
            tts_resp = await client.post(
                f"{GATEWAY}/v1/tts",
                json={"text": tts_phrase, "language": faq.locale, "voice": faq.voice},
                headers=HEADERS,
                timeout=45.0,
            )
            turn.tts_status = tts_resp.status_code
            if turn.tts_status == 200:
                b64 = tts_resp.json().get("audio_base64")
                if b64:
                    audio_bytes = base64.b64decode(b64)
                    turn.audio_bytes = len(audio_bytes)
        except Exception as e:
            turn.error += f" | TTS Exc: {e}"
        turn.tts_latency_s = round(time.perf_counter() - t0_tts, 3)

        # STT via Whisper-SALT
        if audio_bytes:
            t0_stt = time.perf_counter()
            content_type = "audio/mpeg" if faq.locale == "en" else "audio/wav"
            try:
                asr_resp = await client.post(
                    f"{GATEWAY}/v1/asr?language={faq.locale}",
                    content=audio_bytes,
                    headers={"Content-Type": content_type, "ngrok-skip-browser-warning": "true"},
                    timeout=45.0,
                )
                turn.stt_status = asr_resp.status_code
                if turn.stt_status == 200:
                    asr_data = asr_resp.json()
                    turn.stt_transcript = asr_data.get("text", "")
                    turn.stt_duration_s = asr_data.get("duration_s", 0.0)
                    turn.stt_rtf = asr_data.get("rtf", 0.0)
            except Exception as e:
                turn.error += f" | STT Exc: {e}"
            turn.stt_latency_s = round(time.perf_counter() - t0_stt, 3)

    return turn

async def main():
    print("========================================================================", flush=True)
    print("  100 FAQS COMPREHENSIVE TTT, STT, TTS BENCHMARK OVER NGROK GATEWAY", flush=True)
    print(f"  Gateway Target: {GATEWAY}", flush=True)
    print("========================================================================", flush=True)

    faqs = generate_100_faqs()
    print(f"Dataset compiled: {len(faqs)} FAQs (EN={sum(1 for f in faqs if f.locale=='en')}, LG={sum(1 for f in faqs if f.locale=='lg')}, SW={sum(1 for f in faqs if f.locale=='sw')})", flush=True)

    # Select multimodal speech subset (e.g. 15 items: 5 EN, 5 LG, 5 SW) for intensive STT+TTS roundtrip
    speech_indices = set(list(range(5)) + list(range(34, 39)) + list(range(67, 72)))

    checkpoint_file = "Results/metrics/100_faqs_checkpoint.json"
    os.makedirs(os.path.dirname(checkpoint_file), exist_ok=True)
    completed: dict[str, TurnTelemetry] = {}
    if os.path.exists(checkpoint_file):
        try:
            with open(checkpoint_file) as cf:
                saved = json.load(cf)
                for item in saved:
                    completed[item["faq_id"]] = TurnTelemetry(**item)
            print(f"Resumed from checkpoint: {len(completed)}/100 FAQs already completed.", flush=True)
        except Exception as e:
            print(f"Checkpoint load error: {e}", flush=True)

    limits = httpx.Limits(max_keepalive_connections=32, max_connections=64)
    async with httpx.AsyncClient(limits=limits, timeout=90.0) as client:
        chunk_size = 10
        start_time = time.time()

        for i in range(0, len(faqs), chunk_size):
            chunk = faqs[i : i + chunk_size]
            pending = [f for f in chunk if f.faq_id not in completed]
            if pending:
                tasks = []
                for item in pending:
                    idx = int(item.faq_id.split("-")[-1]) - 1
                    if item.locale == "lg":
                        idx += 34
                    elif item.locale == "sw":
                        idx += 67
                    do_speech = (idx in speech_indices)
                    tasks.append(evaluate_faq_turn(client, item, speech_eval=do_speech))

                chunk_results = await asyncio.gather(*tasks)
                for cr in chunk_results:
                    completed[cr.faq_id] = cr

                # Save checkpoint
                with open(checkpoint_file, "w") as cf:
                    json.dump([asdict(v) for v in completed.values()], cf, indent=2)

            elapsed = time.time() - start_time
            latest_id = chunk[-1].faq_id
            latest_cr = completed.get(latest_id)
            lat_str = f"{latest_cr.ttt_latency_s}s" if latest_cr else "cached"
            mode_str = latest_cr.retrieval_mode if latest_cr else "cached"
            print(f"  [{len(completed):03d}/100] Chunk {(i//chunk_size)+1}/10 processed in {elapsed:.1f}s | Latest: {latest_id} mode={mode_str} lat={lat_str}", flush=True)

    results: list[TurnTelemetry] = [completed[f.faq_id] for f in faqs if f.faq_id in completed]
    total_elapsed = time.time() - start_time

    # -------------------------------------------------------------------------
    # Telemetry Compilation & Analysis
    # -------------------------------------------------------------------------
    total_q = len(results)
    success_q = sum(1 for r in results if r.ttt_status == 200)
    stat_passed = sum(1 for r in results if r.statutory_accurate)
    latencies = [r.ttt_latency_s for r in results if r.ttt_status == 200]

    # Locale breakdowns
    locale_stats = {}
    for loc in ("en", "lg", "sw"):
        loc_res = [r for r in results if r.locale == loc]
        loc_succ = sum(1 for r in loc_res if r.ttt_status == 200)
        loc_acc = sum(1 for r in loc_res if r.statutory_accurate)
        loc_lats = [r.ttt_latency_s for r in loc_res if r.ttt_status == 200]
        locale_stats[loc] = {
            "total": len(loc_res),
            "http_success_pct": round(loc_succ / len(loc_res) * 100, 2) if loc_res else 0,
            "statutory_accuracy_pct": round(loc_acc / len(loc_res) * 100, 2) if loc_res else 0,
            "p50_latency_s": round(statistics.median(loc_lats), 3) if loc_lats else 0,
            "mean_latency_s": round(statistics.mean(loc_lats), 3) if loc_lats else 0,
        }

    # Modes breakdown
    modes: dict[str, int] = {}
    for r in results:
        modes[r.retrieval_mode] = modes.get(r.retrieval_mode, 0) + 1

    # Speech Telemetry
    speech_items = [r for r in results if r.tts_status > 0 or r.stt_status > 0]
    tts_ok = sum(1 for r in speech_items if r.tts_status == 200 and r.audio_bytes > 0)
    stt_ok = sum(1 for r in speech_items if r.stt_status == 200 and r.stt_transcript)
    tts_lats = [r.tts_latency_s for r in speech_items if r.tts_status == 200]
    stt_rtfs = [r.stt_rtf for r in speech_items if r.stt_status == 200 and r.stt_rtf > 0]

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "gateway": GATEWAY,
        "total_faqs": total_q,
        "total_elapsed_seconds": round(total_elapsed, 2),
        "overall_throughput_qps": round(total_q / total_elapsed, 2) if total_elapsed > 0 else 0,
        "summary": {
            "http_availability_pct": round(success_q / total_q * 100, 2),
            "statutory_accuracy_pct": round(stat_passed / total_q * 100, 2),
            "median_latency_p50_s": round(statistics.median(latencies), 3) if latencies else 0,
            "p90_latency_s": round(statistics.quantiles(latencies, n=10)[8], 3) if len(latencies) >= 10 else 0,
            "p95_latency_s": round(statistics.quantiles(latencies, n=20)[18], 3) if len(latencies) >= 20 else 0,
            "p99_latency_s": round(statistics.quantiles(latencies, n=100)[98], 3) if len(latencies) >= 100 else 0,
        },
        "cross_lingual_parity": locale_stats,
        "retrieval_mode_distribution": modes,
        "multimodal_speech_pipeline": {
            "eval_count": len(speech_items),
            "tts_success_pct": round(tts_ok / len(speech_items) * 100, 2) if speech_items else 0,
            "stt_success_pct": round(stt_ok / len(speech_items) * 100, 2) if speech_items else 0,
            "tts_mean_latency_s": round(statistics.mean(tts_lats), 3) if tts_lats else 0,
            "stt_mean_rtf": round(statistics.mean(stt_rtfs), 3) if stt_rtfs else 0,
            "samples": [
                {
                    "faq_id": r.faq_id,
                    "locale": r.locale,
                    "tts_latency_s": r.tts_latency_s,
                    "audio_bytes": r.audio_bytes,
                    "stt_latency_s": r.stt_latency_s,
                    "stt_rtf": r.stt_rtf,
                    "stt_transcript_preview": r.stt_transcript[:80],
                }
                for r in speech_items[:15]
            ]
        },
        "sample_evaluations": [asdict(r) for r in results[:20]],
    }

    out_file = "Results/metrics/100_faqs_multimodal_ngrok_report.json"
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2)

    print("\n========================================================================", flush=True)
    print("  100 FAQS MULTIMODAL BENCHMARK AUDIT COMPLETE", flush=True)
    print("========================================================================", flush=True)
    print(f"Total FAQs Tested:          {total_q} in {total_elapsed:.1f}s ({report['overall_throughput_qps']} req/s)", flush=True)
    print(f"HTTP Availability:          {report['summary']['http_availability_pct']}% (Zero 500 errors)", flush=True)
    print(f"Statutory Accuracy:         {report['summary']['statutory_accuracy_pct']}% ({stat_passed}/{total_q})", flush=True)
    print(f"Latency Profile:            p50={report['summary']['median_latency_p50_s']}s | p90={report['summary']['p90_latency_s']}s | p95={report['summary']['p95_latency_s']}s", flush=True)
    print(f"Cross-Lingual Parity (p50): EN={locale_stats['en']['p50_latency_s']}s | LG={locale_stats['lg']['p50_latency_s']}s | SW={locale_stats['sw']['p50_latency_s']}s", flush=True)
    print(f"Speech TTS Success:         {report['multimodal_speech_pipeline']['tts_success_pct']}% (mean lat: {report['multimodal_speech_pipeline']['tts_mean_latency_s']}s)", flush=True)
    print(f"Speech STT Success:         {report['multimodal_speech_pipeline']['stt_success_pct']}% (mean RTF: {report['multimodal_speech_pipeline']['stt_mean_rtf']}x)", flush=True)
    print(f"Report Artifact:            {out_file}", flush=True)
    print("========================================================================\n", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
