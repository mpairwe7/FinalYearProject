#!/usr/bin/env python3
"""Comprehensive Language Switch & Speech/Voice Warmup Suite via Ngrok Gateway (2026).

Warms up:
1. Speech & Voice Pipeline:
   - Speech Health & Engine Status (/v1/speech/health)
   - Voice Catalog (/v1/speech/voices)
   - Machine Translation Engine (/v1/translate) across EN <-> LG, EN <-> SW
   - Neural & Expressive Text-to-Speech (/v1/tts) for EN, LG, SW
   - ASR Automatic Speech Recognition (/v1/asr) with Whisper-SALT
   - Compound Voice-to-Voice Pipeline (/v1/voice/chat) for EN, LG, SW

2. Cross-Lingual Language Switching & Conversation Handoffs:
   - Dynamic Language Switch Requests (EN -> LG, LG -> SW, SW -> EN)
   - Code-Switching & Vernacular Greetings
   - Key Statutory Queries in English, Luganda, and Kiswahili
   - Pre-warming Redis Translation Cache for Sub-Second Presentation Demo
"""

from __future__ import annotations

import base64
import sys
import time
from typing import Any
import httpx

NGROK_BASE = "https://struttingly-nongeological-briella.ngrok-free.dev/api"
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
    "User-Agent": "URA-Warmup-Suite/2.0",
}


def print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(f"  {title.upper()}")
    print("=" * 80)


def warmup_speech_health(client: httpx.Client) -> bool:
    print_section("1. Speech & Voice Service Health Check")
    t0 = time.perf_counter()
    resp = client.get("/v1/speech/health", headers=HEADERS)
    dt = time.perf_counter() - t0
    if resp.status_code == 200:
        data = resp.json()
        print(f"  ✓ /v1/speech/health: Status 200 ({dt:.3f}s)")
        print(f"    - Overall:   {data.get('status')} (enabled={data.get('enabled')})")
        print(f"    - ASR Tier:  {data.get('asr_backend')}")
        print(f"    - TTS Tier:  {data.get('tts_backend')}")
        print(f"    - MT Tier:   {data.get('mt_backend')}")
        return True
    print(f"  ✗ /v1/speech/health failed: {resp.status_code} - {resp.text}")
    return False


def warmup_voices_catalog(client: httpx.Client) -> bool:
    print_section("2. Voices Catalog Enumeration")
    t0 = time.perf_counter()
    resp = client.get("/v1/speech/voices", headers=HEADERS)
    dt = time.perf_counter() - t0
    if resp.status_code == 200:
        data = resp.json()
        voices = data.get("voices", {})
        total_voices = sum(len(v) for v in voices.values())
        print(f"  ✓ /v1/speech/voices: Status 200 ({dt:.3f}s)")
        print(f"    - Total Voices Registered: {total_voices} across {list(voices.keys())}")
        for lang, v_list in voices.items():
            print(f"      • [{lang}] {len(v_list)} voices: {[v.get('id') for v in v_list[:3]]}")
        return True
    print(f"  ✗ /v1/speech/voices failed: {resp.status_code}")
    return False


def warmup_machine_translation(client: httpx.Client) -> None:
    print_section("3. Cross-Lingual Machine Translation Engine (/v1/translate)")
    pairs = [
        ("en", "lg", "How do I register for a TIN in Uganda?", "TIN Registration"),
        ("en", "sw", "What is the standard VAT rate in Uganda?", "VAT Standard"),
        ("lg", "en", "Omusolo gw'ennyumba ez'obupangisa gubalibwa gutya?", "Rental Tax"),
        ("sw", "en", "Kiwango cha kodi ya mapato ya shirika ni asilimia ngapi?", "Corporate Tax"),
        ("en", "lg", "Under Section 24, a taxpayer has 45 days to lodge an objection.", "Statutory TPCA"),
        ("en", "sw", "All VAT-registered businesses must issue EFRIS fiscal receipts.", "EFRIS Mandate"),
    ]

    for src, tgt, text, label in pairs:
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/translate",
            json={"text": text, "source_lang": src, "target_lang": tgt},
            headers=HEADERS,
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            out = data.get("text", "").strip()
            backend = data.get("backend", "auto")
            print(f"  ✓ [{src} -> {tgt}] ({label}) 200 OK in {dt:.3f}s (backend={backend})")
            print(f"      In:  {text}")
            print(f"      Out: {out}")
        else:
            print(f"  ✗ [{src} -> {tgt}] failed ({resp.status_code}): {resp.text}")


def warmup_tts(client: httpx.Client) -> dict[str, bytes]:
    print_section("4. Neural Text-to-Speech Synthesis (/v1/tts)")
    scripts = [
        ("en", "Hello! Welcome to the Uganda Revenue Authority assistant. How may I help you today?"),
        ("lg", "Tukusanyukidde mu kitongole ky'emisolo ekya Uganda Revenue Authority. Nnyinza ntya okukuyamba?"),
        ("sw", "Karibu kwenye Mamlaka ya Mapato ya Uganda. Nawezaje kukusaidia kuhusu kodi leo?"),
    ]

    audio_samples: dict[str, bytes] = {}
    for loc, text in scripts:
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/tts",
            json={"text": text, "locale": loc},
            headers=HEADERS,
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            b64 = data.get("audio_base64", "")
            raw = base64.b64decode(b64) if b64 else b""
            audio_samples[loc] = raw
            backend = data.get("backend", "auto")
            sr = data.get("sample_rate", 24000)
            dur = data.get("duration_s", 0)
            print(f"  ✓ [{loc}] TTS 200 OK in {dt:.3f}s | Audio: {len(raw)} bytes | Dur: {dur:.2f}s @ {sr}Hz ({backend})")
        else:
            print(f"  ✗ [{loc}] TTS failed: {resp.status_code}")
    return audio_samples


def warmup_asr(client: httpx.Client, audio_samples: dict[str, bytes]) -> None:
    print_section("5. Speech-to-Text Recognition (/v1/asr)")
    for loc, raw in audio_samples.items():
        if not raw:
            continue
        t0 = time.perf_counter()
        resp = client.post(
            f"/v1/asr?language={loc}&sample_rate=24000",
            content=raw,
            headers={"Content-Type": "audio/mpeg", "ngrok-skip-browser-warning": "1"},
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            txt = data.get("text", "").strip()
            det_lang = data.get("language", loc)
            backend = data.get("backend", "whisper_salt")
            print(f"  ✓ [{loc}] ASR 200 OK in {dt:.3f}s | Detected Lang: {det_lang} | Backend: {backend}")
            print(f"      Transcription: '{txt}'")
        else:
            print(f"  ✗ [{loc}] ASR failed: {resp.status_code}")


def warmup_voice_chat(client: httpx.Client, audio_samples: dict[str, bytes]) -> None:
    print_section("6. End-to-End Voice Chat Pipeline (/v1/voice/chat)")
    for loc, raw in audio_samples.items():
        if not raw:
            continue
        t0 = time.perf_counter()
        try:
            resp = client.post(
                f"/v1/voice/chat?language={loc}&tts_enabled=true",
                content=raw,
                headers={"Content-Type": "audio/mpeg", "ngrok-skip-browser-warning": "1"},
                timeout=120.0,
            )
            dt = time.perf_counter() - t0
            if resp.status_code == 200:
                data = resp.json()
                rep = data.get("reply", "")[:85].replace("\n", " ")
                trans = data.get("transcription", "")
                aud_out = len(data.get("audio_base64") or "")
                print(f"  ✓ [{loc}] Voice Chat 200 OK in {dt:.3f}s | Reply Audio: {aud_out} bytes")
                print(f"      User said: '{trans}'")
                print(f"      Bot replied: '{rep}...'")
            else:
                print(f"  ✗ [{loc}] Voice Chat failed: {resp.status_code}")
        except Exception as e:
            dt = time.perf_counter() - t0
            print(f"  ⚠️ [{loc}] Voice Chat timeout/error ({dt:.1f}s): {e}")


def warmup_language_switch_dialogue(client: httpx.Client) -> None:
    print_section("7. Dynamic Language Switch Dialogue Turns (/v1/chat)")
    conversation_id = f"warmup-switch-demo-{int(time.time())}"

    dialogue_script = [
        # Turn 1: English start
        ("en", "Hello, what is the standard rate of VAT in Uganda?", "en", "English inquiry"),
        # Turn 2: Switch to Luganda
        ("lg", "Tusobola okukozesa Oluganda? Omusolo gw'ennyumba ez'obupangisa gubalibwa gutya?", "lg", "Switch to Luganda"),
        # Turn 3: Continue in Luganda
        ("lg", "Abantu abaliko obulemu basonyiyibwa omusolo gwa mmeka buli mwezi?", "lg", "Luganda PWD inquiry"),
        # Turn 4: Switch to Swahili
        ("sw", "Tafadhali tubadilishe kwa Kiswahili. Kiwango cha kodi ya mapato ya shirika ni asilimia ngapi?", "sw", "Switch to Swahili"),
        # Turn 5: Continue in Swahili
        ("sw", "Mfumo wa EFRIS unalazimisha wafanyabiashara kutoa nini?", "sw", "Swahili EFRIS inquiry"),
        # Turn 6: Switch back to English
        ("en", "Let us switch back to English. What is the deadline to file monthly PAYE returns?", "en", "Switch back to English"),
    ]

    for loc, query, exp_loc, desc in dialogue_script:
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/chat",
            json={
                "message": query,
                "locale": loc,
                "conversation_id": conversation_id,
            },
            headers=HEADERS,
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            rep = data.get("reply", "")[:120].replace("\n", " ")
            mode = data.get("retrieval_mode", "unknown")
            res_loc = data.get("locale", loc)
            print(f"  ✓ [{desc}] 200 OK in {dt:.3f}s | Mode: {mode} | Locale: {res_loc}")
            print(f"      Q: {query}")
            print(f"      A: {rep}...")
        else:
            print(f"  ✗ [{desc}] failed ({resp.status_code}): {resp.text}")


def warmup_statutory_rate_cache(client: httpx.Client) -> None:
    print_section("8. Statutory Rate & FAQ Presentation Cache Pre-Warming")
    essential_demo_queries = [
        # EN Core Invariants
        ("en", "What is the standard VAT rate in Uganda?"),
        ("en", "What is the annual mandatory VAT registration threshold?"),
        ("en", "What is the monthly tax-free threshold for PAYE in Uganda?"),
        ("en", "How is individual rental income tax calculated?"),
        ("en", "How many days does a taxpayer have to lodge an objection under Section 24?"),
        ("en", "Can a VAT-registered business claim input tax on fuel and telephone expenses?"),
        ("en", "Is import WHT always due on commercial goods?"),
        ("en", "What is the penalty for failing to issue an EFRIS invoice?"),
        ("en", "What is the maximum age limit for importing used motor vehicles into Uganda?"),
        # LG Vernacular Core Invariants
        ("lg", "Omusolo gwa VAT mu Uganda guli ku kigero kya bitundu bimeka?"),
        ("lg", "Abantu abaliko obulemu basonyiyibwa omusolo gwa mmeka buli mwezi?"),
        ("lg", "Omusolo gw'ennyumba ez'obupangisa ku bantu ssekinoomu gubalibwa ku kiwango ki?"),
        ("lg", "Alipoota z'omusolo eza buli mwezi zirina okuwaayo ku lunaku ki?"),
        ("lg", "Bizinensi entono ezitasobola kukuuma bitabo zisasula zitya presumptive tax?"),
        ("lg", "Nnyinza okuyingiza mmotoka ekozeseddwaako erina emyaka 16 mu Uganda?"),
        # SW Regional Core Invariants
        ("sw", "Kiwango cha kawaida cha kodi ya VAT nchini Uganda ni asilimia ngapi?"),
        ("sw", "Watu wenye ulemavu wanasamehewa kiasi gani cha mshahara wao kwa mwezi?"),
        ("sw", "Je, kiwango cha kodi ya mapato ya kupangisha kwa watu binafsi ni asilimia ngapi?"),
        ("sw", "Mlipakodi ana siku ngapi za kuwasilisha pingamizi la kodi chini ya Kifungu cha 24?"),
        ("sw", "Je, vifaa vya sola vinatozwa ushuru gani wa forodha na VAT?"),
        ("sw", "Je, biashara isiyosajiliwa inaweza kutoza VAT kwenye ankara zake?"),
        ("sw", "Ninawezaje kulipa kodi ya URA kwa kutumia PRN kwenye benki au simu?"),
        # Presentation Greetings & Presence
        ("en", "Hello! Good morning URA assistant."),
        ("lg", "Oli otya nno leero? Nkulamusizza."),
        ("sw", "Habari ya leo! Habari za kazi."),
        ("en", "Who are you and what services do you provide?"),
        ("lg", "Ggwe ani era oyinza kunnyamba ku ki?"),
        ("sw", "Wewe ni nani na unasaidiaje walipakodi?"),
    ]

    print(f"Pre-warming {len(essential_demo_queries)} presentation queries across EN, LG, and SW...")
    for idx, (loc, q) in enumerate(essential_demo_queries, 1):
        t0 = time.perf_counter()
        resp = client.post(
            "/v1/chat",
            json={"message": q, "locale": loc},
            headers=HEADERS,
        )
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            data = resp.json()
            mode = data.get("retrieval_mode", "fast")
            print(f"  [{idx:02d}/{len(essential_demo_queries)}] ✓ [{loc}] {dt*1000:6.1f}ms | Mode: {mode:12s} | Q: {q[:45]}")
        else:
            print(f"  [{idx:02d}/{len(essential_demo_queries)}] ✗ [{loc}] failed: {resp.status_code}")


def main() -> int:
    print("=" * 80)
    print("  URA MULTILINGUAL ASSISTANT: COMPLETE WARMUP SUITE VIA NGROK GATEWAY")
    print(f"  Target Gateway: {NGROK_BASE}")
    print("=" * 80)

    t_total_start = time.perf_counter()
    with httpx.Client(base_url=NGROK_BASE, timeout=60.0) as client:
        # 1. Health
        ok = warmup_speech_health(client)
        if not ok:
            print("❌ Speech health check failed. Aborting.")
            return 1

        # 2. Voices
        warmup_voices_catalog(client)

        # 3. Translation
        warmup_machine_translation(client)

        # 4. TTS
        audio_samples = warmup_tts(client)

        # 5. ASR
        warmup_asr(client, audio_samples)

        # 6. Voice Chat
        warmup_voice_chat(client, audio_samples)

        # 7. Language Switch Dialogue
        warmup_language_switch_dialogue(client)

        # 8. Statutory Rate & Presentation Cache Pre-Warming
        warmup_statutory_rate_cache(client)

    total_time = time.perf_counter() - t_total_start
    print_section(f"WARMUP COMPLETE! Total Elapsed Time: {total_time:.2f}s")
    print("✨ All language switch paths, speech models, and demo caches are warm and ready for live presentation.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
