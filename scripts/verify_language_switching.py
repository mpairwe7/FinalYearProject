#!/usr/bin/env python3
"""Language Switching & Cross-Lingual Context Verification Script.

Tests:
1. Multi-turn language switching across conversation turns (EN -> LG -> SW -> EN)
   with conversational entity and context preservation across language boundaries.
2. Auto-detection language switching (when caller sends default locale en but text is LG/SW/EN).
3. Evaluates accuracy, correctness (grounded citations, domain facts), and response language.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any

BASE_URL = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
}

MARKERS = {
    "en": ("the", "and", "you", "your", "for", "is", "are", "to", "of", "a", "must", "tax", "please", "can"),
    "lg": ("oba", "nga", "era", "eri", "kye", "bye", "gwa", "eby", "omu", "aba", "oku", "okw", "ekya", "ssente", "musolo", "wano", "buli"),
    "sw": ("ya", "wa", "kwa", "ni", "katika", "una", "kama", "hii", "kodi", "lazima", "unaweza", "yako", "kuwa", "je", "asilimia", "na"),
}


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S"); print(f"[{ts}] {msg}", flush=True)


def classify_text(text: str) -> str:
    words = [w.strip(".,;:?!\"\x27()").lower() for w in (text or "").split()]
    if not words:
        return "unknown"
    scores = {}
    for lang, mset in MARKERS.items():
        hits = sum(1 for w in words if w in mset)
        scores[lang] = hits / len(words)
    best = max(scores, key=lambda k: scores[k])
    if scores[best] < 0.015:
        return "unknown"
    return best


def post_chat(msg: str, locale: str = "en", session_id: str | None = None) -> tuple[int, dict[str, Any], float]:
    url = f"{BASE_URL}/api/v1/chat"
    payload = {"message": msg, "locale": locale}
    if session_id:
        payload["conversation_id"] = session_id

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            elapsed = time.perf_counter() - t0
            return resp.status, json.loads(resp.read().decode("utf-8")), elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.perf_counter() - t0
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(err_body), elapsed
        except Exception:
            return e.code, {"error": err_body}, elapsed


def test_cross_lingual_multiturn() -> bool:
    log("=== Test 1: Cross-Lingual Multi-Turn Conversation ===")
    session_id = f"lang-switch-{int(time.time())}"
    log(f"Session ID: {session_id}")

    turns = [
        {
            "turn": 1,
            "send_locale": "en",
            "expect_lang": "en",
            "message": "What is EFRIS and who is required to use it in Uganda?",
            "expected_keywords": ["electronic", "fiscal", "efris", "vat", "business", "invoice"],
            "desc": "Turn 1 (English anchor): Introduce EFRIS",
        },
        {
            "turn": 2,
            "send_locale": "lg",
            "expect_lang": "lg",
            "message": "Nnyinza ntya okukozesa enkola eyo ku ssimu yange ey\x27omu ngalo?",
            "expected_keywords": ["efris", "ssimu", "app", "omukutu", "pulogulaamu", "play store", "download", "kozesa", "smart"],
            "desc": "Turn 2 (Switch to Luganda): \x27enkola eyo\x27 (that system) on mobile phone",
        },
        {
            "turn": 3,
            "send_locale": "sw",
            "expect_lang": "sw",
            "message": "Je, ni adhabu gani zitatolewa ikiwa sitatoa risiti kupitia mfumo huo?",
            "expected_keywords": ["adhabu", "faini", "kodi", "risiti", "mfumo", "efris", "shilingi", "asilimia"],
            "desc": "Turn 3 (Switch to Kiswahili): \x27mfumo huo\x27 (that system) penalties",
        },
        {
            "turn": 4,
            "send_locale": "en",
            "expect_lang": "en",
            "message": "Switching back to English: summarize the system name and penalties discussed above.",
            "expected_keywords": ["efris", "electronic", "fiscal", "penalt", "double", "currency", "receipt"],
            "desc": "Turn 4 (Switch back to English): Cross-lingual synthesis & context recall",
        },
    ]

    all_passed = True
    for item in turns:
        t_num = item["turn"]
        loc = item["send_locale"]
        exp_lang = item["expect_lang"]
        msg = item["message"]
        desc = item["desc"]
        kw_list = item["expected_keywords"]

        log(f"User [{loc}]: {msg}")
        status, body, elapsed = post_chat(msg, locale=loc, session_id=session_id)
        assert status == 200, f"Turn {t_num} failed with status {status}: {body}"

        reply = body.get("reply", "")
        reported_loc = body.get("locale", "unknown")
        detected_lang = classify_text(reply)
        sources = body.get("sources", [])
        retrieval_mode = body.get("retrieval_mode", "unknown")

        log(f"Status: {status} ({elapsed:.2f}s) | Mode: {retrieval_mode} | Sources: {len(sources)}")
        log(f"Reported Locale: {reported_loc} | Linguistic Classifier: {detected_lang}")
        log(f"Reply: {reply[:180]}...")

        # Verify language match
        lang_match = (reported_loc == exp_lang) or (detected_lang == exp_lang)
        if not lang_match:
            log(f"⚠️ Language mismatch: expected {exp_lang}, reported {reported_loc}, classified {detected_lang}")
            all_passed = False

        # Verify domain keyword presence
        matched_kw = [k for k in kw_list if k.lower() in reply.lower()]
        log(f"Domain keywords matched ({len(matched_kw)}/{len(kw_list)}): {matched_kw}")
        if len(matched_kw) < 1:
            log(f"⚠️ Low keyword recall: {matched_kw} from expected {kw_list}")
            all_passed = False
        time.sleep(1.5)

    if all_passed:
        log("\n✅ Cross-Lingual Multi-Turn Conversation PASSED!\n")
    else:
        log("\n❌ Cross-Lingual Multi-Turn Conversation had warnings/failures.\n")
    return all_passed


def test_auto_detection() -> bool:
    log("=== Test 2: Auto Language Detection (Caller sends locale=\x27en\x27) ===")
    test_cases = [
        {
            "query": "Omusolo gwa VAT mu Uganda guli ku bitundu bimeka?",
            "expected_locale": "lg",
            "desc": "Luganda VAT rate question",
        },
        {
            "query": "Kiwango cha kodi ya VAT nchini Uganda ni asilimia ngapi?",
            "expected_locale": "sw",
            "desc": "Kiswahili VAT rate question",
        },
        {
            "query": "What is the standard VAT rate in Uganda?",
            "expected_locale": "en",
            "desc": "English VAT rate question",
        },
    ]

    all_passed = True
    for case in test_cases:
        query = case["query"]
        expected = case["expected_locale"]
        desc = case["desc"]
        log(f"Query ({desc}): {query}")

        status, body, elapsed = post_chat(query, locale="en")
        assert status == 200, f"Failed with status {status}: {body}"

        reply = body.get("reply", "")
        reported_loc = body.get("locale", "unknown")
        detected_lang = classify_text(reply)

        log(f"Elapsed: {elapsed:.2f}s | Reported Locale: {reported_loc} | Text Classifier: {detected_lang}")
        log(f"Reply: {reply[:140]}...")

        if reported_loc == expected or detected_lang == expected:
            log(f"✅ Correctly identified and answered in {expected}")
        else:
            log(f"❌ Auto-detection mismatch: expected {expected}, got {reported_loc}")
            all_passed = False
        time.sleep(1.5)

    if all_passed:
        log("\n✅ Auto Language Detection PASSED!\n")
    return all_passed


def main() -> int:
    log(f"Starting Language Switching Verification on {BASE_URL}")
    ok1 = test_cross_lingual_multiturn()
    ok2 = test_auto_detection()
    if ok1 and ok2:
        log("🎉 ALL LANGUAGE SWITCHING TESTS PASSED!")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
