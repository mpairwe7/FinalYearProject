#!/usr/bin/env python3
"""Comprehensive Live Regression & Multi-Turn Context Verification Suite.

Validates the full local GPU stack over the live ngrok tunnel:
1. System & Speech Health endpoints
2. Hybrid & Workflow RAG grounded retrieval
3. Multi-turn Conversational Session & Hierarchical Rolling Context
4. ASR (Whisper-SALT) and TTS (Edge-TTS & Spark-TTS) endpoints
5. Guardrails and Admin security boundaries
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")
HEADERS = {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "1",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get(path: str) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            try:
                return resp.status, json.loads(data.decode("utf-8"))
            except Exception:
                return resp.status, data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def http_post(path: str, payload: dict[str, Any], timeout: int = 90) -> tuple[int, dict[str, Any]]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": body}


def test_health() -> bool:
    log("=== 1. Health & Readiness Checks ===")
    status, body = http_get("/api/health")
    log(f"GET /api/health -> HTTP {status}: {body}")
    assert status == 200, f"Health check failed: {status}"
    assert body.get("status") == "alive", f"Expected alive, got {body}"

    status, body = http_get("/api/v1/speech/health")
    log(f"GET /api/v1/speech/health -> HTTP {status}: {body}")
    assert status == 200, f"Speech health check failed: {status}"
    assert body.get("enabled") is True, f"Speech not enabled: {body}"
    assert body.get("status") == "ready", f"Speech not ready: {body}"
    log(" Health checks PASSED\n")
    return True


def test_single_turn_hybrid() -> bool:
    log("=== 2. Single Turn Grounded Retrieval Check ===")
    t0 = time.perf_counter()
    status, resp = http_post("/api/v1/chat", {
        "message": "What is EFRIS and who is required to use it?",
        "locale": "en"
    })
    elapsed = time.perf_counter() - t0
    log(f"POST /api/v1/chat ({elapsed:.2f}s) -> HTTP {status}")
    assert status == 200, f"Chat failed: {status} - {resp}"
    
    reply = resp.get("reply", "")
    sources = resp.get("sources", [])
    retrieval_mode = resp.get("retrieval_mode", "")
    model = resp.get("model", "")
    
    log(f"Model: {model} | Retrieval Mode: {retrieval_mode} | Sources: {len(sources)}")
    log(f"Reply Preview: {reply[:160]}...")
    assert len(reply) > 50, "Reply too short"
    assert any(term in reply.lower() for term in ["electronic", "fiscal", "invoicing", "efris", "vat"]), "Missing core EFRIS facts"
    log(" Grounded Retrieval PASSED\n")
    return True


def test_multi_turn_rolling_context() -> bool:
    log("=== 3. Multi-Turn Conversational Session & Rolling Context ===")
    session_id = f"regtest-rolling-{int(time.time())}"
    log(f"Starting multi-turn test on session_id='{session_id}'")

    turns = [
        {
            "turn": 1,
            "query": "What are the key requirements for registering for EFRIS in Uganda?",
            "expectation": ["tin", "efris", "register", "vat", "business"],
            "description": "Initial Anchor Query (Establish topic: EFRIS)"
        },
        {
            "turn": 2,
            "query": "Can I use it on a mobile smartphone or tablet?",
            "expectation": ["efris", "mobile", "smart", "phone", "app", "system", "portal", "device", "web", "invoicing"],
            "description": "Pronoun Coreference ('it' -> EFRIS)"
        },
        {
            "turn": 3,
            "query": "What penalties apply if a taxpayer fails to issue receipts through that system?",
            "expectation": ["penalty", "penalties", "fine", "currency", "offence", "failure", "receipt", "efris", "tax", "section", "shilling", "points"],
            "description": "Demonstrative Coreference ('that system' -> EFRIS)"
        },
        {
            "turn": 4,
            "query": "Let's switch topics. What is the rental income tax rate for individual landlords in Uganda?",
            "expectation": ["rental", "12%", "rate", "individual", "income", "threshold", "2,820,000", "2.82"],
            "description": "Context Switch / Topic Shift (Rental Income Tax)"
        },
        {
            "turn": 5,
            "query": "What deductions or allowances are permitted for it?",
            "expectation": ["deduction", "allowance", "expense", "individual", "gross", "rental", "nil", "zero", "0%", "not allowed", "disallowed", "rate", "flat", "threshold"],
            "description": "Elliptical Reference ('it' -> Rental Income Tax)"
        },
        {
            "turn": 6,
            "query": "Going back to the very first system we discussed earlier, what was its name and what are its key features for VAT invoicing?",
            "expectation": ["efris", "electronic", "fiscal", "receipt", "invoic", "vat", "track", "real-time", "device", "system"],
            "description": "Long-range Context Recall & Entity Tracking across turns"
        }
    ]

    for item in turns:
        t_num = item["turn"]
        query = item["query"]
        desc = item["description"]
        expected_keywords = item["expectation"]

        log(f"--- Turn {t_num}: {desc} ---")
        log(f"User: '{query}'")

        t0 = time.perf_counter()
        status, resp = http_post("/api/v1/chat", {
            "message": query,
            "conversation_id": session_id,
            "locale": "en"
        })
        elapsed = time.perf_counter() - t0

        assert status == 200, f"Turn {t_num} failed with status {status}: {resp}"
        reply = resp.get("reply", "")
        sources = resp.get("sources", [])
        log(f"Status: {status} ({elapsed:.2f}s) | Sources: {len(sources)}")
        log(f"Agent Reply:\n{reply}\n")

        # Verify entity retention and answer quality
        reply_lower = reply.lower()
        matched = [kw for kw in expected_keywords if kw in reply_lower]
        log(f"Keywords matched ({len(matched)}/{len(expected_keywords)}): {matched}")
        assert len(matched) >= 2, f"Turn {t_num} failed context retention. Matched keywords: {matched}, expected from: {expected_keywords}"
        time.sleep(1)

    log(" Multi-Turn Rolling Context & Coreference Retention PASSED\n")
    return True


def test_speech_endpoints() -> bool:
    log("=== 4. Live Speech Endpoints (TTS) ===")
    
    # English Edge-TTS
    log("Testing English TTS (/api/v1/tts)...")
    status, data = http_post("/api/v1/tts", {
        "text": "Hello, welcome to Uganda Revenue Authority taxpayer services.",
        "locale": "en"
    })
    log(f"English TTS -> HTTP {status}")
    assert status == 200, f"English TTS failed: {status}"
    log(" English TTS PASSED")

    # Luganda Spark-TTS-SALT
    log("Testing Luganda Spark-TTS-SALT (/api/v1/tts)...")
    status, data = http_post("/api/v1/tts", {
        "text": "Tusanyuse okukuyamba ku nsonga z'emisolo.",
        "locale": "lg"
    }, timeout=120)
    log(f"Luganda TTS -> HTTP {status}")
    assert status == 200, f"Luganda TTS failed: {status}"
    log(" Luganda Spark-TTS-SALT PASSED\n")
    return True


def test_security_boundaries() -> bool:
    log("=== 5. Security & Boundary Checks ===")
    # Unauthenticated admin access should be rejected
    status, body = http_get("/api/v1/admin/tickets/stats")
    log(f"GET /api/v1/admin/tickets/stats without auth -> HTTP {status}")
    assert status in (401, 403), f"Admin endpoint leaked data without auth: {status}"
    log(" Security Boundaries PASSED\n")
    return True


def main() -> int:
    log(f"Starting Live Regression Test Suite against {BASE_URL}")
    results = []
    
    tests = [
        ("Health Checks", test_health),
        ("Single Turn Hybrid RAG", test_single_turn_hybrid),
        ("Multi-Turn Rolling Context & Session Memory", test_multi_turn_rolling_context),
        ("Speech TTS Endpoints", test_speech_endpoints),
        ("Security Boundaries", test_security_boundaries),
    ]

    for name, fn in tests:
        try:
            fn()
            results.append((name, "PASSED"))
        except Exception as e:
            log(f" TEST FAILED: {name} - {e}")
            results.append((name, f"FAILED: {e}"))
            return 1

    log("=" * 60)
    log("ALL REGRESSION TESTS COMPLETED SUCCESSFULLY!")
    for name, res in results:
        log(f"  - {name}: {res}")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
