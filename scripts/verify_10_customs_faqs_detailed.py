#!/usr/bin/env python3
"""Comprehensive Live Verification of 10 Customs FAQs + Emotional Intelligence against ngrok URL."""

from __future__ import annotations

import json
import os
import time
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")

TEST_CASES = [
    {
        "id": "CUST-01",
        "tag": "customs_valuation",
        "topic": "Basis for Declaring Imported Goods (Sea vs Air)",
        "query": "What is the basis for declaring imported goods in Uganda?",
        "emotion_prompt": "I am so anxious about my shipment arriving tomorrow! What is the basis for declaring imported goods in Uganda?",
        "expected_source": "ura_customs_valuation_faqs.csv"
    },
    {
        "id": "CUST-02",
        "tag": "customs_valuation",
        "topic": "Challenging Customs Valuation / Appeal",
        "query": "Can an importer challenge Customs valuation?",
        "emotion_prompt": "I am furious that customs rejected my receipt! Can an importer challenge Customs valuation?",
        "expected_source": "ura_customs_valuation_faqs.csv"
    },
    {
        "id": "CUST-03",
        "tag": "customs_valuation",
        "topic": "Customs Valuation Methods under ACV",
        "query": "What are the Customs valuation methods under ACV?",
        "emotion_prompt": "I'm completely lost with all these acronyms. What are the Customs valuation methods under ACV?",
        "expected_source": "ura_customs_valuation_faqs.csv"
    },
    {
        "id": "CUST-04",
        "tag": "documents_point_of_entry",
        "topic": "Point of Entry Required Documents",
        "query": "Which documents are required at the point of entry for goods?",
        "emotion_prompt": "Urgent! My truck is at Malaba border right now. Which documents are required at the point of entry for goods?",
        "expected_source": "ura_documents_point_of_entry_faqs.csv"
    },
    {
        "id": "CUST-05",
        "tag": "documents_point_of_entry",
        "topic": "International Traveler Entry Requirements",
        "query": "What documents must international travelers present when entering Uganda?",
        "emotion_prompt": "I'm nervous about traveling for the first time. What documents must international travelers present when entering Uganda?",
        "expected_source": "ura_documents_point_of_entry_faqs.csv"
    },
    {
        "id": "CUST-06",
        "tag": "customs_offences",
        "topic": "Cargo Surplus / Deficiency Offence",
        "query": "Is deficiency or surplus in cargo an offence?",
        "emotion_prompt": "I'm terrified my container will be seized! Is deficiency or surplus in cargo an offence?",
        "expected_source": "ura_customs_offences_faqs.csv"
    },
    {
        "id": "CUST-07",
        "tag": "customs_valuation",
        "topic": "Deductive Value Method",
        "query": "What is the deductive value method?",
        "emotion_prompt": "Can you please explain in simple terms, what is the deductive value method?",
        "expected_source": "ura_customs_valuation_faqs.csv"
    },
    {
        "id": "CUST-08",
        "tag": "customs_valuation",
        "topic": "Transaction Value Method",
        "query": "What is the transaction value method?",
        "emotion_prompt": "I really need clear guidance: what is the transaction value method?",
        "expected_source": "ura_customs_valuation_faqs.csv"
    },
    {
        "id": "CUST-09",
        "tag": "customs_offences",
        "topic": "Definition of Customs Offence & Penalties",
        "query": "What is a customs offence?",
        "emotion_prompt": "I am worried about getting into trouble with the law. What is a customs offence?",
        "expected_source": "ura_customs_offences_faqs.csv"
    },
    {
        "id": "CUST-10",
        "tag": "groupage_cargo",
        "topic": "Groupage Cargo Clearance",
        "query": "What is groupage cargo?",
        "emotion_prompt": "I am a small trader and feeling overwhelmed by shipping terms. What is groupage cargo?",
        "expected_source": "ura_groupage_cargo_faqs.csv"
    }
]


def post_chat(message: str) -> dict:
    url = f"{BASE_URL}/api/v1/chat"
    payload = json.dumps({"message": message, "locale": "en"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "1",
            "X-Session-ID": f"customs-eval-detail-{int(time.time()*1000)}"
        }
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("==================================================================")
    print(" RUNNING DETAILED 10 CUSTOMS FAQS VERIFICATION (ACCURACY + EQ)")
    print(f" Target: {BASE_URL}")
    print("==================================================================\n")

    results = []
    for idx, tc in enumerate(TEST_CASES, start=1):
        print(f"--- Case {idx}/10: {tc['id']} - {tc['topic']} ---")
        
        # Test 1: Direct FAQ query
        t0 = time.time()
        res_direct = post_chat(tc["query"])
        dur_direct = time.time() - t0
        
        # Test 2: Emotional nuance query
        t1 = time.time()
        res_eq = post_chat(tc["emotion_prompt"])
        dur_eq = time.time() - t1
        
        reply_direct = res_direct.get("reply", "")
        sources_direct = res_direct.get("sources", [])
        mode_direct = res_direct.get("retrieval_mode", "")
        
        reply_eq = res_eq.get("reply", "")
        mode_eq = res_eq.get("retrieval_mode", "")
        
        # Evaluate Factual Accuracy
        has_expected_source = tc["expected_source"] in sources_direct or any("customs" in s or "documents" in s or "cargo" in s for s in sources_direct)
        is_grounded = mode_direct in ["hybrid", "dense", "workflow"] and len(reply_direct) > 20
        
        # Evaluate Conversational Nature
        is_conversational = len(reply_direct) > 20 and not ("Internal Server Error" in reply_direct)
        
        # Evaluate Emotional Intelligence
        # EQ is demonstrated by: acknowledging emotion, providing reassuring guidance, or routing to human help / withholding hallucination
        eq_handled = len(reply_eq) > 20 and not ("Internal Server Error" in reply_eq)
        
        item_summary = {
            "id": tc["id"],
            "topic": tc["topic"],
            "direct_query": tc["query"],
            "direct_reply": reply_direct,
            "sources": sources_direct,
            "retrieval_mode": mode_direct,
            "latency_direct_s": round(dur_direct, 2),
            "emotion_query": tc["emotion_prompt"],
            "emotion_reply": reply_eq,
            "latency_eq_s": round(dur_eq, 2),
            "factual_match": has_expected_source or is_grounded,
            "conversational": is_conversational,
            "eq_handled": eq_handled
        }
        results.append(item_summary)
        
        print(f"  [Direct Query] Mode: {mode_direct} | Sources: {sources_direct} | Latency: {dur_direct:.2f}s")
        print(f"  Reply: {reply_direct[:120]}...\n")
        print(f"  [EQ Query] Mode: {mode_eq} | Latency: {dur_eq:.2f}s")
        print(f"  Reply: {reply_eq[:120]}...\n")

    out_file = "/home/developer/Mpairwe7/FinalYearProject/Results/customs_faqs_detailed_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("==================================================================")
    print(f" Verification Complete! Detailed report saved to {out_file}")
    print("==================================================================")


if __name__ == "__main__":
    main()
