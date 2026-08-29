#!/usr/bin/env python3
"""Live Verification of 10 Customs FAQs over ngrok URL.

Evaluates:
  1. Correctness of Reply (Factual accuracy against URA Customs / EACCMA regulations)
  2. Conversational Nature (Clarity, plain language, structure, actionable next steps)
  3. Emotional Intelligence (Empathy acknowledgement, distress recognition, supportive de-escalation)

Target URL: https://struttingly-nongeological-briella.ngrok-free.dev
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
import urllib.request
import urllib.error

BASE_URL = os.environ.get("BASE_URL", "https://struttingly-nongeological-briella.ngrok-free.dev")

CUSTOMS_FAQS = [
    {
        "id": "CUST-01",
        "topic": "Customs Valuation Basis (Sea/Road vs Air)",
        "user_emotion": "Neutral / Factual Inquiry",
        "query": "What is the basis for declaring imported goods for customs valuation in Uganda? Is freight included for both air and sea cargo?",
        "expected_facts": [
            "CIF basis for sea/road imports (cost, insurance, freight)",
            "Air cargo customs value uses cost and insurance only (freight excluded)"
        ],
        "eq_expectation": "Direct, structured, clear professional guidance"
    },
    {
        "id": "CUST-02",
        "topic": "Challenging Customs Valuation / Appeal",
        "user_emotion": "Frustrated / Disputing Valuation",
        "query": "Customs rejected my invoice price at the border and valued my goods way higher! I am so frustrated. Can I challenge this customs valuation or am I forced to pay?",
        "expected_facts": [
            "Importers have the right to challenge/appeal customs valuation under ACV / EACCMA",
            "Administrative and judicial appeal without penalty",
            "Objection/dispute procedure"
        ],
        "eq_expectation": "Empathetic acknowledgment of frustration, reassuring tone, clear step-by-step dispute process"
    },
    {
        "id": "CUST-03",
        "topic": "Valuation Methods under ACV",
        "user_emotion": "Confused Trader",
        "query": "I am completely confused about how URA determines customs value when transaction value cannot be used. What are the sequential methods under ACV?",
        "expected_facts": [
            "Six sequential valuation methods under ACV / EACCMA Section 122",
            "Transaction value of identical goods, similar goods, deductive value, computed value, fallback method"
        ],
        "eq_expectation": "Patient, de-mystifying explanation, breaking down complex sequential methods into easy plain language"
    },
    {
        "id": "CUST-04",
        "topic": "Required Documents at Point of Entry",
        "user_emotion": "Urgent Importer",
        "query": "Urgent help needed! My container just arrived at the border point of entry. Exactly what documents must I present to customs to clear the goods?",
        "expected_facts": [
            "Commercial invoice, Bill of Lading / Airway Bill, packing list",
            "Certificate of origin, permits (if restricted), proof of payment/insurance"
        ],
        "eq_expectation": "High urgency acknowledgment, rapid checklist format for immediate action"
    },
    {
        "id": "CUST-05",
        "topic": "Passenger Baggage Duty-Free Allowance",
        "user_emotion": "Anxious Traveler",
        "query": "I am flying into Entebbe airport tonight with personal gifts for my family and I am terrified of getting stopped and penalized. What passenger baggage items are duty-free?",
        "expected_facts": [
            "Accompanied personal luggage/effects are duty-free",
            "Specific statutory passenger allowance limits for gifts/articles"
        ],
        "eq_expectation": "Reassuring tone calming traveler anxiety, clear distinction between personal effects and commercial quantities"
    },
    {
        "id": "CUST-06",
        "topic": "Customs Offences and Seizure of Goods",
        "user_emotion": "Distressed Importer / Potential Penalty",
        "query": "Please help, I am panicking! Customs detained my shipment claiming undeclared surplus goods. Is surplus cargo considered a customs offence and could my goods be forfeited?",
        "expected_facts": [
            "Surplus (unmanifested) or deficiency in reported cargo is an offence under EACCMA",
            "Goods may be liable to forfeiture or penalties",
            "Proper explanation and formal clearance steps"
        ],
        "eq_expectation": "Calming distress, clear factual statement of the law without shaming, constructive guidance on resolving with proper officer"
    },
    {
        "id": "CUST-07",
        "topic": "Deductive vs Computed Value Methods",
        "user_emotion": "Neutral / Clarification",
        "query": "Can you explain the difference between the Deductive Value method and the Computed Value method in customs valuation?",
        "expected_facts": [
            "Deductive value starts from domestic resale price minus post-import costs/profits",
            "Computed value builds up from cost of materials, fabrication, and manufacturer profit"
        ],
        "eq_expectation": "Crisp side-by-side comparison, easily understandable without heavy accounting jargon"
    },
    {
        "id": "CUST-08",
        "topic": "International Traveler Entry Requirements",
        "user_emotion": "Traveler Preparation",
        "query": "What health and immigration documents must international travelers present when entering Uganda through Entebbe airport?",
        "expected_facts": [
            "Valid passport (at least 6 months validity)",
            "Appropriate visa (e-visa or eligible entry)",
            "Yellow fever vaccination certificate"
        ],
        "eq_expectation": "Friendly, welcoming, organized travel checklist"
    },
    {
        "id": "CUST-09",
        "topic": "Export Procedures and Documentation",
        "user_emotion": "First-time Small Exporter",
        "query": "I am a first-time local farmer trying to export dried fruits to Kenya. The customs process feels so intimidating. What basic export procedures must I follow?",
        "expected_facts": [
            "Export entry declaration in customs system (Asycuda/Uganda Electronic Single Window)",
            "Commercial invoice, packing list, certificate of origin, phytosanitary certificate",
            "Verification and customs release"
        ],
        "eq_expectation": "Encouraging, supportive tone for a small business/farmer, simplifying the export journey into manageable steps"
    },
    {
        "id": "CUST-10",
        "topic": "Authorised Economic Operator (AEO) Benefits",
        "user_emotion": "Growing Business Owner",
        "query": "My logistics company is handling high trade volumes and customs delays are costing us money. What is the Authorised Economic Operator (AEO) scheme and what benefits does URA offer?",
        "expected_facts": [
            "AEO is a trade facilitation program for compliant businesses under WCO SAFE framework",
            "Benefits include expedited clearance, priority inspection, simplified procedures, pre-arrival processing"
        ],
        "eq_expectation": "Professional, business-enabling tone highlighting efficiency and compliance advantages"
    }
]


def post_chat(message: str, locale: str = "en") -> dict[str, Any]:
    url = f"{BASE_URL}/api/v1/chat"
    payload = json.dumps({"message": message, "locale": locale}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "1",
            "X-Session-ID": f"customs-eval-{int(time.time()*1000)}"
        }
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def evaluate_response(faq: dict[str, Any], resp: dict[str, Any]) -> dict[str, Any]:
    reply_text = resp.get("reply", "")
    sources = resp.get("sources", [])
    model = resp.get("model", "unknown")
    retrieval_mode = resp.get("retrieval_mode", "unknown")
    
    # 1. Correctness check
    facts_present = []
    for fact in faq["expected_facts"]:
        # Keyword checks
        keywords = [w.lower() for w in fact.split() if len(w) > 4]
        matches = sum(1 for kw in keywords if kw in reply_text.lower())
        present = matches >= max(1, len(keywords) // 3)
        facts_present.append(present)
    
    correctness_score = sum(facts_present) / max(1, len(facts_present))
    
    # 2. Conversational Nature check
    # Check length, structure (paragraphs, bullet points, headers), plain language vs robotic jargon
    has_structure = any(marker in reply_text for marker in ["\n", "*", "-", "1.", "2."])
    is_adequate_length = len(reply_text.split()) >= 25
    not_too_verbose = len(reply_text.split()) <= 450
    has_actionable_guidance = any(term in reply_text.lower() for term in ["step", "must", "can", "submit", "apply", "appeal", "present", "ensure", "contact", "officer", "help", "register"])
    
    conv_score = (
        (0.35 if has_structure else 0.1) +
        (0.35 if is_adequate_length and not_too_verbose else 0.1) +
        (0.30 if has_actionable_guidance else 0.1)
    )
    
    # 3. Emotional Intelligence check
    emotion_type = faq["user_emotion"]
    has_empathy_or_reassurance = any(kw in reply_text.lower() for kw in [
        "understand", "frustrat", "don't worry", "worry", "panic", "calm", "happy to", "help", "assist", "please note",
        "right to", "not to worry", "clear", "relief", "support", "reassur", "guide", "officer"
    ])
    
    # If distressed/frustrated, check de-escalation & recourse
    if any(e in emotion_type.lower() for e in ["frustrated", "anxious", "panicking", "distressed", "urgent", "confused", "terrified", "intimidating"]):
        has_eq_feature = has_empathy_or_reassurance or ("appeal" in reply_text.lower() or "dispute" in reply_text.lower() or "officer" in reply_text.lower() or "contact" in reply_text.lower())
        eq_score = 0.95 if (has_empathy_or_reassurance and has_eq_feature) else (0.80 if has_eq_feature else 0.60)
    else:
        eq_score = 0.90 if has_structure and not ("error" in reply_text.lower()) else 0.75
        
    return {
        "id": faq["id"],
        "topic": faq["topic"],
        "user_emotion": faq["user_emotion"],
        "query": faq["query"],
        "reply": reply_text,
        "sources": sources,
        "model": model,
        "retrieval_mode": retrieval_mode,
        "correctness_score": round(correctness_score, 2),
        "conversational_score": round(conv_score, 2),
        "eq_score": round(eq_score, 2),
        "overall_score": round((correctness_score * 0.4 + conv_score * 0.3 + eq_score * 0.3), 2)
    }


def main():
    print(f"==================================================================")
    print(f" Running Live Evaluation of 10 Customs FAQs against ngrok URL")
    print(f" Target: {BASE_URL}")
    print(f"==================================================================\n")
    
    results = []
    for idx, faq in enumerate(CUSTOMS_FAQS, start=1):
        print(f"[{idx}/10] Testing {faq['id']}: {faq['topic']} ({faq['user_emotion']})...")
        t0 = time.time()
        try:
            resp = post_chat(faq["query"])
            lat = time.time() - t0
            eval_res = evaluate_response(faq, resp)
            eval_res["latency_s"] = round(lat, 2)
            results.append(eval_res)
            print(f"       Latency: {eval_res['latency_s']}s | Correctness: {eval_res['correctness_score']*100:.0f}% | Conversational: {eval_res['conversational_score']*100:.0f}% | EQ: {eval_res['eq_score']*100:.0f}% | Score: {eval_res['overall_score']*100:.0f}%\n")
        except Exception as e:
            print(f"       ERROR: {e}\n")
            results.append({
                "id": faq["id"],
                "topic": faq["topic"],
                "user_emotion": faq["user_emotion"],
                "query": faq["query"],
                "reply": f"Error: {e}",
                "sources": [],
                "model": "error",
                "retrieval_mode": "error",
                "correctness_score": 0.0,
                "conversational_score": 0.0,
                "eq_score": 0.0,
                "overall_score": 0.0,
                "latency_s": round(time.time() - t0, 2)
            })

    # Summary
    avg_correctness = sum(r["correctness_score"] for r in results) / len(results)
    avg_conv = sum(r["conversational_score"] for r in results) / len(results)
    avg_eq = sum(r["eq_score"] for r in results) / len(results)
    avg_overall = sum(r["overall_score"] for r in results) / len(results)
    avg_lat = sum(r["latency_s"] for r in results) / len(results)

    output_path = "/home/developer/Mpairwe7/FinalYearProject/Results/customs_faqs_ngrok_eval.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_faqs": len(results),
                "avg_correctness_pct": round(avg_correctness * 100, 1),
                "avg_conversational_pct": round(avg_conv * 100, 1),
                "avg_eq_pct": round(avg_eq * 100, 1),
                "avg_overall_pct": round(avg_overall * 100, 1),
                "avg_latency_s": round(avg_lat, 2)
            },
            "evaluations": results
        }, f, indent=2, ensure_ascii=False)

    print(f"==================================================================")
    print(f" Evaluation Complete!")
    print(f" - Average Correctness:    {avg_correctness * 100:.1f}%")
    print(f" - Average Conversational: {avg_conv * 100:.1f}%")
    print(f" - Average Emotional Int:  {avg_eq * 100:.1f}%")
    print(f" - Overall Mean Score:     {avg_overall * 100:.1f}%")
    print(f" - Average Latency:        {avg_lat:.2f}s")
    print(f" Report saved to: {output_path}")
    print(f"==================================================================")


if __name__ == "__main__":
    main()
