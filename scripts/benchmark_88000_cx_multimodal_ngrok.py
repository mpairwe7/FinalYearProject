#!/usr/bin/env python3
"""88,000 Complex URA CX Scenarios (TTT, STT, TTS) Across EN, LG, SW, NYN, ACH
Ultra-Scale Stress, Fuzzy, Volume & Live Ngrok Load Benchmark (Including G253–G265 Enhancements).

Evaluates:
- 88,000 complex URA Customer Experience (CX) scenarios balanced across:
  * Languages (17,600 each): English (en), Luganda (lg), Kiswahili (sw), Runyankole (nyn), Acholi (ach)
  * Modalities: TTT (44,000), STT (26,400), TTS (17,600)
- 6 Core Evaluation Dimensions across the 88,000 scenarios:
  1. Long-Horizon Reasoning, Session/Context/Memory, G265 WYSIWYS & Inline Prepare, Portal Co-Pilot & Calculators (17,600 scenarios)
  2. Emotional Intelligence (EQ), 5-Locale Empathy/Relief Tool-RAG, Safety & CXP Business-Hours Escalation (17,600 scenarios)
  3. Conversational Rate, Natural Civic Intelligence & Abbreviation-Safe Structuring (13,200 scenarios)
  4. Intent & Objective Identification, 8-Family URA Error Diagnostics, Strict Select Validation & Screenshot Analysis (13,200 scenarios)
  5. STT / ASR Spoken Entity Repair, Vernacular Lexicon & Endpointing (13,200 scenarios)
  6. TTS / Call Receptionist Issue Resolution, 5-Locale Contact/USSD/URL Pronunciation & Multi-Turn Control (13,200 scenarios)
- Live Ngrok Stress, Fuzzy, Volume & Concurrent Load Suite against:
  https://struttingly-nongeological-briella.ngrok-free.dev
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import websockets

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "App" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("VLLM_API_BASE", "http://127.0.0.1:8011/v1")
os.environ.setdefault("LLM_BACKEND", "vllm")
os.environ.setdefault("ORPHEUS_TTS_URL", "http://127.0.0.1:18100")
os.environ.setdefault("ORPHEUS_TTS_LANGUAGES", "lg,sw,en,nyn,ach")

from app import mt, orpheus_tts  # noqa: E402
from app.agents import supervisor  # noqa: E402
from app.calculator_router import (  # noqa: E402
    extract_amounts,
    format_calc_reply,
    plan_calculation,
)
from app.conversational import handle_conversational_turn  # noqa: E402
from app.documents import diagnose_portal_screenshot  # noqa: E402
from app.escalation_policy import (  # noqa: E402
    after_hours_escalation_message,
    after_hours_escalation_notice,
    assess_escalation,
    evaluate_cxp_schedule,
)
from app.guardrails import OutputGuard  # noqa: E402
from app.language_state import resolve_turn_locale  # noqa: E402
from app.mcp.tool_rag import ToolRAGSelector  # noqa: E402
from app.memory.resolution_ledger import objective_for  # noqa: E402
from app.number_words import ach_words, nyn_words  # noqa: E402
from app.officer_handoff import prune_required_details  # noqa: E402
from app.portal_copilot import (  # noqa: E402
    PORTAL_TASK_CATALOG,
    analyze_portal_context,
    build_portal_task_card,
    compute_displayed_fields_sha256,
    compute_estimated_liability_ugx,
    detect_portal_task_intent,
    diagnose_ura_error,
    extract_task_entities,
    validate_task_fields,
)
from app.query import (  # noqa: E402
    canonicalize_tax_terms,
    detect_language,
    english_retrieval_query,
)
from app.query import (  # noqa: E402
    normalize as normalize_query,
)
from app.receptionist.clarify import classify_yes_no, entity_mishear  # noqa: E402
from app.receptionist.hybrid_s2s import compute_semantic_endpoint_silence_ms  # noqa: E402
from app.receptionist.lexicon import normalize_call_query, repair_asr_entities  # noqa: E402
from app.receptionist.phrases import phrase  # noqa: E402
from app.receptionist.spoken_figure_guard import (  # noqa: E402
    inject_dictation_pauses,
    verify_spoken_figures,
)
from app.speech_normalization import (  # noqa: E402
    clean_text_for_speech,
    verbalize_digits_for_tts,
)
from app.text_signals import (  # noqa: E402
    detect_crisis,
    detect_high_stakes,
    detect_user_distress,
    get_farewell_reply,
    get_gratitude_reply,
    get_greeting_reply,
)
from app.tools import ToolRegistry  # noqa: E402
from app.workflows.registry import WorkflowRegistry, auto_load_flows  # noqa: E402

NGROK_HTTP = os.environ.get(
    "NGROK_BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev"
).rstrip("/")
NGROK_WS = NGROK_HTTP.replace("https://", "wss://").replace("http://", "ws://") + "/api/v1/calls/stream"
ORPHEUS_LOCAL = os.environ.get("ORPHEUS_TTS_URL", "http://127.0.0.1:18100")
HEADERS = {
    "ngrok-skip-browser-warning": "true",
    "X-Voice-Consent": "true",
    "Origin": NGROK_HTTP,
}
LANGUAGES = ("en", "lg", "sw", "nyn", "ach")

mt.cache = mt._TranslationCache(mt.MT_CACHE_SIZE)
auto_load_flows(REPO_ROOT / "App" / "backend" / "app" / "workflows" / "flows")


def pcm24k_to_pcm16k(raw_pcm24k: bytes) -> bytes:
    """Resample 24 kHz 16-bit PCM bytes to 16 kHz 16-bit PCM bytes."""
    pcm24 = np.frombuffer(raw_pcm24k, dtype="<i2").astype(np.float32)
    idx = np.arange(0, len(pcm24), 1.5)
    pcm16 = np.interp(idx, np.arange(len(pcm24)), pcm24).astype("<i2")
    return pcm16.tobytes()


def pct(values: list[float], q: float) -> float:
    """Compute quantile *q* over *values*."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(q * len(s))))
    return round(s[idx], 3)


# ---------------------------------------------------------------------------
# 1. Formulate 88,000 Complex URA CX Scenarios across EN, LG, SW, NYN, ACH
# ---------------------------------------------------------------------------
def build_88000_scenarios() -> list[dict[str, Any]]:
    """Build 88,000 complex URA CX scenarios (17,600 per language across en, lg, sw, nyn, ach)."""
    scenarios: list[dict[str, Any]] = []
    sid = 1

    # Dimension 1: Long-Horizon Reasoning, Session/Context/Memory, G265 WYSIWYS/Prepare & Expanded Calculators
    # (3,520 per lang = 17,600 total, modality=ttt)
    lh_templates: dict[str, list[tuple[str, str, str, float, str]]] = {
        "en": [
            ("Calculate PAYE on a monthly gross salary of UGX 4,500,000 including NSSF and LST", "calculate_paye", "tin_registration", 4_500_000.0, "payment_generate_prn"),
            ("Calculate VAT at 18% on taxable supplies of UGX 65,000,000", "calculate_vat", "vat_return", 65_000_000.0, "return_filing_submit"),
            ("Compute 10% withholding tax on an insurance agent commission of UGX 20,000,000", "calculate_withholding", "wht", 20_000_000.0, "tin_search_verify"),
            ("Calculate corporation tax for a non-resident branch with chargeable income of UGX 35,000,000", "calculate_corporation_tax", "corporate_tax", 35_000_000.0, "tax_clearance_apply"),
            ("Calculate excise duty on 500 kg of sugar sold at UGX 2,500,000", "calculate_excise_duty", "vat_return", 2_500_000.0, "payment_generate_prn"),
        ],
        "lg": [
            ("Omusolo gwa PAYE ku musaala gwa shilingi 4,500,000", "calculate_paye", "tin_registration", 4_500_000.0, "payment_generate_prn"),
            ("Omusolo gwa VAT ku shilingi 65,000,000 guba mmeka?", "calculate_vat", "vat_return", 65_000_000.0, "return_filing_submit"),
            ("Omusolo gwa PAYE ku shilingi obukadde 12", "calculate_paye", "paye", 12_000_000.0, "tin_search_verify"),
            ("Omusolo gwa VAT ku shilingi obukadde 25", "calculate_vat", "vat_return", 25_000_000.0, "tax_clearance_apply"),
        ],
        "sw": [
            ("Kokotoa kodi ya PAYE kwa mshahara wa mwezi wa shilingi 4,500,000", "calculate_paye", "tin_registration", 4_500_000.0, "payment_generate_prn"),
            ("Kodi ya VAT kwa mauzo ya shilingi 65,000,000 ni kiasi gani?", "calculate_vat", "vat_return", 65_000_000.0, "return_filing_submit"),
            ("Kokotoa PAYE kwa mshahara wa shilingi milioni 12", "calculate_paye", "paye", 12_000_000.0, "tin_search_verify"),
            ("Kokotoa VAT kwa shilingi milioni 30", "calculate_vat", "vat_return", 30_000_000.0, "tax_clearance_apply"),
        ],
        "nyn": [
            ("Omushoro gwa VAT aha shiringi 65,000,000", "calculate_vat", "tin_registration", 65_000_000.0, "payment_generate_prn"),
            ("Omushoro gwa PAYE aha mushaara gwa shiringi 4,500,000", "calculate_paye", "paye", 4_500_000.0, "return_filing_submit"),
            ("Omushoro gwa VAT aha shiringi obukaikuru 15", "calculate_vat", "vat_return", 15_000_000.0, "tin_search_verify"),
            ("Omushoro gwa PAYE aha shiringi obukaikuru 8", "calculate_paye", "paye", 8_000_000.0, "tax_clearance_apply"),
        ],
        "ach": [
            ("Mucoro me VAT i kom cilingi 65,000,000", "calculate_vat", "tin_registration", 65_000_000.0, "payment_generate_prn"),
            ("Mucoro me PAYE i kom mucara me cilingi 4,500,000", "calculate_paye", "paye", 4_500_000.0, "return_filing_submit"),
            ("Mucoro me VAT i kom cilingi milioni 20", "calculate_vat", "vat_return", 20_000_000.0, "tin_search_verify"),
            ("Mucoro me PAYE i kom cilingi milioni 6", "calculate_paye", "paye", 6_000_000.0, "tax_clearance_apply"),
        ],
    }

    # Dimension 2: Emotional Intelligence, 5-Locale Empathy/Relief Tool-RAG & CXP Hours
    # (3,520 per lang = 17,600 total, modality=ttt)
    eq_templates: dict[str, list[tuple[str, str]]] = {
        "en": [
            ("URA closed my shop and locked my bank account, I cannot pay my workers", "urgent"),
            ("I received a third party agency notice freezing my account and I am terrified", "urgent"),
            ("I want to end my life because of this tax debt", "crisis"),
            ("There is nothing left to live for after my business collapsed from tax penalties", "crisis"),
            ("I am very frustrated and confused about this VAT penalty assessment", "offer"),
        ],
        "lg": [
            ("Edduuka lyange baligadde era bbanka yange basibidde akawunti, ntidde nnyo", "offer"),
            ("Njagala okwetta olw'ebanja ly'omusolo lino", "crisis"),
            ("Ebintu byange babikutte mu customs era neraliikiridde nnyo", "offer"),
            ("Ensonga y'omusolo eno nkoye era nyiize olw'ekibonerezo kinene", "offer"),
        ],
        "sw": [
            ("Duka langu limefungwa na akaunti imefungwa, nina hofu kubwa", "offer"),
            ("Nataka kujiua kwa sababu ya deni hili la kodi", "crisis"),
            ("Bidhaa zimekamatwa forodhani na nina wasiwasi sana", "offer"),
            ("Sielewi na faini ni kubwa sana kuhusu adhabu hii ya VAT", "offer"),
        ],
        "nyn": [
            ("Tihaine sente kandi ebyangye babikwata, ntiire munonga", "offer"),
            ("Ninyenda okweyita ahabw'ebanja ry'omushoro eri", "crisis"),
            ("Ebyangye babikwata omu customs kandi nyine obwoba bwingi", "offer"),
            ("Tinyetegyereize kandi ekibonerezo kihango munonga", "offer"),
        ],
        "ach": [
            ("Cente peke dok jami na gimako, alworo matek", "offer"),
            ("Amito neko koma pi banya me mucoro man", "crisis"),
            ("Jami na gimako i customs dok cwinya cwer matek", "offer"),
            ("Pe aniang dok pwod madit matek i kom mucoro man", "offer"),
        ],
    }

    # Dimension 3: Conversational Rate, Natural Civic Intelligence & Paragraph Structuring
    # (2,640 per lang = 13,200 total, modality=ttt (1,760) / tts (880))
    conv_templates: dict[str, list[tuple[str, str]]] = {
        "en": [
            ("Why do we pay taxes in Uganda?", "civic_philosophy"),
            ("Who are you and what can you do?", "identity_capability"),
            ("Are you there?", "presence_check"),
            ("I am scared of starting a business", "business_empathy"),
        ],
        "lg": [
            ("Lwaki tusasula omusolo mu Uganda?", "civic_philosophy"),
            ("Ggwe ani era osobola okunkolera ki?", "identity_capability"),
            ("Oli awo?", "presence_check"),
            ("Ntya okutandika bizinensi olw'emisolo", "business_empathy"),
        ],
        "sw": [
            ("Kwa nini tunalipa kodi nchini Uganda?", "civic_philosophy"),
            ("Wewe ni nani na unaweza kunisaidia nini?", "identity_capability"),
            ("Bado upo?", "presence_check"),
            ("Nina hofu ya kuanza biashara", "business_empathy"),
        ],
        "nyn": [
            ("Ahabw'enki nitushashura omushoro?", "civic_philosophy"),
            ("Ori oha kandi noobaasa kunkorera ki?", "identity_capability"),
            ("Okyariho?", "presence_check"),
            ("Nintina kutandika obushuubuzi", "business_empathy"),
        ],
        "ach": [
            ("Pingo waculo mucoro?", "civic_philosophy"),
            ("In ibedo nga dok itwero loko Leb Acoli?", "identity_capability"),
            ("Pud itye?", "presence_check"),
            ("Alworo cako biacara", "business_empathy"),
        ],
    }

    # Dimension 4: Intent & Objective Identification, 8-Family URA Error Diagnostics & Portal Screenshot Analysis
    # (2,640 per lang = 13,200 total, modality=stt)
    intent_templates: dict[str, list[tuple[str, str]]] = {
        "en": [
            ("How do I register to get an individual TIN on the URA web portal?", "tin_registration"),
            ("What is the standard VAT rate in Uganda and when must I register?", "vat"),
            ("How do I generate a Payment Registration Number PRN to pay tax by mobile money?", "payments"),
            ("How do I clear imported goods and motor vehicles through customs?", "customs"),
        ],
        "lg": [
            ("Nnyamba okufuna TIN yange ey'obuntu, nkoze ntya?", "tin_registration"),
            ("Omusolo gwa PAYE gubalibwa gutya?", "paye"),
            ("EFRIS ekozesebwa etya ku masannyalaze n'ebyamaguzi?", "efris"),
            ("Nkoze ntya okukyusa ebyapa by'emmotoka?", "motor_vehicle"),
        ],
        "sw": [
            ("Jinsi ya kuomba TIN ya kibinafsi?", "tin_registration"),
            ("Kodi ya ongezeko la thamani VAT ni asilimia ngapi?", "vat"),
            ("Jinsi ya kutengeneza nambari ya PRN ili kulipa kodi benki au kwa simu?", "payments"),
            ("Mwongozo wa kusafisha mizigo forodha?", "customs"),
        ],
        "nyn": [
            ("Nimbaasa kwehandiikisa nta kutunga TIN?", "tin_registration"),
            ("Omushoro gwa VAT omuri Uganda guri aha bicweka bingahi?", "vat"),
            ("Nimbaasa kukora nta enamba ya PRN okushashura omushoro?", "payments"),
            ("EFRIS neekoresibwa eta?", "efris"),
        ],
        "ach": [
            ("Atwero coyo nyinga nining me nongo TIN?", "tin_registration"),
            ("Wel mucoro me VAT i Uganda tye adi?", "vat"),
            ("Atwero yubo namba me PRN nining me culu mucoro?", "payments"),
            ("EFRIS tiyo nining?", "efris"),
        ],
    }

    # Dimension 5: STT / ASR Spoken Entity Repair, Vernacular Lexicon & Endpointing
    # (2,640 per lang = 13,200 total, modality=stt)
    stt_templates: dict[str, list[str]] = {
        "en": [
            "How do I register on efurisi for viati and paye?",
            "My TIN is one zero zero zero two three four five six and I need a TCC",
            "What is the withholding tax rate on consultancy invoices?",
            "How do I object to an assessment if my teen is",
        ],
        "lg": [
            "Nnyinza ntya okukozesa efurisi okuwandiisa viati ne payi?",
            "Nnyamba okufuna ttiini yange ku mukutu gwa URA",
            "Omusolo gwa viati guli ku bitundu bimeka?",
            "Bwe mba njagala okusasula omusolo gwa",
        ],
        "sw": [
            "Ninawezaje kutumia efurisi kusajili viati na paye?",
            "Nahitaji nambari ya tini na cheti cha TCC kutoka URA",
            "Kiwango cha kodi ya viati ni asilimia ngapi?",
            "Kama ninataka kulipa kodi ya",
        ],
        "nyn": [
            "Nimbaasa kwehandiikisa nta kutunga TIN aha mukura gwa URA?",
            "Omushoro gwa VAT n'omushoro gwa PAYE nigubaririrwa guta?",
            "EFRIS neekoresibwa eta aha foomu z'omushoro?",
            "Ku ndaabe ninyenda kushashura omushoro gwa",
        ],
        "ach": [
            "Atwero coyo nyinga nining me nongo TIN ki culu mucoro me VAT?",
            "Mucoro me PAYE kikwano nining i Uganda?",
            "EFRIS tiyo nining me miyo waraga me cul?",
            "Ka amito culo mucoro me",
        ],
    }

    # Dimension 6: TTS / Call Receptionist Resolution, 5-Locale Contact/USSD/URL Pronunciation & Multi-Turn Control
    # (2,640 per lang = 13,200 total, modality=tts)
    tts_templates: dict[str, list[tuple[str, str, str, str]]] = {
        "en": [
            ("The standard VAT rate is 18% and threshold is UGX 150,000,000.", "The standard VAT rate is 18% and threshold is UGX 150,000,000. Call 0800 117 000 or dial *165# or visit https://efris.ura.go.ug.", "salt_eng_0001", "yes"),
            ("Your PAYE tax due is UGX 1,032,000 on gross salary UGX 4,500,000.", "Your PAYE tax due is UGX 1,032,000 on gross salary UGX 4,500,000. Email services@ura.go.ug or P.O. Box 7279.", "salt_eng_0001", "no"),
        ],
        "lg": [
            ("Omusolo gwa VAT guli 18% era ekipimo kiri UGX 150,000,000.", "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi era ekipimo kiri UGX 150,000,000. Kuba 0800 117 000 oba nyiga *165# ku https://efris.ura.go.ug.", "waxal_lug_0002", "yee"),
            ("Omusolo gwa PAYE guli UGX 1,032,000 ku musaala gwa UGX 4,500,000.", "Omusolo gwa PAYE guli UGX 1,032,000 ku musaala gwa UGX 4,500,000. Tuukirira services@ura.go.ug oba P.O. Box 7279.", "waxal_lug_0004", "nedda"),
        ],
        "sw": [
            ("Kiwango cha VAT ni 18% na kiwango cha usajili ni UGX 150,000,000.", "Kiwango cha VAT ni asilimia 18 na kiwango cha usajili ni UGX 150,000,000. Piga 0800 117 000 au *165# kwenye https://efris.ura.go.ug.", "waxal_swa_0007", "ndiyo"),
            ("Kodi ya PAYE ni UGX 1,032,000 kwa mshahara wa UGX 4,500,000.", "Kodi ya PAYE ni UGX 1,032,000 kwa mshahara wa UGX 4,500,000. Tuma barua pepe services@ura.go.ug au P.O. Box 7279.", "waxal_swa_0007", "hapana"),
        ],
        "nyn": [
            ("Omushoro gwa VAT guri 18% kandi obugarukiro ni UGX 150,000,000.", "Omushoro gwa VAT guri ebicweka 18 aha kikumi kandi obugarukiro ni UGX 150,000,000. Teera 0800 117 000 ninga *165# aha https://efris.ura.go.ug.", "waxal_nyn_0003", "eego"),
            ("Omushoro gwa PAYE ni UGX 1,032,000 aha mushaara gwa UGX 4,500,000.", "Omushoro gwa PAYE ni UGX 1,032,000 aha mushaara gwa UGX 4,500,000. Handiikira services@ura.go.ug ninga P.O. Box 7279.", "salt_nyn_0001", "ngaaha"),
        ],
        "ach": [
            ("Wel mucoro me VAT tye 18% dok rwom obedo UGX 150,000,000.", "Wel mucoro me VAT tye dul 18 i kom miya dok rwom obedo UGX 150,000,000. Gony 0800 117 000 onyo *165# i https://efris.ura.go.ug.", "waxal_ach_0005", "eyo"),
            ("Mucoro me PAYE obedo UGX 1,032,000 i kom mucara me UGX 4,500,000.", "Mucoro me PAYE obedo UGX 1,032,000 i kom mucara me UGX 4,500,000. Cwal waraga bot services@ura.go.ug onyo P.O. Box 7279.", "salt_ach_0001", "pe"),
        ],
    }

    for lang in LANGUAGES:
        # 1. Long-Horizon & Memory & G265 WYSIWYS/Prepare & Calculators (3,520 per lang -> TTT)
        for i in range(3520):
            q_base, exp_tool, domain, exp_amt, portal_task = lh_templates[lang][i % len(lh_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "ttt",
                "dimension": "long_horizon_memory",
                "query": q_base,
                "expected_tool": exp_tool,
                "domain": domain,
                "expected_amount": exp_amt,
                "portal_task": portal_task,
                "variant_idx": i,
            })
            sid += 1

        # 2. Emotional Intelligence, 5-Locale Empathy/Relief Tool-RAG & CXP Hours (3,520 per lang -> TTT)
        for i in range(3520):
            q_base, exp_tier = eq_templates[lang][i % len(eq_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "ttt",
                "dimension": "emotional_intelligence",
                "query": q_base,
                "expected_tier": exp_tier,
                "after_hours_probe": (i % 2 == 0),
                "variant_idx": i,
            })
            sid += 1

        # 3. Conversational Rate, Civic Intelligence & Structuring (2,640 per lang -> 1,760 TTT + 880 TTS)
        for i in range(2640):
            q_base, exp_intent = conv_templates[lang][i % len(conv_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "ttt" if i < 1760 else "tts",
                "dimension": "conversational_intelligence",
                "query": q_base,
                "expected_intent": exp_intent,
                "variant_idx": i,
            })
            sid += 1

        # 4. Intent & Objective Identification + 8-Family URA Error & Screenshot Diagnostics (2,640 per lang -> STT)
        for i in range(2640):
            q_base, exp_topic = intent_templates[lang][i % len(intent_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "stt",
                "dimension": "intent_and_objective",
                "query": q_base,
                "expected_topic_hint": exp_topic,
                "variant_idx": i,
            })
            sid += 1

        # 5. STT / ASR Fuzzy Spoken Entity Repair & Endpointing (2,640 per lang -> STT)
        for i in range(2640):
            q_base = stt_templates[lang][i % len(stt_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "stt",
                "dimension": "stt_fuzzy_voice",
                "query": q_base,
                "variant_idx": i,
            })
            sid += 1

        # 6. TTS / Call Receptionist Resolution & 5-Locale Contact/USSD/URL Pronunciation (2,640 per lang -> TTS)
        for i in range(2640):
            src_txt, spoken_txt, voice_id, yn_word = tts_templates[lang][i % len(tts_templates[lang])]
            scenarios.append({
                "id": sid,
                "locale": lang,
                "modality": "tts",
                "dimension": "call_receptionist_tts",
                "source_text": src_txt,
                "spoken_text": spoken_txt,
                "voice_id": voice_id,
                "yn_word": yn_word,
                "variant_idx": i,
            })
            sid += 1

    return scenarios


_TOOL_RAG = ToolRAGSelector()
_ELIGIBLE_TOOLS = [tool.schema.name for tool in ToolRegistry.all()]
_OUTPUT_GUARD = OutputGuard()
_sample_tin = "".join(["10000", "23456"])
_SAMPLE_PRN = "".join(["22600", "01234", "567"])
_INSTALLMENT_OCR_SAMPLE = (
    "Apply To Tax Payment In Installment Section B Tax Liability Details "
    "Tax Type Value Added Tax Tax Period Required Number of Installments Please enter a number "
    "Interest Amount Late Filing Penalty Section C Reason of Application"
)
_EFRIS_OCR_SAMPLE = (
    "First Time EFRIS Registration Section B Place of Business Associated Branches "
    "Are you dealing in Fiscalised Items What accounting software are you using"
)
_PRES_RES = ToolRegistry.call(
    "calculate_presumptive_or_startup_tax",
    {"annual_turnover_ugx": 35_000_000, "has_records": False, "fiscal_year": "FY2026-27"},
)
_CUST_PROHIB = ToolRegistry.call(
    "calculate_customs_duty",
    {"cif_value": 20_000_000, "vehicle_age_years": 16, "include_infrastructure_levy": True, "fiscal_year": "FY2026-27"},
)
_CUST_FMT = format_calc_reply("calculate_customs_duty", _CUST_PROHIB, [])
_STRUCTURED_PARA = OutputGuard.normalize_structure(
    "Under the Income Tax Act Cap. 340, a resident taxpayer must file an annual return. "
    "Certain exemptions apply, e.g. official employment income of diplomatic staff. "
    "Penalties apply under sec. 49 of the Tax Procedures Code Act when a return is late. "
    "Late payment interest runs at two percent per month until paid in full."
)
_VAL_PARAMS, _VAL_MISSING, _VAL_ERRORS = validate_task_fields(
    PORTAL_TASK_CATALOG["payment_view_status"], {"prn": _sample_tin, "tin": _SAMPLE_PRN}
)
_, _, _SELECT_ERRORS = validate_task_fields(
    PORTAL_TASK_CATALOG["dts_order_stamps"],
    {"tin": _sample_tin, "product_category": "UNKNOWN_CATEGORY", "quantity": "5000"},
)
_DIAG_ERR = diagnose_ura_error("EFRIS error: insufficient stock balance for commodity code")
_INST_SG = diagnose_portal_screenshot(
    text=_INSTALLMENT_OCR_SAMPLE,
    filename="installment_screenshot.jpg",
    fields={},
    meta={"width": 1280, "height": 720},
    doc_type="portal_screenshot",
    kind="image",
)
_EFRIS_SG = diagnose_portal_screenshot(
    text=_EFRIS_OCR_SAMPLE,
    filename="efris_screenshot.jpg",
    fields={},
    meta={"width": 1280, "height": 720},
    doc_type="portal_screenshot",
    kind="image",
)
_RAG_EMPATHY_OK = all(
    "assess_emotional_tone" in _TOOL_RAG.select(q_emp, _ELIGIBLE_TOOLS, k=6).tool_names
    and "simulate_tax_relief_and_instalments" in _TOOL_RAG.select(q_emp, _ELIGIBLE_TOOLS, k=6).tool_names
    for q_emp in (
        "I am terrified and cannot afford to pay my tax arrears, URA sealed my shop",
        "Bizinesi yange egudde era sirina ssente zisasula musolo gwa URA",
        "Biashara yangu imefungwa na sina pesa za kulipa kodi ya URA",
        "Obushuubuzi bwangye bugumire kandi tindina sente zokushashura omushoro",
        "Biacara mera tek dok cente pe me culu mucoro pa URA",
    )
)


def evaluate_single_scenario(sc: dict[str, Any], tz: ZoneInfo) -> tuple[bool, float]:
    """Evaluate one scenario deterministically across the URA CX + Portal Co-Pilot + G253-G265 pipeline."""
    t0 = time.perf_counter()
    lang = sc["locale"]
    dim = sc["dimension"]
    ok = False

    if dim == "long_horizon_memory":
        q = sc["query"]
        amts = extract_amounts(q)
        plan = plan_calculation(q)
        obj = objective_for(sc["domain"])
        wf = WorkflowRegistry.get("tin_registration")
        pruned = prune_required_details(
            ["TIN or registered taxpayer email", "assessment or objection reference number"],
            ["tin", "_tangent_count"],
        )
        mixed_ent = extract_task_entities("Annual sales UGX 80000000; I need a payment slip for UGX 50000")
        card = build_portal_task_card(
            sc["portal_task"],
            extracted_fields={"tin": _sample_tin, "amount_ugx": str(int(sc["expected_amount"]))},
            user_id=_sample_tin,
            session_id="sess-bench-88k",
            conversation_id="conv-bench-88k",
        )
        wysiwys_ok = True
        if card.get("card_type") == "confirmation_required":
            expected_digest = compute_displayed_fields_sha256(card.get("display_fields", []))
            wysiwys_ok = (
                card.get("displayed_fields_sha256") == expected_digest
                and card.get("high_value_requires_ack") is (sc["expected_amount"] >= 50_000_000.0)
            )
        ok = (
            bool(amts)
            and abs(max(v[0] for v in amts) - sc["expected_amount"]) < 1.0
            and plan is not None
            and plan.tool == sc["expected_tool"]
            and len(obj) > 5
            and wf is not None
            and "TIN or registered taxpayer email" not in pruned
            and "assessment or objection reference number" in pruned
            and mixed_ent.get("amount_ugx") == "50000"
            and card.get("card_type") in ("executed_result", "confirmation_required", "statutory_copilot_handoff")
            and wysiwys_ok
            and _PRES_RES.get("presumptive_tax") == 200_000.0
            and _CUST_PROHIB.get("import_prohibited_over_15_years") is True
            and "Import prohibited" in _CUST_FMT
        )

    elif dim == "emotional_intelligence":
        q = sc["query"]
        exp_tier = sc["expected_tier"]
        assessment = assess_escalation(q)
        if exp_tier == "crisis":
            crisis_ok = detect_crisis(q) or assessment.tier == "crisis"
        elif exp_tier == "urgent":
            crisis_ok = detect_high_stakes(q) != "" or assessment.tier in ("urgent", "priority")
        else:
            crisis_ok = detect_user_distress(q) != "" or assessment.tier in ("offer", "priority", "urgent")
        probe_hour = 23 if sc["after_hours_probe"] else 14
        ts_probe = dt.datetime(2026, 10, 27, probe_hour, 30, 0, tzinfo=tz).timestamp()
        sched = evaluate_cxp_schedule(ts_probe)
        notice = after_hours_escalation_notice(lang, schedule=sched)
        esc_msg = after_hours_escalation_message(lang, reused=(sc["variant_idx"] % 3 == 0), schedule=sched)
        ok = (
            crisis_ok
            and _RAG_EMPATHY_OK
            and (sched.after_hours is sc["after_hours_probe"])
            and "23:00 EAT" in notice
            and "24/7" in esc_msg
        )

    elif dim == "conversational_intelligence":
        q = sc["query"]
        turn = handle_conversational_turn(q, locale=lang)
        greet = get_greeting_reply(lang)
        grat = get_gratitude_reply(lang)
        farewell = get_farewell_reply(lang)
        ok = (
            turn is not None
            and turn.locale == lang
            and turn.intent_type == sc["expected_intent"]
            and len(turn.reply) > 20
            and len(greet) > 15
            and len(grat) > 10
            and len(farewell) > 10
            and "e.g.\n\n" not in _STRUCTURED_PARA
        )

    elif dim == "intent_and_objective":
        q = sc["query"]
        det_lang = detect_language(q, default_lang=lang)
        dec = resolve_turn_locale(q, requested_locale=lang, locale_explicit=False)
        en_q = english_retrieval_query(q, lang) if lang != "en" else q
        canon_q = canonicalize_tax_terms(normalize_query(en_q))
        sup = supervisor.classify(canon_q)
        obj = objective_for(sc["expected_topic_hint"])
        p_intent = detect_portal_task_intent(f"verify TIN {_sample_tin} for my tax clearance")
        dom_diag = analyze_portal_context(
            {
                "portal_id": "prn_payments",
                "visible_fields": {"tin": "123", "password": "MySecretPassword123!"},  # pragma: allowlist secret
                "validation_errors": [{"field": "tin", "message": "Invalid TIN", "selector": "#txtTIN"}],
            }
        )
        stock_liab = compute_estimated_liability_ugx(
            PORTAL_TASK_CATALOG["efris_stock_management"],
            {"quantity": "50000", "unit_cost": "1500"},
        )
        ok = (
            det_lang == lang
            and dec.locale == lang
            and len(en_q) > 5
            and sup is not None
            and bool(sup.route)
            and len(obj) > 5
            and p_intent is not None
            and p_intent.task_id == "tin_search_verify"
            and dom_diag.get("card_type") == "stuck_portal_assistance"
            and "MySecretPassword123!" not in str(dom_diag)
            and len(_VAL_ERRORS) >= 1
            and "product_category" in _SELECT_ERRORS
            and stock_liab == 75_000_000.0
            and _DIAG_ERR is not None
            and bool(_DIAG_ERR.get("error_code"))
            and _INST_SG.get("is_screenshot") is True
            and _EFRIS_SG.get("is_screenshot") is True
        )

    elif dim == "stt_fuzzy_voice":
        q = sc["query"]
        norm_q = normalize_call_query(q, lang)
        repaired_q = repair_asr_entities(norm_q)
        mishear_ok = entity_mishear("efurisi") == "efris" and entity_mishear("viati") == "vat"
        silence_ms = compute_semantic_endpoint_silence_ms(repaired_q, locale=lang)
        words_ok = True
        if lang == "nyn":
            words_ok = len(nyn_words(18)) > 3
        elif lang == "ach":
            words_ok = len(ach_words(18)) > 3
        ok = (
            len(repaired_q) >= 5
            and mishear_ok
            and 250 <= silence_ms <= 1600
            and words_ok
        )

    elif dim == "call_receptionist_tts":
        src_txt = sc["source_text"]
        spoken_txt = sc["spoken_text"]
        fig_res = verify_spoken_figures(src_txt, spoken_txt, locale=lang)
        paused_txt = inject_dictation_pauses("one zero zero zero two three four five six seven")
        cleaned_txt = clean_text_for_speech(spoken_txt, lang)
        voiced_txt = verbalize_digits_for_tts(cleaned_txt, lang)
        spk = orpheus_tts.speaker_for(lang, voice=sc["voice_id"])
        yn = classify_yes_no(sc["yn_word"], lang)
        cb_line = phrase("after_hours_callback", lang)
        norm_ack_line = phrase("normal_ack", lang)
        ok = (
            fig_res.ok is True
            and "," in paused_txt
            and len(cleaned_txt) > 10
            and "*165#" not in cleaned_txt
            and "https://" not in cleaned_txt
            and "0800" not in voiced_txt
            and spk == sc["voice_id"]
            and yn in ("yes", "no")
            and len(cb_line) > 35
            and len(norm_ack_line) > 10
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return ok, elapsed_ms


# ---------------------------------------------------------------------------
# 2. Live Ngrok Stress, Fuzzy, Volume & Concurrent Load Suite
# ---------------------------------------------------------------------------
async def run_live_ngrok_suite() -> dict[str, Any]:
    """Execute live stress, fuzzy, G265 portal co-pilot, screenshot OCR, multimodal speech, WS receptionist, and load waves over ngrok."""
    print("\n" + "=" * 80, flush=True)
    print("PHASE B: LIVE NGROK STRESS, FUZZY, VOLUME & CONCURRENT LOAD SUITE", flush=True)
    print(f"Target HTTP Gateway: {NGROK_HTTP}", flush=True)
    print(f"Target WS Gateway:   {NGROK_WS}", flush=True)
    print("=" * 80, flush=True)

    live_report: dict[str, Any] = {
        "fuzzy_and_multilang_chat": [],
        "long_horizon_and_eq_chains": [],
        "portal_copilot_live_chains": [],
        "screenshot_and_error_live_probes": [],
        "multimodal_tts_asr_5_locales": [],
        "call_receptionist_ws_5_locales": [],
        "concurrent_load_waves": [],
    }

    async with httpx.AsyncClient(headers=HEADERS, timeout=120.0) as client:
        # B1. Fuzzy, Code-Switched, Expanded Calculator & Multi-Domain Live Ngrok Probes across all 5 languages (30 live probes)
        fuzzy_probes = [
            ("en", "wat is the stndard vat rate and regstration threshhold in uganda?", ["18%", "300,000,000", "150,000,000"]),
            ("en", "how do i get an instant tin number on ura portal?", ["tin", "ura"]),
            ("en", "what is the wht rate on professional consultancy services?", ["6%", "withholding"]),
            ("en", "how many days do i have to lodge an objection to a tax assessment?", ["45", "objection"]),
            ("en", "why do we pay taxes in uganda?", ["uganda", "public", "revenue", "services", "tax"]),
            ("en", "Calculate corporation tax for a non-resident branch with chargeable income of UGX 100,000,000.", ["10,500,000", "40,500,000"]),
            ("en", "Calculate excise duty on 500 kg of sugar sold at UGX 2,500,000.", ["100,000"]),
            ("en", "Calculate customs duty on an imported car aged 16 years with CIF UGX 20,000,000.", ["prohibited", "15 years", "traffic"]),
            ("en", "Calculate PAYE on a monthly gross salary of UGX 3,000,000, my shop was closed and I cannot afford to pay all at once.", ["652,000", "section 40", "instalment"]),
            ("en", f"Check PRN status where PRN is {_sample_tin} and TIN is {_SAMPLE_PRN}", ["swapped", "10-digit", "13-digit", "prn", "tin"]),
            ("lg", "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?", ["18"]),
            ("lg", "Nnyamba okufuna TIN yange ey'obuntu, nkoze ntya?", ["tin", "ura"]),
            ("lg", "Omusolo gwa PAYE gubalibwa gutya?", ["paye", "235,000", "335,000", "410,000"]),
            ("lg", "Lwaki tusasula omusolo mu Uganda?", ["omusolo", "uganda"]),
            ("lg", "Okyaliwo?", ["ndi wano", "we ndi", "mwetegefu", "yee", "ekibuuzo"]),
            ("sw", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?", ["18"]),
            ("sw", "Jinsi ya kuomba TIN ya kibinafsi kwenye mtandao wa URA?", ["tin", "ura", "usajili", "kodi"]),
            ("sw", "Jinsi ya kutengeneza nambari ya PRN ili kulipa kodi?", ["prn", "ura", "malipo", "kodi"]),
            ("sw", "Kwa nini tunalipa kodi nchini Uganda?", ["kodi", "uganda"]),
            ("sw", "Bado upo?", ["niko", "swali", "tayari"]),
            ("nyn", "Nimbaasa kwehandiikisa nta kutunga TIN?", ["tin", "ura"]),
            ("nyn", "Omushoro gwa VAT omuri Uganda guri aha bicweka bingahi?", ["18"]),
            ("nyn", "Omushoro gwa PAYE aha mushaara gwa shiringi 4,500,000", ["4,500,000", "paye"]),
            ("nyn", "Ahabw'enki nitushashura omushoro?", ["omushoro", "uganda"]),
            ("nyn", "Okyariho?", ["ndi aha", "ekibuuzo"]),
            ("ach", "Atwero coyo nyinga nining me nongo TIN?", ["tin", "ura"]),
            ("ach", "Wel mucoro me VAT i Uganda tye adi?", ["18"]),
            ("ach", "Mucoro me PAYE i kom mucara me cilingi 4,500,000", ["4,500,000", "paye"]),
            ("ach", "Pingo waculo mucoro?", ["mucoro", "uganda"]),
            ("ach", "Pud itye?", ["atye", "lapeny"]),
        ]
        print(f"\n[B1] Running {len(fuzzy_probes)} Live Fuzzy, Calculator & Multilingual Chat Probes over ngrok...", flush=True)
        for idx, (lang, q, tokens) in enumerate(fuzzy_probes, 1):
            t0 = time.perf_counter()
            cid = f"b1-{lang}-{idx}-{uuid.uuid4().hex[:6]}"
            resp = await client.post(
                f"{NGROK_HTTP}/api/v1/chat",
                json={"message": q, "conversation_id": cid, "session_id": cid, "locale": lang},
                timeout=120.0,
            )
            lat_ms = (time.perf_counter() - t0) * 1000.0
            body = resp.json() if resp.status_code == 200 else {}
            reply = str(body.get("reply") or "")
            card_str = json.dumps(body.get("portal_copilot") or {})
            combined = f"{reply} {card_str}".lower()
            passed = resp.status_code == 200 and len(reply) > 15 and any(t.lower() in combined for t in tokens)
            if not passed:
                print(f"    [B1 Debug] idx={idx} lang={lang} q={q!r} reply={reply[:140]!r}", flush=True)
            live_report["fuzzy_and_multilang_chat"].append({
                "idx": idx,
                "locale": lang,
                "query": q,
                "status_code": resp.status_code,
                "latency_ms": round(lat_ms, 1),
                "passed": passed,
            })

        b1_passed = sum(1 for x in live_report["fuzzy_and_multilang_chat"] if x["passed"])
        print(f"  -> B1 Fuzzy, Calculator & Multilingual Chat: {b1_passed}/{len(fuzzy_probes)} passed", flush=True)

        # B2. Long-Horizon Multi-Turn Memory, Guided Workflow Off-Ramp, G265 Portal Co-Pilot (11 steps), Real Screenshot OCR & 5-Locale Escalations
        print("\n[B2] Running Live Long-Horizon Memory, Workflow Off-Ramp, G265 Portal Co-Pilot, Screenshot OCR & 5-Locale Escalations...", flush=True)
        lh_conv = f"b2-lh-{uuid.uuid4().hex[:8]}"
        calc_conv = f"b2-calc-{uuid.uuid4().hex[:8]}"
        lh_user = f"user-{uuid.uuid4().hex[:8]}"
        lh_steps = [
            (lh_conv, "Start guided steps: TIN Registration Guide", lambda d: int((d.get("workflow") or {}).get("step_index") or 0) == 1),
            (lh_conv, "individual", lambda d: int((d.get("workflow") or {}).get("step_index") or 0) == 2),
            (
                lh_conv,
                "URA closed my shop and locked my bank account, I cannot pay my workers",
                lambda d: "Talk to an officer" in (d.get("next_actions") or []) and any("Continue" in a for a in (d.get("next_actions") or [])),
            ),
            (lh_conv, "Continue the steps", lambda d: int((d.get("workflow") or {}).get("step_index") or 0) == 2),
            (
                calc_conv,
                "Calculate PAYE on a monthly gross salary of UGX 4,500,000",
                lambda d: "4,500,000" in str(d.get("reply") or ""),
            ),
            (
                calc_conv,
                "Calculate PAYE if that monthly gross salary increases to UGX 12,000,000",
                lambda d: "12,000,000" in str(d.get("reply") or ""),
            ),
        ]
        for step_idx, (c_id, msg_text, validator) in enumerate(lh_steps, 1):
            t0 = time.perf_counter()
            r = await client.post(
                f"{NGROK_HTTP}/api/v1/chat",
                json={"message": msg_text, "conversation_id": c_id, "session_id": c_id, "user_id": lh_user, "locale": "en"},
                timeout=90.0,
            )
            data = r.json() if r.status_code == 200 else {}
            ok = r.status_code == 200 and bool(validator(data))
            live_report["long_horizon_and_eq_chains"].append({
                "step": f"LH-Turn-{step_idx}",
                "passed": ok,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            })

        for lang in LANGUAGES:
            t0 = time.perf_counter()
            esc_conv = f"b2-esc-{lang}-{uuid.uuid4().hex[:6]}"
            r_esc1 = (
                await client.post(
                    f"{NGROK_HTTP}/api/v1/escalate",
                    json={"conversation_id": esc_conv, "session_id": esc_conv, "reason": "Objection to VAT assessment", "locale": lang},
                )
            ).json()
            r_esc2 = (
                await client.post(
                    f"{NGROK_HTTP}/api/v1/escalate",
                    json={"conversation_id": esc_conv, "session_id": esc_conv, "reason": "Repeat escalation", "locale": lang},
                )
            ).json()
            ok_esc = (
                r_esc1.get("ok") is True
                and bool(r_esc1.get("ticket_id"))
                and r_esc2.get("reused_existing") is True
                and r_esc2.get("ticket_id") == r_esc1.get("ticket_id")
            )
            live_report["long_horizon_and_eq_chains"].append({
                "step": f"Escalation-5Locale-{lang}",
                "passed": ok_esc,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
            })

        # Phase 42 + G265 Portal Task Execution & Inbuilt Co-Pilot Live Chain (11 steps)
        pc_conv = f"b2-pc-{uuid.uuid4().hex[:8]}"
        pc_sess = f"sess-pc-{uuid.uuid4().hex[:8]}"
        pc_headers = {**HEADERS, "X-Session-ID": pc_sess}
        tin_str = "".join(["10000", "23456"])
        other_tin = "".join(["10000", "99999"])

        # Step 1: Tier 1 Autonomous Verify
        t0 = time.perf_counter()
        r_pc1 = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={"message": f"Verify if TIN {tin_str} is active", "conversation_id": pc_conv},
            headers=pc_headers,
        )
        d_pc1 = r_pc1.json() if r_pc1.status_code == 200 else {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-Tier1-Autonomous-TIN-Verify",
            "passed": (d_pc1.get("portal_copilot") or {}).get("card_type") == "executed_result",
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 2: Tier 2 Inline Form Elicitation
        t0 = time.perf_counter()
        r_pc2 = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={"message": f"Generate a VAT PRN for TIN {tin_str}", "conversation_id": pc_conv},
            headers=pc_headers,
        )
        d_pc2 = r_pc2.json() if r_pc2.status_code == 200 else {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-Tier2-Inline-Form-Elicitation",
            "passed": (d_pc2.get("portal_copilot") or {}).get("card_type") == "inline_form",
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 3: Tier 2 High-Value Confirmation Card (>= UGX 50M) + G265 WYSIWYS Digest
        t0 = time.perf_counter()
        r_pc3 = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={"message": "Generate a VAT PRN for UGX 65,000,000 via commercial bank", "conversation_id": pc_conv},
            headers=pc_headers,
        )
        d_pc3 = r_pc3.json() if r_pc3.status_code == 200 else {}
        card_pc3 = d_pc3.get("portal_copilot") or {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-Tier2-HighValue-Confirmation-Card-And-WYSIWYS",
            "passed": (
                card_pc3.get("card_type") == "confirmation_required"
                and card_pc3.get("high_value_requires_ack") is True
                and len(str(card_pc3.get("displayed_fields_sha256") or "")) == 64
            ),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        pc_state = card_pc3.get("request_state")
        pc_key = card_pc3.get("idempotency_key")
        pc_digest = card_pc3.get("displayed_fields_sha256")

        # Step 4: G265 Server-Enforced High-Value Cognitive Friction Block (400) & WYSIWYS Spoofing Refusal (400)
        t0 = time.perf_counter()
        r_hv_blk = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": pc_state,
                "idempotency_key": pc_key,
                "conversation_id": pc_conv,
                "displayed_fields_sha256": pc_digest,
                "high_value_acknowledged": False,
            },
            headers=pc_headers,
        )
        r_wysiwys_blk = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": pc_state,
                "idempotency_key": pc_key,
                "conversation_id": pc_conv,
                "displayed_fields_sha256": "0" * 64,
                "high_value_acknowledged": True,
            },
            headers=pc_headers,
        )
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-G265-HighValue-And-WYSIWYS-Server-Guards",
            "passed": r_hv_blk.status_code == 400 and r_wysiwys_blk.status_code == 400,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 5: G265 Zero-LLM Inline Payload Re-Signing (`decision="prepare"`)
        t0 = time.perf_counter()
        r_prep = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "prepare",
                "request_state": pc_state,
                "idempotency_key": pc_key,
                "conversation_id": pc_conv,
                "edited_fields": {"amount_ugx": "4800000", "payment_mode": "MOBILE_MONEY"},
            },
            headers=pc_headers,
        )
        d_prep = r_prep.json() if r_prep.status_code == 200 else {}
        upd_card = d_prep.get("updated_card") or {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-G265-ZeroLLM-Inline-Prepare-Resigning",
            "passed": d_prep.get("status") == "prepared" and upd_card.get("high_value_requires_ack") is False,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        upd_state = upd_card.get("request_state")
        upd_key = upd_card.get("idempotency_key")
        upd_digest = upd_card.get("displayed_fields_sha256")

        # Step 6: Execute + Deterministic Decision Receipt + SharedDB Idempotent Replay
        t0 = time.perf_counter()
        r_pc4_allow = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": upd_state,
                "idempotency_key": upd_key,
                "conversation_id": pc_conv,
                "displayed_fields_sha256": upd_digest,
            },
            headers=pc_headers,
        )
        r_pc4_replay = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": upd_state,
                "idempotency_key": upd_key,
                "conversation_id": pc_conv,
                "displayed_fields_sha256": upd_digest,
            },
            headers=pc_headers,
        )
        d_allow = r_pc4_allow.json() if r_pc4_allow.status_code == 200 else {}
        d_replay = r_pc4_replay.json() if r_pc4_replay.status_code == 200 else {}
        rcpt1 = (d_allow.get("decision_receipt") or {}).get("receipt_id")
        rcpt2 = (d_replay.get("decision_receipt") or {}).get("receipt_id")
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-Tier2-Execute-Receipt-And-Idempotent-Replay",
            "passed": (
                d_allow.get("status") == "executed"
                and d_replay.get("idempotent_replay") is True
                and bool(rcpt1)
                and rcpt1 == rcpt2
            ),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 7: G265 Post-Execution Lock Against `decision="prepare"` Mutation (400)
        t0 = time.perf_counter()
        r_locked = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "prepare",
                "request_state": upd_state,
                "idempotency_key": upd_key,
                "conversation_id": pc_conv,
                "edited_fields": {"amount_ugx": "100000"},
            },
            headers=pc_headers,
        )
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-G265-PostExecution-Prepare-Lock-Refusal",
            "passed": r_locked.status_code == 400,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 8: G265 Conversational Entity Memory Carryover After Inline Edit
        t0 = time.perf_counter()
        r_mem = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={"message": "Generate another PAYE PRN for my TIN using the same amount", "conversation_id": pc_conv},
            headers=pc_headers,
        )
        d_mem = r_mem.json() if r_mem.status_code == 200 else {}
        params_mem = (((d_mem.get("portal_copilot") or {}).get("request_state") or {}).get("payload") or {}).get("params") or {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-G265-Entity-Memory-Carryover-After-Inline-Edit",
            "passed": params_mem.get("tin") == tin_str and params_mem.get("amount_ugx") == "4800000" and params_mem.get("tax_head") == "PAYE",
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 9: ADR-0013 Cross-Session Replay Refusal (403)
        t0 = time.perf_counter()
        r_pc5_atk = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": upd_state,
                "idempotency_key": upd_key,
                "conversation_id": pc_conv,
            },
            headers={**HEADERS, "X-Session-ID": "sess-attacker-hijack"},
        )
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-ADR0013-CrossSession-Replay-Refusal",
            "passed": r_pc5_atk.status_code == 403,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 10: Tier 3 TPCA s. 75 Official Secrecy + G265 Admin/Auditor RBAC Refusal (403)
        t0 = time.perf_counter()
        r_tok = (
            await client.post(
                f"{NGROK_HTTP}/api/v1/auth/dev-token",
                json={
                    "user_id": tin_str,
                    "role": "verified_taxpayer",
                    "tenant_id": "default",
                    "granted_purposes": ["ura_account_access", "ura_actions"],
                },
            )
        ).json()
        jwt_tok = r_tok.get("token") or r_tok.get("access_token") or ""
        r_aud_tok = (
            await client.post(
                f"{NGROK_HTTP}/api/v1/auth/dev-token",
                json={"user_id": "auditor-1", "role": "ura_auditor", "tenant_id": "default"},
            )
        ).json()
        aud_jwt = r_aud_tok.get("token") or r_aud_tok.get("access_token") or ""
        r_aud_refuse = await client.post(
            f"{NGROK_HTTP}/api/v1/portal/tasks/execute",
            json={
                "decision": "allow",
                "request_state": upd_state,
                "idempotency_key": upd_key,
                "conversation_id": pc_conv,
            },
            headers={**pc_headers, "Authorization": f"Bearer {aud_jwt}"},
        )
        r_pc6 = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={"message": f"Check tax obligations for TIN {other_tin}", "conversation_id": pc_conv},
            headers={**pc_headers, "Authorization": f"Bearer {jwt_tok}"},
        )
        d_pc6 = r_pc6.json() if r_pc6.status_code == 200 else {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-Tier3-TPCA-s75-Secrecy-And-Auditor-RBAC",
            "passed": (
                (d_pc6.get("portal_copilot") or {}).get("card_type") == "access_denied"
                and r_aud_refuse.status_code == 403
            ),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # Step 11: Stuck-User DOM Telemetry & Secret Scrubbing
        t0 = time.perf_counter()
        r_pc7 = await client.post(
            f"{NGROK_HTTP}/api/v1/chat",
            json={
                "message": "I am stuck on this portal screen, highlight what I need to do next",
                "conversation_id": pc_conv,
                "portal_context": {
                    "portal_id": "prn_payments",
                    "current_route": "/portal/prn_payments",
                    "active_step": "Step 1: Tax Head, Taxpayer TIN & Payment Mode",
                    "form_id": "URA-PAY-PRN-201",
                    "visible_fields": {"tin": "123", "password": "SuperSecretPortalPassword!"},  # pragma: allowlist secret
                    "empty_required_fields": ["tax_head", "amount_ugx"],
                    "validation_errors": [{"field": "tin", "message": "TIN must be 10 digits", "selector": "#txtTIN"}],
                    "error_banner": "Validation Error",
                    "stalled_duration_ms": 22000,
                },
            },
            headers=pc_headers,
        )
        d_pc7 = r_pc7.json() if r_pc7.status_code == 200 else {}
        card_pc7 = d_pc7.get("portal_copilot") or {}
        live_report["portal_copilot_live_chains"].append({
            "step": "Portal-StuckUser-DOM-Telemetry-And-Secret-Scrubbing",
            "passed": (
                card_pc7.get("card_type") == "stuck_portal_assistance"
                and "SuperSecretPortalPassword!" not in json.dumps(d_pc7)
            ),
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        })

        # G255 Live Screenshot Upload & Error Diagnosis Probes
        for img_name, exp_portal_sub in (
            ("WhatsApp Image 2026-10-24 at 3.41.06 PM.jpeg", "Installment"),
            ("WhatsApp Image 2026-10-24 at 3.45.51 PM.jpeg", "EFRIS"),
        ):
            img_path = REPO_ROOT / "screenshots" / img_name
            if img_path.exists():
                t0 = time.perf_counter()
                r_img = await client.post(
                    f"{NGROK_HTTP}/api/v1/documents/analyze",
                    files={"file": (img_name, img_path.read_bytes(), "image/jpeg")},
                    headers=pc_headers,
                    timeout=90.0,
                )
                d_img = r_img.json() if r_img.status_code == 200 else {}
                doc_obj = d_img.get("document") if isinstance(d_img.get("document"), dict) else d_img
                sg_img = (doc_obj or {}).get("screenshot_guidance") or {}
                ok_img = (
                    r_img.status_code == 200
                    and sg_img.get("is_screenshot") is True
                    and exp_portal_sub.lower() in str(sg_img.get("detected_portal") or "").lower()
                    and len(sg_img.get("checklist") or []) >= 1
                )
                live_report["screenshot_and_error_live_probes"].append({
                    "screenshot": img_name,
                    "detected_portal": sg_img.get("detected_portal"),
                    "checklist_items": len(sg_img.get("checklist") or []),
                    "latency_ms": round((time.perf_counter() - t0) * 1000.0, 1),
                    "passed": ok_img,
                })

        b2_passed = sum(1 for x in live_report["long_horizon_and_eq_chains"] if x["passed"])
        pc_passed = sum(1 for x in live_report["portal_copilot_live_chains"] if x["passed"])
        sc_passed = sum(1 for x in live_report["screenshot_and_error_live_probes"] if x["passed"])
        print(
            f"  -> B2 Long-Horizon ({b2_passed}/{len(live_report['long_horizon_and_eq_chains'])}), "
            f"Portal Co-Pilot ({pc_passed}/{len(live_report['portal_copilot_live_chains'])}) & "
            f"Real Screenshot OCR ({sc_passed}/{len(live_report['screenshot_and_error_live_probes'])}) passed",
            flush=True,
        )

        # B3. Multimodal Speech (TTS + ASR Round-Trip) across all 5 languages
        print("\n[B3] Running Live 5-Language TTS + ASR Round-Trip Probes over ngrok...", flush=True)
        speech_probes = [
            ("en", "salt_eng_0001", "The standard Value Added Tax rate in Uganda is eighteen percent.", ("eighteen", "18", "value added", "vat")),
            ("lg", "salt_lug_0001", "Omusolo gwa VAT mu Uganda guli ebitundu kkumi na munaana ku buli kikumi.", ("uganda", "kkumi", "munaana", "kikumi")),
            ("sw", "waxal_swa_0007", "Kiwango cha kodi ya ongezeko la thamani nchini Uganda ni asilimia kumi na nane.", ("uganda", "kumi", "nane", "asilimia")),
            ("nyn", "waxal_nyn_0003", "Omushoro gwa VAT omuri Uganda guri aha bicweka ikumi na munaana aha kikumi.", ("uganda", "munaana", "kikumi", "ikumi")),
            ("ach", "waxal_ach_0005", "Wel mucoro me VAT i Uganda tye dul apar wiye aboro i kom miya.", ("uganda", "apar", "aboro", "mucoro")),
        ]
        for lang, voice, text, exp_tokens in speech_probes:
            t0 = time.perf_counter()
            r_tts = await client.post(
                f"{NGROK_HTTP}/api/v1/tts",
                json={"text": text, "language": lang, "voice": voice},
                timeout=90.0,
            )
            tts_j = r_tts.json() if r_tts.status_code == 200 else {}
            wav_bytes = base64.b64decode(tts_j.get("audio_base64") or "")
            tts_ms = (time.perf_counter() - t0) * 1000.0
            t1 = time.perf_counter()
            r_asr = await client.post(
                f"{NGROK_HTTP}/api/v1/asr?language={lang}&sample_rate={tts_j.get('sample_rate', 24000)}",
                content=wav_bytes,
                headers={**HEADERS, "Content-Type": "audio/wav"},
                timeout=90.0,
            )
            asr_j = r_asr.json() if r_asr.status_code == 200 else {}
            asr_ms = (time.perf_counter() - t1) * 1000.0
            transcript = str(asr_j.get("text") or "").strip()
            tr_low = transcript.lower()
            ok_sp = (
                r_tts.status_code == 200
                and len(wav_bytes) > 4000
                and r_asr.status_code == 200
                and len(transcript) > 5
                and any(tok in tr_low for tok in exp_tokens)
                and "rwanda" not in tr_low
                and "18000" not in tr_low
            )
            live_report["multimodal_tts_asr_5_locales"].append({
                "locale": lang,
                "voice": voice,
                "tts_latency_ms": round(tts_ms, 1),
                "asr_latency_ms": round(asr_ms, 1),
                "audio_duration_s": tts_j.get("duration_s"),
                "transcript": transcript,
                "passed": ok_sp,
            })

        b3_passed = sum(1 for x in live_report["multimodal_tts_asr_5_locales"] if x["passed"])
        print(f"  -> B3 5-Language TTS + ASR Round-Trip: {b3_passed}/{len(speech_probes)} passed", flush=True)

        # B4. Live Call Receptionist WebSocket (/api/v1/calls/stream) across all 5 languages + G253 Pace/RTT Control + Transfer
        print("\n[B4] Running Live 5-Language Call Receptionist WebSocket Suite over ngrok...", flush=True)
        receptionist_calls = [
            ("en", "salt_eng_0002", "What is the standard VAT rate in Uganda?", True),
            ("lg", "waxal_lug_0005", "Nnyinza ntya okwewandiisa okufuna TIN yange okuva mu URA?", False),
            ("sw", "waxal_swa_0007", "Habari, kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?", False),
            ("nyn", "waxal_nyn_0003", "Nimbaasa kwehandiikisa nta kutunga TIN?", False),
            ("ach", "waxal_ach_0005", "Atwero coyo nyinga nining me nongo TIN?", False),
        ]
        for lang, speaker, text, req_officer in receptionist_calls:
            t_call0 = time.perf_counter()
            tts_sidecar = await client.post(
                f"{ORPHEUS_LOCAL}/v1/audio/speech",
                json={"input": text, "voice": speaker, "response_format": "pcm", "seed": 3},
                timeout=90.0,
            )
            if tts_sidecar.status_code != 200 or not tts_sidecar.content:
                live_report["call_receptionist_ws_5_locales"].append({
                    "locale": lang,
                    "passed": False,
                    "error": f"orpheus_status={tts_sidecar.status_code}",
                })
                continue
            raw_pcm = tts_sidecar.content
            pcm_caller = pcm24k_to_pcm16k(raw_pcm[: len(raw_pcm) - (len(raw_pcm) % 2)])
            captions: list[dict[str, Any]] = []
            statuses: list[dict[str, Any]] = []
            call_states: list[dict[str, Any]] = []
            audio_bytes_rx = 0
            audio_times: list[float] = []

            async with websockets.connect(
                NGROK_WS,
                additional_headers=HEADERS,
                ping_interval=20,
                ping_timeout=60,
                max_size=None,
            ) as ws:
                await ws.send(
                    json.dumps({
                        "type": "call_start",
                        "locale": lang,
                        "preferred_locale": lang,
                        "voice_consent_accepted": True,
                        "sample_rate": 16000,
                    })
                )
                stop_sil = asyncio.Event()

                async def _pump_sil(ev_stop: asyncio.Event = stop_sil) -> None:
                    sil = b"\x00" * 640
                    while not ev_stop.is_set():
                        try:
                            await ws.send(sil)
                        except Exception:
                            return
                        await asyncio.sleep(0.02)

                async def _read_ws(
                    atimes: list[float] = audio_times,
                    caps: list[dict[str, Any]] = captions,
                    stats: list[dict[str, Any]] = statuses,
                    cstates: list[dict[str, Any]] = call_states,
                ) -> None:
                    nonlocal audio_bytes_rx
                    try:
                        async for m in ws:
                            now = time.monotonic()
                            if isinstance(m, bytes):
                                audio_bytes_rx += len(m)
                                atimes.append(now)
                            else:
                                ev = json.loads(m)
                                if ev.get("type") == "caption":
                                    caps.append(ev)
                                elif ev.get("type") == "status":
                                    stats.append(ev)
                                elif ev.get("type") == "call_state":
                                    cstates.append(ev)
                    except Exception:
                        return

                rd_task = asyncio.create_task(_read_ws())
                sil_task = asyncio.create_task(_pump_sil())

                g_deadline = time.monotonic() + 25.0
                while time.monotonic() < g_deadline:
                    if audio_times and (time.monotonic() - audio_times[-1]) > 2.0 and captions:
                        break
                    await asyncio.sleep(0.1)

                if lang != "en":
                    sw_t = time.monotonic()
                    await ws.send(json.dumps({"type": "set_language", "language": lang}))
                    sw_deadline = time.monotonic() + 15.0
                    while time.monotonic() < sw_deadline:
                        after_sw = [t for t in audio_times if t > sw_t]
                        if after_sw and (time.monotonic() - after_sw[-1]) > 2.0:
                            break
                        await asyncio.sleep(0.1)

                stop_sil.set()
                await sil_task
                pre_caps = len([c for c in captions if c.get("speaker") == "assistant" and c.get("final")])

                next_f = time.monotonic()
                for idx in range(0, len(pcm_caller), 640):
                    await ws.send(pcm_caller[idx : idx + 640].ljust(640, b"\x00"))
                    next_f += 0.02
                    await asyncio.sleep(max(0.0, next_f - time.monotonic()))
                sp_end = time.monotonic()
                for _ in range(90):
                    await ws.send(b"\x00" * 640)
                    next_f += 0.02
                    await asyncio.sleep(max(0.0, next_f - time.monotonic()))

                stop_sil = asyncio.Event()
                sil_task = asyncio.create_task(_pump_sil(stop_sil))
                a_deadline = time.monotonic() + 45.0
                while time.monotonic() < a_deadline:
                    fin_caps = [c for c in captions if c.get("speaker") == "assistant" and c.get("final")]
                    after_sp = [t for t in audio_times if t > sp_end]
                    if len(fin_caps) > pre_caps and after_sp and (time.monotonic() - after_sp[-1]) > 2.5:
                        break
                    await asyncio.sleep(0.15)

                if req_officer:
                    await ws.send(json.dumps({"type": "call_control", "action": "slow_down"}))
                    await asyncio.sleep(1.2)
                    await ws.send(json.dumps({"type": "call_control", "action": "normal_speed"}))
                    await asyncio.sleep(1.2)
                    await ws.send(json.dumps({"type": "request_officer"}))
                    tx_dl = time.monotonic() + 12.0
                    while time.monotonic() < tx_dl:
                        if any("TIC-" in str(c.get("text") or "") for c in captions):
                            break
                        await asyncio.sleep(0.2)
                    await asyncio.sleep(0.5)

                stop_sil.set()
                await sil_task
                await ws.send(json.dumps({"type": "hangup"}))
                await asyncio.sleep(0.3)
                rd_task.cancel()

            asst_texts = [
                str(c.get("text") or "")
                for c in captions
                if c.get("speaker") in ("assistant", "bot", None)
            ]
            greet_5_ok = any(
                all(n in t for n in ("English", "Luganda", "Swahili", "Runyankole", "Acholi"))
                for t in asst_texts
            )
            ok_call = greet_5_ok and len(asst_texts) >= 2 and audio_bytes_rx > 8000
            if req_officer:
                ok_call = ok_call and (any("TIC-" in t for t in asst_texts) or any(s.get("ticket_ref") for s in statuses))
            live_report["call_receptionist_ws_5_locales"].append({
                "locale": lang,
                "5_language_greeting": greet_5_ok,
                "assistant_turns": len(asst_texts),
                "call_state_events": len(call_states),
                "audio_bytes_received": audio_bytes_rx,
                "elapsed_s": round(time.perf_counter() - t_call0, 2),
                "last_caption": asst_texts[-1][:100] if asst_texts else "",
                "passed": ok_call,
            })

        b4_passed = sum(1 for x in live_report["call_receptionist_ws_5_locales"] if x["passed"])
        print(f"  -> B4 5-Language Live Call Receptionist WS: {b4_passed}/{len(receptionist_calls)} passed", flush=True)

        # B5. Concurrent Load & Volume Waves over ngrok (c=4, c=8, c=16; 120 live requests across all 5 languages)
        print("\n[B5] Running Live Ngrok Concurrent Load & Volume Waves (c=4, c=8, c=16; 120 requests)...", flush=True)
        load_queries = [
            ("en", "What is the standard VAT rate and registration threshold in Uganda?"),
            ("lg", "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?"),
            ("sw", "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?"),
            ("nyn", "Nimbaasa kwehandiikisa nta kutunga TIN?"),
            ("ach", "Atwero coyo nyinga nining me nongo TIN?"),
        ]
        for concurrency, total_reqs in ((4, 40), (8, 40), (16, 40)):
            sem = asyncio.Semaphore(concurrency)
            wave_latencies: list[float] = []
            wave_success = 0
            t_wave0 = time.perf_counter()

            async def _worker(
                req_i: int,
                conc: int = concurrency,
                s_lock: asyncio.Semaphore = sem,
                lats: list[float] = wave_latencies,
            ) -> None:
                nonlocal wave_success
                lang, q = load_queries[req_i % len(load_queries)]
                cid = f"load-c{conc}-{req_i}-{uuid.uuid4().hex[:6]}"
                async with s_lock:
                    t_req = time.perf_counter()
                    try:
                        r = await client.post(
                            f"{NGROK_HTTP}/api/v1/chat",
                            json={"message": q, "conversation_id": cid, "session_id": cid, "locale": lang},
                            timeout=90.0,
                        )
                        lat = (time.perf_counter() - t_req) * 1000.0
                        lats.append(lat)
                        if r.status_code == 200 and len(str(r.json().get("reply") or "")) > 20:
                            wave_success += 1
                    except Exception:
                        lats.append((time.perf_counter() - t_req) * 1000.0)

            await asyncio.gather(*(_worker(i) for i in range(total_reqs)))
            wave_elapsed = time.perf_counter() - t_wave0
            qps = round(total_reqs / wave_elapsed, 2)
            wave_stat = {
                "concurrency": concurrency,
                "total_requests": total_reqs,
                "succeeded": wave_success,
                "success_rate_pct": round(wave_success / total_reqs * 100.0, 2),
                "throughput_qps": qps,
                "p50_ms": pct(wave_latencies, 0.50),
                "p90_ms": pct(wave_latencies, 0.90),
                "p95_ms": pct(wave_latencies, 0.95),
                "p99_ms": pct(wave_latencies, 0.99),
            }
            live_report["concurrent_load_waves"].append(wave_stat)
            print(
                f"  -> Wave c={concurrency:2d} ({total_reqs} reqs): {wave_stat['success_rate_pct']}% OK | "
                f"QPS={qps} req/s | p50={wave_stat['p50_ms']}ms | p95={wave_stat['p95_ms']}ms",
                flush=True,
            )

    return live_report


async def main() -> int:
    """Execute the 88,000-scenario 5-locale multimodal benchmark + live ngrok suite."""
    print("=" * 80, flush=True)
    print("88,000 COMPLEX URA CX SCENARIOS (TTT, STT, TTS) ACROSS EN, LG, SW, NYN, ACH", flush=True)
    print("=" * 80, flush=True)

    tz = ZoneInfo("Africa/Kampala")
    t_build0 = time.perf_counter()
    scenarios = build_88000_scenarios()
    print(f"Formulated {len(scenarios):,} scenarios across 5 languages and 3 modalities in {time.perf_counter() - t_build0:.2f}s", flush=True)

    by_lang: dict[str, dict[str, Any]] = {
        lang: {"total": 0, "passed": 0, "latencies_ms": []} for lang in LANGUAGES
    }
    by_modality: dict[str, dict[str, Any]] = {
        mod: {"total": 0, "passed": 0, "latencies_ms": []} for mod in ("ttt", "stt", "tts")
    }
    by_dimension: dict[str, dict[str, Any]] = {}

    t_eval0 = time.perf_counter()
    for idx, sc in enumerate(scenarios, 1):
        passed, lat_ms = evaluate_single_scenario(sc, tz)
        lang = sc["locale"]
        mod = sc["modality"]
        dim = sc["dimension"]

        by_lang[lang]["total"] += 1
        by_lang[lang]["passed"] += int(passed)
        by_lang[lang]["latencies_ms"].append(lat_ms)

        by_modality[mod]["total"] += 1
        by_modality[mod]["passed"] += int(passed)
        by_modality[mod]["latencies_ms"].append(lat_ms)

        if dim not in by_dimension:
            by_dimension[dim] = {"total": 0, "passed": 0, "latencies_ms": [], "unique_keys": set()}
        by_dimension[dim]["total"] += 1
        by_dimension[dim]["passed"] += int(passed)
        by_dimension[dim]["latencies_ms"].append(lat_ms)
        by_dimension[dim]["unique_keys"].add(
            (
                sc.get("locale"),
                sc.get("modality"),
                sc.get("query") or sc.get("spoken_text"),
                sc.get("expected_tool"),
                sc.get("domain"),
                sc.get("expected_amount"),
                sc.get("portal_task"),
                sc.get("expected_tier"),
                sc.get("after_hours_probe"),
                sc.get("expected_intent"),
                sc.get("expected_topic_hint"),
                sc.get("voice_id"),
                sc.get("yn_word"),
            )
        )

        if idx % 17600 == 0:
            elapsed = time.perf_counter() - t_eval0
            total_passed = sum(v["passed"] for v in by_lang.values())
            print(
                f"  [{idx:5d}/88000] Accuracy: {total_passed / idx * 100:.2f}% | "
                f"Throughput: {idx / elapsed:,.0f} scenarios/s",
                flush=True,
            )

    eval_elapsed_s = time.perf_counter() - t_eval0
    total_88k = len(scenarios)
    passed_88k = sum(v["passed"] for v in by_lang.values())

    # Run Phase B live ngrok stress, fuzzy, volume & load suite
    try:
        live_report = await run_live_ngrok_suite()
    except Exception as exc:
        print(f"Live ngrok suite aborted: {exc!r}", flush=True)
        live_report = {"error": repr(exc)}

    summary: dict[str, Any] = {
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gateway_url": NGROK_HTTP,
        "total_scenarios": total_88k,
        "passed_scenarios": passed_88k,
        "overall_accuracy_pct": round(passed_88k / total_88k * 100.0, 2),
        "matrix_elapsed_s": round(eval_elapsed_s, 2),
        "matrix_throughput_scenarios_per_s": round(total_88k / eval_elapsed_s, 1),
        "by_language": {
            lang: {
                "total": v["total"],
                "passed": v["passed"],
                "accuracy_pct": round(v["passed"] / v["total"] * 100.0, 2),
                "p50_ms": pct(v["latencies_ms"], 0.50),
                "p95_ms": pct(v["latencies_ms"], 0.95),
            }
            for lang, v in by_lang.items()
        },
        "by_modality": {
            mod: {
                "total": v["total"],
                "passed": v["passed"],
                "accuracy_pct": round(v["passed"] / v["total"] * 100.0, 2),
                "p50_ms": pct(v["latencies_ms"], 0.50),
                "p95_ms": pct(v["latencies_ms"], 0.95),
            }
            for mod, v in by_modality.items()
        },
        "by_dimension": {
            dim: {
                "total": v["total"],
                "unique_inputs": len(v["unique_keys"]),
                "passed": v["passed"],
                "accuracy_pct": round(v["passed"] / v["total"] * 100.0, 2),
                "p50_ms": pct(v["latencies_ms"], 0.50),
                "p95_ms": pct(v["latencies_ms"], 0.95),
            }
            for dim, v in by_dimension.items()
        },
        "live_ngrok_suite": live_report,
    }

    out_path = REPO_ROOT / "docs" / "Reports" / "data" / "ura_88000_cx_multimodal_ngrok_report_2026_10_28.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved telemetry JSON -> {out_path} ({out_path.stat().st_size} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
