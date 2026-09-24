"""End-of-call conversation summary generation using LLM with deterministic fallbacks."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .. import database as db
from ..guardrails import redact_pii_text
from .language import LANGUAGE_NAMES, lexical_hits
from .store import get_call, list_turns, update_call

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = (
    "You are a structured call summarization engine for Uganda Revenue Authority (URA). "
    "Analyze the transcript and output strictly valid JSON without conversational commentary. "
    "The transcript may be in English, Luganda or Swahili; write every field in English — "
    "the officers reading it work in English."
)

_SUMMARY_PROMPT_TEMPLATE = """Analyze the following phone conversation transcript between a Taxpayer and URA:

{transcript}

The call was in: {languages}. Write the summary in English regardless.

Return ONLY a JSON object matching this schema:
{{
  "subject": "Short 1-5 word topic",
  "summary": "2-3 sentence overview of the conversation and outcome",
  "caller_intent": "Core purpose or question of the caller",
  "resolution": "answered | transferred | unresolved | abandoned",
  "key_facts": ["List of relevant tax facts mentioned"],
  "follow_ups": ["List of follow-up steps for taxpayer or officer"],
  "sentiment": "positive | neutral | frustrated",
  "ai_handling_notes": "Brief note on AI performance, clarifications, or transfer reason"
}}
"""


def _parse_summary_json(raw: str) -> dict[str, Any] | None:
    """Parse JSON from model output, stripping code fences and extracting object."""
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M)
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        if isinstance(data, dict) and "summary" in data:
            return {
                "subject": str(data.get("subject", "URA Tax Query")),
                "summary": str(data.get("summary", "")),
                "caller_intent": str(data.get("caller_intent", "General inquiry")),
                "resolution": str(data.get("resolution", "unresolved")),
                "key_facts": list(data.get("key_facts", [])),
                "follow_ups": list(data.get("follow_ups", [])),
                "sentiment": str(data.get("sentiment", "neutral")),
                "ai_handling_notes": str(data.get("ai_handling_notes", "")),
            }
    except Exception:
        logger.debug("Failed parsing summary JSON", exc_info=True)
    return None


def _fallback_summary(turns: list[dict[str, Any]], call: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback summary when LLM generation fails or is offline."""
    caller_queries = [t["text"] for t in turns if t.get("speaker") == "caller"]
    first_q = caller_queries[0] if caller_queries else "Inquiry"
    first_q_brief = first_q[:60] + ("..." if len(first_q) > 60 else "")

    status = call.get("status", "ended")
    transferred = bool(call.get("transferred"))
    end_reason = call.get("end_reason", "normal")

    if transferred:
        resolution = "transferred"
    elif status == "ended" and caller_queries and any(t.get("kind") == "answer" for t in turns):
        resolution = "answered"
    elif end_reason in ("caller_hangup", "timeout"):
        resolution = "abandoned"
    else:
        resolution = "unresolved"

    return {
        "subject": first_q_brief,
        "summary": f"Taxpayer called regarding: {first_q}. Outcome: {resolution}.",
        "caller_intent": first_q_brief,
        "resolution": resolution,
        "key_facts": [],
        "follow_ups": ["Review ticket context in staff dashboard" if transferred else "No immediate action"],
        "sentiment": "neutral",
        "ai_handling_notes": f"Fallback template summary generated (end reason: {end_reason}).",
    }


def _summarise_gemini(prompt: str) -> dict[str, Any] | None:
    try:
        from ..providers.gateway import gemini_generate
        raw_reply = gemini_generate(
            prompt,
            system=SUMMARY_SYSTEM,
            model="gemini-2.5-flash",
            max_tokens=1500,
            temperature=0.0,
            locale="en",
        )
        return _parse_summary_json(raw_reply)
    except Exception:
        logger.debug("Gemini summary generation failed", exc_info=True)
        return None


def _summarise_local(prompt: str) -> dict[str, Any] | None:
    try:
        from ..llm import _vllm_generate
        # Chat messages, not a string: given a string, _vllm_generate's own
        # error handler returned "" and every Luganda summary silently fell
        # through to Gemini or the template.
        raw_reply = _vllm_generate(
            [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.0,
        )
        return _parse_summary_json(raw_reply)
    except Exception:
        logger.debug("Local LLM summary generation failed", exc_info=True)
        return None


def _call_languages(call: dict[str, Any], turns: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The call's language and every language it was in, oldest first.

    From the per-call metrics when the call recorded them (multilingual
    calls); otherwise from the row's locale — overruled by the transcript
    when the caller's own words are mostly Luganda, which is how a Luganda
    caller on a single-engine English call still gets a Sunflower summary.
    """
    metrics = call.get("metrics") or {}
    lang_metrics = metrics.get("language") if isinstance(metrics, dict) else None
    locale = str(call.get("locale") or "en")
    used = list((lang_metrics or {}).get("used") or [locale])
    language = str((lang_metrics or {}).get("final") or locale)
    caller_text = " ".join(str(t.get("text", "")) for t in turns if t.get("speaker") == "caller")
    hits = lexical_hits(caller_text)
    if language != "lg" and hits["lg"] > max(hits["en"], hits["sw"]) and hits["lg"] >= 3:
        language = "lg"
    if language not in used:
        used.append(language)
    return language, used


def generate_call_summary(call_id: str) -> dict[str, Any]:
    """Generate structured summary for a completed call and persist it."""
    call = get_call(call_id)
    if not call:
        return {}

    turns = list_turns(call_id)
    if not turns:
        summary = _fallback_summary([], call)
        summary["language"], summary["languages_used"] = _call_languages(call, [])
        update_call(call_id, summary_json=summary)
        return summary

    # Build redacted transcript lines
    transcript_lines = []
    for t in turns:
        spk = str(t.get("speaker", "user")).capitalize()
        txt = redact_pii_text(str(t.get("text", "")))
        transcript_lines.append(f"{spk}: {txt}")

    transcript_str = "\n".join(transcript_lines)[:6000]
    language, languages_used = _call_languages(call, turns)
    prompt = _SUMMARY_PROMPT_TEMPLATE.format(
        transcript=transcript_str,
        languages=", ".join(LANGUAGE_NAMES.get(lang, lang) for lang in languages_used),
    )

    parsed_summary: dict[str, Any] | None = None

    # Sunflower reads Luganda far better than Gemini does, so a Luganda call
    # is summarised there first; English and Swahili calls go to Gemini first.
    generators = (_summarise_local, _summarise_gemini) if language == "lg" else (_summarise_gemini, _summarise_local)
    for generate in generators:
        parsed_summary = generate(prompt)
        if parsed_summary:
            break

    # 3. Fallback deterministic summary
    if not parsed_summary:
        parsed_summary = _fallback_summary(turns, call)

    parsed_summary["language"] = language
    parsed_summary["languages_used"] = languages_used

    # Persist summary on voice_calls row
    update_call(call_id, summary_json=parsed_summary)

    # If linked ticket exists, append note to ticket
    ticket_id = call.get("ticket_id")
    if ticket_id:
        try:
            note_content = (
                f"Call Summary [{parsed_summary.get('subject', '')}]: "
                f"{parsed_summary.get('summary', '')}"
            )
            db.update_ticket(ticket_id, staff_note=note_content)
        except Exception:
            logger.debug("Failed updating ticket with call summary", exc_info=True)

    return parsed_summary
