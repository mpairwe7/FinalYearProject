"""Export thumbs-down + officer replies as preference pairs (G29 slice).

Does not fine-tune. Writes JSONL a DPO/KTO job can consume later.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))

from app import database as db  # noqa: E402

#: Golden statutory anchor preference pairs replayed at ratio gamma=0.5 per DICE (ICLR 2025)
#: and LoRR Reset Replay (arXiv:2508.06412) to prevent catastrophic forgetting of Ugandan statutory rates.
_GOLDEN_STATUTORY_REPLAY_ANCHORS: tuple[dict[str, Any], ...] = (
    {
        "prompt": "What is the standard VAT rate and annual registration threshold in Uganda for FY2026-27?",
        "chosen": "In Uganda for FY2026-27, the standard Value Added Tax (VAT) rate is 18% and the mandatory annual VAT registration threshold is UGX 300,000,000 (Value Added Tax (Amendment) Act 2026).",
        "rejected": "The VAT rate in Uganda is 16% and the registration threshold is UGX 50,000,000.",
        "source": "golden_statutory_replay_anchor",
        "domain": "vat",
    },
    {
        "prompt": "How much Presumptive Tax does a small business with annual turnover of UGX 8,500,000 pay?",
        "chosen": "Under the Income Tax Act (Cap. 338) Second Schedule, a small business in Uganda with annual gross turnover not exceeding UGX 10,000,000 is in the 0% tax band and pays UGX 0 in Presumptive Tax.",
        "rejected": "All small businesses must pay 1.5% turnover tax even if their turnover is below UGX 10,000,000.",
        "source": "golden_statutory_replay_anchor",
        "domain": "presumptive_tax",
    },
    {
        "prompt": "How can I waive late-filing penalties if I declare omitted tax before a URA audit notice?",
        "chosen": "Under Section 66(1a) of the Tax Procedures Code Act (Cap. 343), submitting a Voluntary Disclosure on Form DT-10307 before a URA audit notice waives 100% of penal tax and late-payment interest upon paying the principal tax.",
        "rejected": "URA never waives penalties once a return is late.",
        "source": "golden_statutory_replay_anchor",
        "domain": "voluntary_disclosure",
    },
)

_STATUTORY_SIGNAL_RE = re.compile(
    r"\b(?:s\.\s*\d+[a-z0-9()]*|section\s+\d+|form\s+dt[-\s]?\d+|ugx\s*[\d,]+|\d+(?:\.\d+)?%|cap\.?\s*\d+)",
    re.IGNORECASE,
)
_PII_NIN_RE = re.compile(r"\bC[MFR][-\s]?[A-Z0-9]{11,13}\b", re.IGNORECASE)
_PII_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PII_PHONE_RE = re.compile(r"(?:\+256|(?<!\d)256|(?<!\d)0)[ \t-]?7\d{2}[ \t-]?\d{3}[ \t-]?\d{3}\b")


def _scrub_preference_text(text: str) -> str:
    """Scrub taxpayer NINs, mobile numbers, and emails from preference pairs (ISO/IEC 42001 A.7.4)."""
    if not text:
        return ""
    cleaned = _PII_NIN_RE.sub("[REDACTED_NIN]", str(text))
    cleaned = _PII_EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    cleaned = _PII_PHONE_RE.sub("[REDACTED_PHONE]", cleaned)
    return cleaned


def build_bilevel_preference_bundle(
    pairs: list[dict[str, Any]],
    *,
    replay_ratio: float = 0.5,
    max_length_ratio: float = 2.5,
) -> dict[str, Any]:
    """Construct a Bi-Level Preference Optimization (`BPO-RAG`, AAAI 2026) and `DICE`/`LoRR` replay bundle.

    - **Level 1 (`retrieval_and_router_preferences`)**: Extracts statutory citations, forms, rates,
      and UGX figures present in ``chosen`` officer replies but missing from ``rejected`` bot replies
      to supervise retriever/router hard-negative tuning (`DecEx-RAG`, arXiv:2510.05691).
    - **Level 2 (`dpo_length_debiased_replay`)**: Filters length-exploitation outliers
      (`len(chosen) / len(rejected) > max_length_ratio`) and mixes on-policy preference pairs with
      golden statutory replay anchors at ratio ``replay_ratio`` (`DICE`, ICLR 2025; `LoRR`, arXiv:2508.06412).
    """
    retrieval_prefs: list[dict[str, Any]] = []
    debiased_dpo: list[dict[str, Any]] = []
    length_filtered_count = 0

    for item in pairs:
        prompt = _scrub_preference_text(str(item.get("prompt") or "").strip())
        chosen = _scrub_preference_text(str(item.get("chosen") or "").strip())
        rejected = _scrub_preference_text(str(item.get("rejected") or "").strip())
        if not prompt or not rejected:
            continue

        chosen_signals = {m.group(0).strip() for m in _STATUTORY_SIGNAL_RE.finditer(chosen)} if chosen else set()
        rejected_signals = {m.group(0).strip() for m in _STATUTORY_SIGNAL_RE.finditer(rejected)}
        missing_signals = sorted(chosen_signals - rejected_signals)

        if missing_signals or item.get("source") == "thumbs_down":
            retrieval_prefs.append(
                {
                    "prompt": prompt,
                    "missing_statutory_signals": missing_signals,
                    "rejected_excerpt": rejected[:240],
                    "chosen_excerpt": chosen[:240] if chosen else "",
                    "source": item.get("source", "unknown"),
                }
            )

        if chosen:
            len_ratio = len(chosen) / max(1, len(rejected))
            if len_ratio > max_length_ratio:
                length_filtered_count += 1
                continue
            enriched = dict(item)
            enriched["prompt"] = prompt
            enriched["chosen"] = chosen
            enriched["rejected"] = rejected
            enriched["length_ratio"] = round(len_ratio, 3)
            debiased_dpo.append(enriched)

    # Experience Replay mix (gamma = replay_ratio) from golden statutory anchors
    clamped_gamma = max(0.0, min(1.0, float(replay_ratio)))
    replay_anchors: list[dict[str, Any]] = []
    if clamped_gamma > 0.0:
        target_anchors = max(1, int(round(len(debiased_dpo) * clamped_gamma))) if debiased_dpo else len(_GOLDEN_STATUTORY_REPLAY_ANCHORS)
        for idx in range(target_anchors):
            anchor = dict(_GOLDEN_STATUTORY_REPLAY_ANCHORS[idx % len(_GOLDEN_STATUTORY_REPLAY_ANCHORS)])
            anchor["length_ratio"] = round(len(anchor["chosen"]) / max(1, len(anchor["rejected"])), 3)
            replay_anchors.append(anchor)

    return {
        "total_raw_pairs": len(pairs),
        "length_filtered_count": length_filtered_count,
        "replay_ratio": clamped_gamma,
        "retrieval_and_router_preferences": retrieval_prefs,
        "dpo_length_debiased_replay": debiased_dpo + replay_anchors,
    }


def export_pairs(path: Path, *, tenant_id: str | None = None) -> int:
    db.init_db()
    rows = db.export_review_feedback() if hasattr(db, "export_review_feedback") else []
    tickets = []
    try:
        tickets = db.list_tickets(status="resolved", limit=500)
    except TypeError:
        tickets = db.list_tickets(status="resolved")
    pairs = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        rejected = str(row.get("bot_reply") or row.get("assistant") or "").strip()
        query = str(row.get("user_query") or row.get("query") or "").strip()
        if query and rejected:
            pairs.append(
                {
                    "prompt": query,
                    "rejected": rejected,
                    "source": "thumbs_down",
                }
            )
    for ticket in tickets or []:
        reply = str(ticket.get("officer_reply") or "").strip()
        query = str(ticket.get("user_query") or "").strip()
        rejected = str(ticket.get("bot_reply") or "").strip()
        if query and reply:
            pairs.append(
                {
                    "prompt": query,
                    "chosen": reply,
                    "rejected": rejected,
                    "source": "officer_reply",
                    "ticket_id": ticket.get("id"),
                }
            )
    from app import officer_chat

    page_size = 500
    offset = 0
    while True:
        batch = officer_chat.list_officer_preference_pairs(
            limit=page_size,
            tenant_id=tenant_id,
            offset=offset,
        )
        if not batch:
            break
        for pref in batch:
            chosen = str(pref.get("chosen_reply") or "").strip()
            rejected = str(pref.get("rejected_draft") or "").strip()
            prompt_ctx = str(pref.get("prompt_context") or pref.get("topic") or "").strip()
            if prompt_ctx and chosen and rejected:
                pairs.append(
                    {
                        "prompt": prompt_ctx,
                        "chosen": chosen,
                        "rejected": rejected,
                        "source": "officer_copilot_edit",
                        "ticket_id": pref.get("ticket_id"),
                        "tenant_id": pref.get("tenant_id", "default"),
                        "locale": pref.get("locale", "en"),
                        "edit_ratio": pref.get("edit_ratio", 0.0),
                    }
                )
        if len(batch) < page_size:
            break
        offset += len(batch)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for item in pairs:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(pairs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default=str(ROOT / "Data" / "eval" / "preference_pairs.jsonl"),
    )
    args = parser.parse_args()
    n = export_pairs(Path(args.output))
    print(f"wrote {n} pairs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
