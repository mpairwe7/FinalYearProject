"""When a person should be offered, and how fast they should answer (Phase 38).

The text channel escalated on retrieval confidence alone
(``OutputGuard.should_escalate``): faithfulness and hits. Distress across turns
added a "Talk to an officer" button, and a taxpayer who pressed it got a ticket
with ``topic: general`` and ``priority: normal`` — behind everyone else, with the
officer opening cold. The voice channel already weighed more
(``receptionist/risk.py``).

This module is the one place that weighs the four things that should decide it:

* **affect** — how the taxpayer feels (``text_signals.detect_user_distress``),
  and whether it has lasted across turns (``distress_trajectory``);
* **stakes** — what is riding on it (``text_signals.detect_high_stakes``):
  enforcement, a deadline today, a large amount;
* **effort** — how long they have been at it;
* **confidence** — whether the assistant could answer at all.

It returns a tier — ``none``, ``offer`` (a person is offered, with the
reason) or ``priority`` (offered first; the ticket is ``high`` or ``urgent``
with a shorter SLA) — and the reasons as codes. It never escalates by itself:
a person is offered, the taxpayer decides. The crisis path keeps its own
handling. Deterministic and offline: same input, same output.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .text_signals import (
    detect_crisis,
    detect_high_stakes,
    detect_user_distress,
    distress_trajectory,
    strip_stakes,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

TIER_NONE = "none"
TIER_OFFER = "offer"
TIER_PRIORITY = "priority"

#: Affect kinds that make stakes urgent. Confusion is not one of them: it
#: wants a better explanation, not a faster queue.
_DISTRESSED = frozenset({"hardship", "frustration", "anxiety", "urgency"})
_PRIORITY_ORDER = ("low", "normal", "high", "urgent")
#: Retrieval modes that mean the assistant could not answer the question.
_UNANSWERED_MODES = frozenset({"abstained", "false_premise_rejected", "out_of_scope"})


def _env_int(name: str, default: int, *, floor: int) -> int:
    try:
        return max(floor, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def offer_after_turns() -> int:
    """Turns after which a still-unhappy conversation is offered a person."""
    return _env_int("ESCALATION_OFFER_TURNS", 6, floor=2)


def stakes_amount_threshold_ugx() -> float:
    """An amount at issue from which a distressed taxpayer is put ahead of the queue."""
    return float(_env_int("ESCALATION_STAKES_AMOUNT_UGX", 10_000_000, floor=0))


def sla_seconds(priority: str, *, hardship: bool = False) -> int:
    """Target officer response for a ticket of *priority* (as the envelope's SLA)."""
    if priority == "urgent":
        return 30
    base = 60 if priority == "high" else 120
    if hardship:
        # Hardship tightens the target; a configured value never loosens it.
        return min(base, _env_int("EQ_HARDSHIP_SLA_TARGET_SECONDS", 45, floor=15))
    return base


def higher_priority(first: str, second: str) -> str:
    """The more urgent of two ticket priorities (unknown values count as normal)."""
    rank = {name: i for i, name in enumerate(_PRIORITY_ORDER)}
    return first if rank.get(first, 1) >= rank.get(second, 1) else second


@dataclass(frozen=True)
class EscalationAssessment:
    """Whether to offer a person, how urgently, and why — as codes."""

    tier: str = TIER_NONE
    priority: str = "normal"
    sla_seconds: int = 120
    affect: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def offers_person(self) -> bool:
        return self.tier in (TIER_OFFER, TIER_PRIORITY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "priority": self.priority,
            "sla_seconds": self.sla_seconds,
            "affect": self.affect,
            "reasons": list(self.reasons),
        }


def assess_escalation(
    message: str,
    *,
    history: Sequence[str] = (),
    result: Mapping[str, Any] | None = None,
) -> EscalationAssessment:
    """Weigh affect, stakes, effort and confidence for this turn.

    *history* is the taxpayer's earlier messages in the conversation, oldest
    first; *result* the turn's result when there is one (its retrieval mode
    and faithfulness say whether the assistant could answer).
    """
    text = message or ""
    if detect_crisis(text):
        # The crisis path answers with the crisis lines and offers a person;
        # a ticket raised from it goes to the front.
        return EscalationAssessment(
            tier=TIER_PRIORITY, priority="urgent", sla_seconds=sla_seconds("urgent"),
            affect="crisis", reasons=("safety:crisis",),
        )

    stakes = detect_high_stakes(text, amount_threshold_ugx=stakes_amount_threshold_ugx())
    # Affect is read without the enforcement phrases, which the hardship
    # pattern would otherwise count a second time (see strip_stakes).
    kind = detect_user_distress(strip_stakes(text) if "enforcement" in stakes else text)
    sustained = bool(distress_trajectory(kind, list(history))["sustained"])
    turns = len(history) + 1
    outcome = result or {}
    mode = str(outcome.get("retrieval_mode") or "")
    faithfulness = outcome.get("faithfulness_score")
    unanswered = mode in _UNANSWERED_MODES or (
        isinstance(faithfulness, int | float) and float(faithfulness) < 0.5
    )

    reasons: list[str] = []
    if kind:
        reasons.append(f"affect:{kind}")
    if sustained:
        reasons.append("affect:sustained")
    reasons.extend(f"stakes:{code}" for code in stakes)
    if turns >= offer_after_turns():
        reasons.append("effort:long_conversation")
    if unanswered:
        reasons.append("confidence:unanswered")

    distressed = kind in _DISTRESSED
    pressing = "enforcement" in stakes or "deadline_today" in stakes
    hardship = kind == "hardship"

    if hardship and pressing:
        tier, priority = TIER_PRIORITY, "urgent"
    elif (distressed or sustained) and stakes:
        tier, priority = TIER_PRIORITY, "high"
    elif hardship or sustained:
        tier, priority = TIER_OFFER, "high" if hardship else "normal"
    elif "enforcement" in stakes:
        tier, priority = TIER_OFFER, "high"
    elif (turns >= offer_after_turns() and (distressed or unanswered)) or (unanswered and distressed):
        tier, priority = TIER_OFFER, "normal"
    else:
        tier, priority = TIER_NONE, "normal"

    return EscalationAssessment(
        tier=tier,
        priority=priority,
        sla_seconds=sla_seconds(priority, hardship=hardship),
        affect=kind,
        reasons=tuple(reasons) if tier != TIER_NONE else (),
    )


# ---------------------------------------------------------------------------
# Phase 40 (G174): URA CXP Operating Hours (07:00–23:00 EAT / 11:00 PM Close)
# & Business-Hours SLA Pause/Resume Calculator
# ---------------------------------------------------------------------------


def cxp_open_hour() -> int:
    """Hour of day (0-23 in CXP_TIMEZONE) when live CXP officer operations open."""
    return min(23, _env_int("CXP_OPEN_HOUR", 7, floor=0))


def cxp_close_hour() -> int:
    """Hour of day (1-24 in CXP_TIMEZONE) when live CXP officer operations stop (default 23 = 11 PM)."""
    return min(24, _env_int("CXP_CLOSE_HOUR", 23, floor=1))


def cxp_timezone_name() -> str:
    return (os.getenv("CXP_TIMEZONE", "Africa/Kampala") or "Africa/Kampala").strip()


@dataclass(frozen=True)
class CxpScheduleStatus:
    """Whether URA CXP human operations are currently open, and when they next reopen."""

    within_operating_hours: bool
    after_hours: bool
    timezone: str
    open_time_eat: str
    close_time_eat: str
    next_open_timestamp: float
    next_open_label: str
    sla_paused: bool
    sla_pause_reason: str
    live_officer_online: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "within_operating_hours": self.within_operating_hours,
            "after_hours": self.after_hours,
            "timezone": self.timezone,
            "open_time_eat": self.open_time_eat,
            "close_time_eat": self.close_time_eat,
            "next_open_timestamp": round(self.next_open_timestamp, 3),
            "next_open_label": self.next_open_label,
            "sla_paused": self.sla_paused,
            "sla_pause_reason": self.sla_pause_reason,
            "live_officer_online": self.live_officer_online,
        }


def evaluate_cxp_schedule(
    now_ts: float | None = None,
    *,
    check_presence: bool = False,
) -> CxpScheduleStatus:
    """Evaluate URA CXP operating status at *now_ts* (defaults to ``time.time()``).

    URA CXP live officer operations run daily until 11:00 PM EAT (``23:00``)
    and reopen at 7:00 AM EAT (``07:00``), while the AI assistant operates 24/7.
    When *check_presence* is True, an actively online officer overrides an
    off-hours clock unless ``CXP_FORCE_AFTER_HOURS=true`` is set.
    """
    ts = float(now_ts if now_ts is not None else time.time())
    tz_name = cxp_timezone_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=3))
        tz_name = "Africa/Kampala"

    open_h = cxp_open_hour()
    close_h = cxp_close_hour()
    local_dt = dt.datetime.fromtimestamp(ts, tz=tz)

    live_online = False
    if check_presence:
        try:
            from .receptionist.presence import count_active_officers  # noqa: PLC0415

            live_online = count_active_officers() > 0
        except Exception:
            live_online = False

    force_mode = (os.getenv("CXP_FORCE_AFTER_HOURS", "") or "").strip().lower()
    if force_mode in ("1", "true", "yes"):
        within = False
    elif force_mode in ("open", "force_open") or live_online:
        within = True
    elif open_h < close_h:
        within = open_h <= local_dt.hour < close_h
    else:
        within = local_dt.hour >= open_h or local_dt.hour < close_h

    if within:
        next_open_dt = local_dt
    elif open_h < close_h and local_dt.hour >= close_h:
        next_day = (local_dt + dt.timedelta(days=1)).date()
        next_open_dt = dt.datetime(next_day.year, next_day.month, next_day.day, open_h, 0, 0, tzinfo=tz)
    else:
        today = local_dt.date()
        next_open_dt = dt.datetime(today.year, today.month, today.day, open_h, 0, 0, tzinfo=tz)

    open_str = f"{open_h:02d}:00 EAT"
    close_str = f"{close_h:02d}:00 EAT"
    next_label = f"{open_h % 12 or 12}:00 {'AM' if open_h < 12 else 'PM'} EAT ({open_str})"
    return CxpScheduleStatus(
        within_operating_hours=within,
        after_hours=not within,
        timezone=tz_name,
        open_time_eat=open_str,
        close_time_eat=close_str,
        next_open_timestamp=next_open_dt.timestamp(),
        next_open_label=next_label,
        sla_paused=not within,
        sla_pause_reason="" if within else f"after_hours_cxp_closed_{close_h:02d}00_to_{open_h:02d}00_eat",
        live_officer_online=live_online,
    )


def business_hours_elapsed_seconds(start_ts: float, end_ts: float) -> float:
    """Elapsed seconds between *start_ts* and *end_ts* during CXP operating hours only.

    Pauses the SLA clock during the after-hours window (default 23:00–07:00 EAT)
    so a ticket created at 11:15 PM EAT is not penalised for the 7h45m before
    officers return at 7:00 AM EAT.
    """
    if end_ts <= start_ts:
        return 0.0
    tz_name = cxp_timezone_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = dt.timezone(dt.timedelta(hours=3))

    open_h = cxp_open_hour()
    close_h = cxp_close_hour()
    if open_h <= 0 and close_h >= 24:
        return end_ts - start_ts

    cur_dt = dt.datetime.fromtimestamp(start_ts, tz=tz)
    end_dt = dt.datetime.fromtimestamp(end_ts, tz=tz)
    total_s = 0.0
    day = cur_dt.date()
    end_day = end_dt.date()
    while day <= end_day:
        win_start = dt.datetime(day.year, day.month, day.day, open_h, 0, 0, tzinfo=tz)
        win_end = (
            dt.datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=tz)
            if close_h >= 24
            else dt.datetime(day.year, day.month, day.day, close_h, 0, 0, tzinfo=tz)
        )
        seg_start = max(cur_dt, win_start)
        seg_end = min(end_dt, win_end)
        if seg_end > seg_start:
            total_s += (seg_end - seg_start).total_seconds()
        day += dt.timedelta(days=1)
    return max(0.0, total_s)


def _eat_12h_label(eat_24h: str) -> str:
    """Convert a 24-hour 'HH:00 EAT' label from CxpScheduleStatus into 'H:00 AM/PM EAT'."""
    try:
        hour = int(str(eat_24h).split(":", 1)[0].strip()) % 24
        return f"{hour % 12 or 12}:00 {'AM' if hour < 12 else 'PM'} EAT"
    except Exception:
        return str(eat_24h)


_AFTER_HOURS_NOTICES: dict[str, str] = {
    "en": (
        "\n\n**After-Hours Escalation Notice (CXP Hours: {open_eat} – {close_eat})**: "
        "Live URA officer operations close at {close_eat} ({close_24h}) and reopen at {open_eat} ({open_24h}). "
        "Your case and conversation history have been queued for priority officer review as soon as operations resume, "
        "and their reply will appear right here in this conversation. Meanwhile, I remain available 24/7 to assist you."
    ),
    "lg": (
        "\n\n**Okumanyisa ku Ssaawa z'Okukola (CXP: {open_eat} – {close_eat})**: "
        "Abakozi ba URA bakola okutuuka ssaawa ttaano ez'ekiro ({close_eat} / {close_24h}) ne baddamu ku ssaawa emu ey'oku makya ({open_eat} / {open_24h}). "
        "Ensonga yo etereddwa mu lukalala olw'okukolebwako mu bwangu nga bwe banaddamu, era eky'okuddamu kyabwe kijja kulabikira wano. "
        "Mu kiseera kino, nkyali wano essaawa 24/7 okukuyamba."
    ),
    "sw": (
        "\n\n**Taarifa ya Saa za Kazi (CXP: {open_eat} – {close_eat})**: "
        "Maafisa wa URA wanafanya kazi hadi saa tano usiku ({close_eat} / {close_24h}) na kufungua tena saa moja asubuhi ({open_eat} / {open_24h}). "
        "Suala lako limewekwa kwenye foleni ya kipaumbele pindi shughuli zitakaporejea, na jibu lao litaonekana hapa hapa. "
        "Kwa sasa, niko hapa saa 24/7 kukusaidia."
    ),
    "nyn": (
        "\n\n**Okumanyisa aha Shaaha z'Okukora (CXP: {open_eat} – {close_eat})**: "
        "Abakozi ba URA nibakora kuhika shaaha itaano z'ekiro ({close_eat} / {close_24h}) kandi bagarukaho shaaha emwe y'akasheeshe ({open_eat} / {open_24h}). "
        "Enshonga yaawe eteirwe omu rweego rw'okubanza kushwijumwa ahonaaho baagarukaho, kandi eky'okugarukamu kyabo nikiija kureebeka aha. "
        "Omu bwire obu, ndi aha shaaha 24/7 kukuhwera."
    ),
    "ach": (
        "\n\n**Ngec i kom Cawa me Tic (CXP: {open_eat} – {close_eat})**: "
        "Lutic me URA gitiyo naka cawa abic me dyewor ({close_eat} / {close_24h}) dok giyabo cawa acel me odiko ({open_eat} / {open_24h}). "
        "Lokki dong kiketo i rek me kony oyot-oyot ka tic ocake, dok lagam gi binen kany i lok man. "
        "Kombedi, pud atye kany cawa 24/7 me konyi."
    ),
}


def after_hours_escalation_notice(
    locale: str = "en",
    *,
    schedule: CxpScheduleStatus | None = None,
) -> str:
    """Localized notice appended when an escalation occurs after CXP operating hours."""
    loc = (locale or "en").strip().lower().split("-")[0]
    sched = schedule or evaluate_cxp_schedule()
    template = _AFTER_HOURS_NOTICES.get(loc, _AFTER_HOURS_NOTICES["en"])
    return template.format(
        open_eat=_eat_12h_label(sched.open_time_eat),
        close_eat=_eat_12h_label(sched.close_time_eat),
        open_24h=sched.open_time_eat,
        close_24h=sched.close_time_eat,
    )


_AFTER_HOURS_ESCALATION_MESSAGES: dict[str, str] = {
    "en": (
        "A URA officer has been asked to look at this, and your case is queued for "
        "priority review. Live CXP officer operations close at {close_eat} ({close_24h}) and "
        "reopen at {open_eat} ({open_24h}) — their reply will appear here in this conversation "
        "as soon as operations resume, so you do not need to start again. Meanwhile, "
        "this AI assistant remains available 24/7 to help you."
    ),
    "lg": (
        "Omukozi wa URA asabiddwa okwetegereza ensonga eno, era etereddwa mu lukalala "
        "olw'okukolebwako mu bwangu. Abakozi ba URA bakola okutuuka ssaawa ttaano ez'ekiro "
        "({close_eat} / {close_24h}) ne baddamu ku ssaawa emu ey'oku makya ({open_eat} / {open_24h}) — eky'okuddamu "
        "kyabwe kijja kulabikira wano mu mboozi eno nga bwe banaddamu, kale teweetaaga "
        "kutandika buto. Mu kiseera kino, omuyambi wa AI ono ali wano essaawa 24/7 okukuyamba."
    ),
    "sw": (
        "Afisa wa URA ameombwa kuangalia suala hili, na kesi yako imewekwa kwenye foleni "
        "ya kipaumbele. Maafisa wa CXP wanafanya kazi hadi saa tano usiku ({close_eat} / {close_24h}) "
        "na kufungua tena saa moja asubuhi ({open_eat} / {open_24h}) — jibu lao litaonekana hapa katika "
        "mazungumzo haya pindi shughuli zitakaporejea, kwa hivyo huna haja ya kuanza upya. "
        "Kwa sasa, msaidizi huyu wa AI anaendelea kupatikana saa 24/7 kukusaidia."
    ),
    "nyn": (
        "Omukozi wa URA ashabiirwe kushwijuma enshonga egi, kandi eteirwe omu rweego "
        "rw'okubanza kukorwaho. Abakozi ba CXP nibakora kuhika shaaha itaano z'ekiro "
        "({close_eat} / {close_24h}) kandi bagarukaho shaaha emwe y'akasheeshe ({open_eat} / {open_24h}) — "
        "eky'okugarukamu kyabo nikiija kureebeka aha omu kiganiro eki ahonaaho baagarukaho, "
        "nahabwekyo torikwetenga kutandika busya. Omu bwire obu, omuhwezi wa AI ogu ari aha shaaha 24/7 kukuhwera."
    ),
    "ach": (
        "Kilimo latic me URA me neno lok man, dok lokki kiketo i rek me kony oyot-oyot. "
        "Lutic me CXP gitiyo naka cawa abic me dyewor ({close_eat} / {close_24h}) dok giyabo cawa acel "
        "me odiko ({open_eat} / {open_24h}) — lagam gi binen kany i lok man cut-cut ka tic ocake, "
        "pi meno pe mite ni icak manyen. Kombedi, lakony me AI man pud tye kany cawa 24/7 me konyi."
    ),
}

_AFTER_HOURS_REUSED_MESSAGES: dict[str, str] = {
    "en": (
        "Your question is already queued with URA's officers for priority review. "
        "Live CXP officer operations close at {close_eat} ({close_24h}) and reopen at "
        "{open_eat} ({open_24h}) — their reply will appear here as soon as operations "
        "resume, so there is no need to ask again. Meanwhile, this AI assistant "
        "remains available 24/7 to help you."
    ),
    "lg": (
        "Ekibuuzo kyo kyamaze dda okuteekebwa mu lukalala lw'abakozi ba URA. "
        "Abakozi ba CXP bakola okutuuka {close_eat} ({close_24h}) ne baddamu ku "
        "{open_eat} ({open_24h}) — eky'okuddamu kyabwe kijja kulabikira wano nga bwe "
        "banaddamu, kale teweetaaga kuddamu kubuuza. Mu kiseera kino, omuyambi wa AI "
        "ono ali wano essaawa 24/7 okukuyamba."
    ),
    "sw": (
        "Swali lako tayari liko kwenye foleni ya maafisa wa URA kwa ukaguzi wa kipaumbele. "
        "Maafisa wa CXP wanafanya kazi hadi {close_eat} ({close_24h}) na kufungua tena "
        "{open_eat} ({open_24h}) — jibu lao litaonekana hapa pindi shughuli zitakaporejea, "
        "kwa hivyo hakuna haja ya kuuliza tena. Kwa sasa, msaidizi huyu wa AI yuko hapa saa 24/7."
    ),
    "nyn": (
        "Ekibuuzo kyawe kyateirwe kare omu rweego rw'abakozi ba URA kushwijumwa. "
        "Abakozi ba CXP nibakora kuhika {close_eat} ({close_24h}) kandi bagarukaho "
        "{open_eat} ({open_24h}) — eky'okugarukamu kyabo nikiija kureebeka aha ahonaaho "
        "baagarukaho, nahabwekyo torikwetenga kubuuza obundi. Omuhwezi wa AI ogu ari aha shaaha 24/7."
    ),
    "ach": (
        "Lapeny ni dong tye i rek pa lutic me URA. Lutic me CXP gitiyo naka "
        "{close_eat} ({close_24h}) dok giyabo {open_eat} ({open_24h}) — lagam gi binen "
        "kany cut-cut ka tic ocake, pi meno pe mite ni ipeny doki. Lakony me AI man "
        "pud tye kany cawa 24/7 me konyi."
    ),
}


def after_hours_escalation_message(
    locale: str = "en",
    *,
    reused: bool = False,
    schedule: CxpScheduleStatus | None = None,
) -> str:
    """Localized confirmation returned by POST /v1/escalate when CXP is closed."""
    loc = (locale or "en").strip().lower().split("-")[0]
    sched = schedule or evaluate_cxp_schedule()
    table = _AFTER_HOURS_REUSED_MESSAGES if reused else _AFTER_HOURS_ESCALATION_MESSAGES
    template = table.get(loc, table["en"])
    return template.format(
        open_eat=_eat_12h_label(sched.open_time_eat),
        close_eat=_eat_12h_label(sched.close_time_eat),
        open_24h=sched.open_time_eat,
        close_24h=sched.close_time_eat,
    )


