"""The officer's brief: what a waiting caller needs, readable in ten seconds.

An officer taking a transferred call should not have to read the transcript.
The brief says why a person is needed, what the caller wants, what the AI
already told them, what is still open and which details they gave — every
claim citing the transcript turns it rests on (``turn_seqs``), so the officer
can check it with one click (docs/plans/officer-call-desk-plan.md §6.2).

It is kept *rolling*: a :class:`BriefScheduler` per live call watches the
turns as they are written (``store.register_turn_observer``) and rebuilds the
brief every ``RECEPTIONIST_BRIEF_EVERY_TURNS`` caller turns, incrementally —
the previous brief plus the turns since. So when the AI transfers, the brief
is usually already there; :func:`build_now` forces a rebuild (on transfer,
and when an officer asks for a refresh).

Models: English and Swahili calls go to Gemini (``RECEPTIONIST_BRIEF_MODEL``),
Luganda calls to Sunflower, which reads Luganda far better — both write
English. If neither answers, a deterministic brief is built from the
transcript (``fallback: true``). Transcript text is redacted when it is
written; the brief never re-introduces an identifier — details are labels
and "given (redacted)".

The brief is content: it goes out on the per-call channel only. The lobby
hears ``call.brief_ready`` with the topic and priority, and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from ..guardrails import redact_pii_text
from .config import get_brief_every_turns, get_brief_model
from .hub import hub
from .language import LANGUAGE_NAMES
from .store import get_call, list_turns, register_turn_observer, unregister_turn_observer, update_call

logger = logging.getLogger(__name__)

SENTIMENTS = ("calm", "confused", "frustrated", "distressed")
PRIORITIES = ("low", "normal", "high", "urgent")
#: Transcript characters sent to the model — Sunflower's context is 4k tokens.
MAX_TRANSCRIPT_CHARS = 6000
#: What the model writes; the rest of a stored brief is bookkeeping.
_MODEL_FIELDS = (
    "why_officer", "caller_goal", "ai_already_said", "still_open", "details_given",
    "sentiment", "topic", "priority", "suggested_opener",
)

BRIEF_SYSTEM = (
    "You brief an officer of the Uganda Revenue Authority (URA) who is about to take over a phone "
    "call from an AI receptionist. The officer must understand the caller in ten seconds. "
    "The transcript may be in English, Luganda or Swahili; write every field in English. "
    "Each transcript line starts with its turn number; every claim you make must list the turn "
    "numbers it rests on in turn_seqs. Never write out an identifier (TIN, phone number, account "
    "number, name): say what kind of detail was given, and give its value as 'given (redacted)'. "
    "Output strictly valid JSON and nothing else."
)

_SCHEMA = """{
  "why_officer": {"text": "why a person is needed, one sentence", "turn_seqs": [12]},
  "caller_goal": {"text": "what the caller wants, one sentence", "turn_seqs": [3]},
  "ai_already_said": [{"text": "what the AI already told them", "turn_seqs": [9]}],
  "still_open": [{"text": "what is still unresolved", "turn_seqs": [12]}],
  "details_given": [{"label": "TIN", "value": "given (redacted)", "turn_seqs": [7]}],
  "sentiment": "calm | confused | frustrated | distressed",
  "topic": "2 to 5 words",
  "priority": "low | normal | high | urgent",
  "suggested_opener": "one sentence the officer can say first"
}"""

_FULL_PROMPT = """Transcript of the call so far:
{transcript}

The call is in {language}.{transfer}

Return ONLY a JSON object matching this schema:
{schema}
"""

_INCREMENTAL_PROMPT = """Your brief so far, covering the call up to turn {covered}:
{previous}

New turns since then:
{transcript}

The call is in {language}.{transfer}

Update the brief with what the new turns change and return ONLY the complete updated JSON object,
matching this schema:
{schema}
"""


# ---------------------------------------------------------------------------
# Building one brief
# ---------------------------------------------------------------------------


def _transcript(turns: list[dict[str, Any]]) -> str:
    lines = [f"{t['seq']} {t.get('speaker', 'caller')}: {t.get('text', '')}" for t in turns if t.get("text")]
    text = "\n".join(lines)
    # Keep the end of a long call — that is where the reason for the transfer is.
    return text[-MAX_TRANSCRIPT_CHARS:]


def _claim(value: Any, valid_seqs: set[int]) -> dict[str, Any] | None:
    """``{"text", "turn_seqs"}`` with the text redacted and only real turn numbers kept."""
    if isinstance(value, str):
        value = {"text": value, "turn_seqs": []}
    if not isinstance(value, dict):
        return None
    text = redact_pii_text(str(value.get("text") or "").strip())
    if not text:
        return None
    seqs = []
    for seq in value.get("turn_seqs") or []:
        try:
            number = int(seq)
        except (TypeError, ValueError):
            continue
        if number in valid_seqs and number not in seqs:
            seqs.append(number)
    return {"text": text, "turn_seqs": seqs}


def parse_brief(raw: str, valid_seqs: set[int]) -> dict[str, Any] | None:
    """The model's brief, validated: fenced or chatty output tolerated, bad evidence dropped."""
    if not raw:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M)
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    goal = _claim(data.get("caller_goal"), valid_seqs)
    if goal is None:
        return None  # a brief that cannot say what the caller wants is no brief
    details = []
    for item in data.get("details_given") or []:
        if not isinstance(item, dict):
            continue
        claim = _claim({"text": item.get("label"), "turn_seqs": item.get("turn_seqs")}, valid_seqs)
        if claim:
            # Whatever the model wrote as the value, the officer sees that it was given.
            details.append({"label": claim["text"], "value": "given (redacted)", "turn_seqs": claim["turn_seqs"]})
    sentiment = str(data.get("sentiment") or "").strip().lower()
    priority = str(data.get("priority") or "").strip().lower()
    return {
        "why_officer": _claim(data.get("why_officer"), valid_seqs),
        "caller_goal": goal,
        "ai_already_said": [c for c in (_claim(v, valid_seqs) for v in data.get("ai_already_said") or []) if c],
        "still_open": [c for c in (_claim(v, valid_seqs) for v in data.get("still_open") or []) if c],
        "details_given": details,
        "sentiment": sentiment if sentiment in SENTIMENTS else "calm",
        "topic": redact_pii_text(str(data.get("topic") or "").strip())[:80],
        "priority": priority if priority in PRIORITIES else "normal",
        "suggested_opener": redact_pii_text(str(data.get("suggested_opener") or "").strip()),
    }


def fallback_brief(call: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    """A brief with no model: the first question, the last answer, the transfer reason."""
    caller = [t for t in turns if t.get("speaker") == "caller" and t.get("text")]
    answers = [t for t in turns if t.get("speaker") == "assistant" and t.get("kind") == "answer"]
    first = caller[0] if caller else None
    goal_text = str(first["text"])[:200] if first else "Not stated yet"
    reason = str(call.get("transfer_reason") or "").replace("_", " ").strip()
    why = None
    if call.get("transferred") or call.get("status") == "transferring":
        last = caller[-1] if caller else None
        why = {"text": f"Transferred: {reason or 'caller asked for an officer'}",
               "turn_seqs": [last["seq"]] if last else []}
    said = [{"text": str(a["text"])[:200], "turn_seqs": [a["seq"]]} for a in answers[-2:]]
    topic = str(call.get("topic") or "").replace("_", " ").strip()
    return {
        "why_officer": why,
        "caller_goal": {"text": goal_text, "turn_seqs": [first["seq"]] if first else []},
        "ai_already_said": said,
        "still_open": [],
        "details_given": [],
        "sentiment": "calm",
        "topic": topic.capitalize() if topic else goal_text[:60],
        "priority": str(call.get("priority") or "normal"),
        "suggested_opener": "Thank you for waiting — I can see what you asked the assistant; let me help you with it.",
    }


def _generate(prompt: str, language: str) -> tuple[dict[str, Any] | None, str]:
    """Ask the models in the order that suits *language*: (raw brief, model name)."""

    def gemini() -> tuple[str, str]:
        from ..providers.gateway import gemini_generate

        model = get_brief_model()
        return gemini_generate(prompt, system=BRIEF_SYSTEM, model=model, max_tokens=1500,
                               temperature=0.0, locale="en"), model

    def sunflower() -> tuple[str, str]:
        from ..llm import _vllm_generate

        messages = [{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": prompt}]
        return _vllm_generate(messages, max_tokens=1200, temperature=0.0), "sunflower"

    for generate in (sunflower, gemini) if language == "lg" else (gemini, sunflower):
        try:
            raw, model = generate()
        except Exception:
            logger.debug("Brief model %s failed", generate.__name__, exc_info=True)
            continue
        if raw:
            return {"raw": raw}, model
    return None, ""


def build_brief(call_id: str, *, force: bool = False) -> dict[str, Any] | None:
    """Build (or bring up to date) the call's brief and store it. Blocking: run it in a thread.

    Returns the brief, or ``None`` when the call has no transcript yet. With
    nothing new since the last brief, the stored one is returned unless
    *force*.
    """
    call = get_call(call_id)
    if not call:
        return None
    turns = list_turns(call_id)
    if not turns:
        return None
    latest = max(int(t["seq"]) for t in turns)
    previous = call.get("brief") if isinstance(call.get("brief"), dict) else None
    covered = int(previous.get("turns_covered") or 0) if previous else 0
    if previous and covered >= latest and not force:
        return previous

    language = str(call.get("locale") or "en")
    valid = {int(t["seq"]) for t in turns}
    transfer = ""
    if call.get("status") == "transferring":
        transfer = f" The AI has asked for an officer ({call.get('transfer_reason') or 'caller request'})."
    fresh = [t for t in turns if int(t["seq"]) > covered]
    if previous and fresh and not force:
        prompt = _INCREMENTAL_PROMPT.format(
            covered=covered, previous=json.dumps({k: previous.get(k) for k in _MODEL_FIELDS}),
            transcript=_transcript(fresh), language=LANGUAGE_NAMES.get(language, language),
            transfer=transfer, schema=_SCHEMA,
        )
    else:
        prompt = _FULL_PROMPT.format(
            transcript=_transcript(turns), language=LANGUAGE_NAMES.get(language, language),
            transfer=transfer, schema=_SCHEMA,
        )

    started = time.perf_counter()
    reply, model = _generate(prompt, language)
    brief = parse_brief(reply["raw"], valid) if reply else None
    fallback = brief is None
    if fallback:
        brief = fallback_brief(call, turns)
        model = "fallback"
    brief.update({
        "language": language,
        "turns_covered": latest,
        "generated_at": time.time(),
        "model": model,
        "fallback": fallback,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    })
    # Officers' notes on a transfer travel with the brief (Call Desk Phase 2).
    if previous and previous.get("transfer_notes"):
        brief["transfer_notes"] = previous["transfer_notes"]
    update_call(call_id, brief_json=brief, brief_updated_at=brief["generated_at"])
    return brief


# ---------------------------------------------------------------------------
# Keeping it rolling on a live call
# ---------------------------------------------------------------------------


class BriefScheduler:
    """Rebuilds one live call's brief as it goes. At most one build in flight."""

    def __init__(self, call_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.call_id = call_id
        self.loop = loop
        self._caller_turns = 0
        self._task: asyncio.Task[None] | None = None
        self._again = False
        # The transfer attempt whose "brief ready" the lobby has heard about.
        self._announced_attempt = 0

    def observe(self, turn: dict[str, Any]) -> None:
        """``store`` turn observer — may be called from any thread."""
        if turn.get("speaker") == "caller":
            self.loop.call_soon_threadsafe(self._caller_turn)

    def _caller_turn(self) -> None:
        self._caller_turns += 1
        if self._caller_turns >= get_brief_every_turns():
            self._caller_turns = 0
            self.request()

    def request(self) -> asyncio.Task[None]:
        """Build now, or once the build in flight finishes. Call on the loop's thread."""
        if self._task is not None and not self._task.done():
            self._again = True
            return self._task
        self._task = self.loop.create_task(self._run())
        return self._task

    async def _run(self) -> None:
        while True:
            self._again = False
            try:
                brief = await asyncio.to_thread(build_brief, self.call_id)
            except Exception:
                logger.exception("Brief build failed for %s", self.call_id)
                brief = None
            if brief:
                publish_brief(self.call_id, brief, self)
            if not self._again:
                return

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()


def publish_brief(call_id: str, brief: dict[str, Any], scheduler: BriefScheduler | None = None) -> None:
    """The brief to the call's watchers; "brief ready" to the lobby once per transfer."""
    hub.publish_call(call_id, "brief", brief)
    from .state import registry

    room = registry.get(call_id)
    if room is None or room.state.mode != "transferring":
        return
    attempt = room.state.transfer_attempts
    if scheduler is not None:
        if scheduler._announced_attempt >= attempt:
            return
        scheduler._announced_attempt = attempt
    hub.publish_lobby("call.brief_ready", {
        "call_id": call_id,
        "topic": brief.get("topic", ""),
        "priority": brief.get("priority", "normal"),
    })


_schedulers: dict[str, BriefScheduler] = {}


def start(call_id: str) -> None:
    """Keep this live call's brief rolling. Call from the event loop."""
    if call_id in _schedulers:
        return
    scheduler = BriefScheduler(call_id, asyncio.get_running_loop())
    _schedulers[call_id] = scheduler
    register_turn_observer(call_id, scheduler.observe)


def stop(call_id: str) -> None:
    scheduler = _schedulers.pop(call_id, None)
    if scheduler is not None:
        unregister_turn_observer(call_id, scheduler.observe)
        scheduler.cancel()


def build_now(call_id: str) -> asyncio.Task[None] | None:
    """Rebuild the live call's brief as soon as possible (on transfer).

    Safe from the loop's thread or any other; returns the build task when
    called on the loop's thread, ``None`` otherwise or for a call that is not
    live.
    """
    scheduler = _schedulers.get(call_id)
    if scheduler is None:
        return None
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is scheduler.loop:
        return scheduler.request()
    scheduler.loop.call_soon_threadsafe(scheduler.request)
    return None
