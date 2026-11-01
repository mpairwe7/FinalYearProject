"""Procedural Memory Tier — Skills, Workflows, Exemplars & Operational Policies (2026).

Completes the 4-tier cognitive memory taxonomy alongside Working, Episodic,
and Semantic memory. Manages executable workflows, deterministic skills,
policy rules, and dynamic few-shot trajectory exemplars.

Exemplars carry *procedure*, not law. The matched exemplar's operating
sequence reaches every generation prompt (``ChatModel._get_procedural_guidance_context``),
and the answer contract tells the model to cite the legal bases it is given,
so a section number written here would be quoted to taxpayers as statute.
Statutory citations come from retrieved passages and tool results, which carry
their own sources (G245).

Only curated exemplars, matched by their reviewed triggers
(``_CURATED_TRIGGERS``), are offered to a natural-language query. A dynamic or
imported trajectory is unreviewed memory (OWASP ASI06): it is validated,
redacted and bounded on the way in, and it can never take a curated id.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Dynamic (recorded or imported) exemplars kept at most; the oldest goes first.
MAX_DYNAMIC_EXEMPLARS = 500
#: Rows read from one JSONL import at most.
MAX_IMPORT_ROWS = 5000
_MAX_QUERY_CHARS = 2000
_MAX_PLAN_STEPS = 12
_MAX_STEP_CHARS = 300
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
#: Words that carry no topic; dropped before ranking so "how do I" matches nothing.
_STOPWORDS = frozenset(
    """
    a an the and or but if of to in on at by for from with without into onto over under about
    above below is are was were be been being am do does did done have has had having can could
    would should may might must shall will i me my mine we us our you your he him his she her it
    its they them their this that these those what which who whom whose when where why how there
    here than then so too very just also only not no nor any all some each every more most other
    such own same both few further once again per via tax taxes ura uganda ugandan ugx please tell
    explain need want know get use using used online portal
    """.split()
)


def _content_tokens(text: str) -> frozenset[str]:
    """Topic tokens of *text*: lowercase alphanumerics, underscores split, stopwords dropped."""
    return frozenset(
        t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower().replace("_", " ")) if t not in _STOPWORDS
    )


@dataclass
class ProceduralSkill:
    """Executable skill or tool capability."""

    skill_id: str
    name: str
    description: str
    domain: str  # "domestic_tax", "customs", "compliance", "calculator", "navigation"
    parameters_schema: dict[str, Any] = field(default_factory=dict)
    deterministic: bool = True
    required_roles: list[str] = field(default_factory=lambda: ["public"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "domain": self.domain,
            "parameters_schema": self.parameters_schema,
            "deterministic": self.deterministic,
            "required_roles": self.required_roles,
        }


@dataclass
class TrajectoryExemplar:
    """Verified few-shot reasoning and tool-calling trajectory."""

    exemplar_id: str
    intent: str
    domain: str
    user_query: str
    plan: list[str]
    tool_sequence: list[str]
    faithfulness_score: float = 1.0
    demonstration_turn: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exemplar_id": self.exemplar_id,
            "intent": self.intent,
            "domain": self.domain,
            "user_query": self.user_query,
            "plan": list(self.plan),
            "tool_sequence": list(self.tool_sequence),
            "faithfulness_score": self.faithfulness_score,
            "demonstration_turn": self.demonstration_turn,
        }


# Curated foundational procedural exemplars for statutory tax operations
_DEFAULT_EXEMPLARS: list[TrajectoryExemplar] = [
    TrajectoryExemplar(
        exemplar_id="ex-paye-calc-01",
        intent="calculate_paye",
        domain="domestic_tax",
        user_query="Calculate PAYE on 3,000,000 UGX monthly salary",
        plan=["Extract gross salary", "Execute calculate_paye tool", "Format structured tax bands"],
        tool_sequence=["calculate_paye"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"monthly_salary": 3000000},
            "output_format": "Table + bottom-line net take-home pay",
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-vat-calc-02",
        intent="calculate_vat",
        domain="domestic_tax",
        user_query="What is the VAT on 50,000,000 UGX?",
        plan=["Extract net amount", "Execute calculate_vat tool", "Quote Value Added Tax Act citation"],
        tool_sequence=["calculate_vat"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"amount": 50000000},
            "output_format": "Net + 18% VAT + Gross inclusive amount",
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-tin-reg-03",
        intent="tin_registration",
        domain="compliance",
        user_query="How do I get an individual TIN online?",
        plan=["Identify taxpayer type (individual vs organisation)", "Provide portal destination link", "List statutory requirements (NIN/ID)"],
        tool_sequence=["tin_procedure_help"],
        faithfulness_score=1.0,
        demonstration_turn={
            "action": "Online guided registration via URA e-Services portal",
            "required_docs": ["National ID / NIN", "Valid mobile number", "Email address"],
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-vat-return-04",
        intent="vat_return_filing",
        domain="compliance",
        user_query="How do I file my monthly VAT return on the portal and what is the deadline?",
        plan=["Verify VAT registration status", "Direct to URA portal e-Services return filing", "State 15th statutory monthly deadline"],
        tool_sequence=["return_filing"],
        faithfulness_score=1.0,
        demonstration_turn={
            "action": "Online VAT return submission on portal.ura.go.ug",
            "statutory_deadline": "15th day of the month following the tax period",
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-tax-objection-05",
        intent="tax_objection",
        domain="compliance",
        user_query="How do I lodge an objection against an assessment and what is the deadline?",
        plan=["Identify notice of assessment date", "Check 45-day statutory window under Section 24 TPCA", "Advise lodging objection on portal with supporting grounds"],
        tool_sequence=["check_objection_validity"],
        faithfulness_score=1.0,
        demonstration_turn={
            "action": "Portal e-Services > Objections and Appeals > Object to a Tax Assessment",
            "statutory_deadline": "45 days from service of assessment notice",
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-mda-ntr-06",
        intent="resolve_mda_ntr_payment",
        domain="mda_ntr",
        user_query="I paid for my driving permit PRN on Mobile Money, but the UDLS portal still says unpaid.",
        plan=[
            "Identify MDA service (MoWT / UDLS driving permit) and PRN state",
            "Execute resolve_mda_ntr_payment tool",
            "Explain 3-hop PRN settlement (Bank/MoMo -> URA e-Tax Ledger -> MDA webhook) and Form DT-3008 refund rule",
        ],
        tool_sequence=["resolve_mda_ntr_payment"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"service_type": "mowt_driving_permit", "prn_status": "paid_not_reflecting_at_mda"},
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-life-event-07",
        intent="assess_life_event_tax_onboarding",
        domain="onboarding",
        user_query="I am renewing my KCCA trading license on IRAS with annual turnover of UGX 8,000,000.",
        plan=[
            "Identify life event (KCCA/IRAS trading license) and turnover (UGX 8,000,000)",
            "Execute assess_life_event_tax_onboarding tool",
            "Disclose 0% (UGX 0) Presumptive Tax below UGX 10,000,000 and NIRA NIN pre-flight name-order check",
        ],
        tool_sequence=["assess_life_event_tax_onboarding"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"life_event": "kcca_iras_trading_license", "annual_turnover_ugx": 8_000_000.0},
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-b2g-uesw-08",
        intent="evaluate_b2g_and_uesw_clearance",
        domain="customs",
        user_query="We won a PPDA e-GP government contract worth UGX 50,000,000; what TCC, EFRIS, and IFMS WHT rules apply?",
        plan=[
            "Identify B2G PPDA e-GP / MoFPED IFMS contract value",
            "Execute evaluate_b2g_and_uesw_clearance tool",
            "Compute 6% WHT above UGX 1,000,000 and state mandatory TCC + EFRIS e-invoice FDN requirements",
        ],
        tool_sequence=["evaluate_b2g_and_uesw_clearance"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"clearance_domain": "ppda_egp_tcc_and_ifms_wht", "amount_ugx": 50_000_000.0},
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-nil-shield-09",
        intent="build_new_taxpayer_compliance_shield",
        domain="onboarding",
        user_query="I registered a new company TIN 3 months ago and have made zero sales so far. Do I need to file returns?",
        plan=[
            "Identify new company with zero sales and unfiled months",
            "Execute build_new_taxpayer_compliance_shield tool",
            "Warn of the late-filing penalty the tool returns and provide the nil-return filing steps",
        ],
        tool_sequence=["build_new_taxpayer_compliance_shield"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"taxpayer_profile": "new_company_zero_sales", "unfiled_months": 3},
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-voluntary-disclosure-10",
        intent="simulate_tax_relief_and_instalments",
        domain="compliance",
        user_query="I have UGX 10,000,000 in unpaid tax from 4 months ago before any audit notice. How do I waive penalties and pay in 6 instalments?",
        plan=[
            "Extract unpaid principal tax, months overdue, pre-audit status, and instalment months",
            "Execute simulate_tax_relief_and_instalments tool",
            "Compare the penalty and interest exposure with the voluntary-disclosure relief and instalment schedule the tool returns",
        ],
        tool_sequence=["simulate_tax_relief_and_instalments"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {
                "principal_tax_ugx": 10_000_000.0,
                "months_late": 4,
                "voluntary_before_audit_notice": True,
                "instalment_months": 6,
            },
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-efris-refund-11",
        intent="resolve_efris_and_account_recovery",
        domain="domestic_tax",
        user_query="How does the 5% VAT refund for final consumers who demand EFRIS e-receipts work on UGX 2,360,000 of purchases?",
        plan=[
            "Identify EFRIS 5% consumer e-receipt refund scenario and 30-day purchase total",
            "Execute resolve_efris_and_account_recovery tool",
            "Verify UGX 2,000,000 FY2026-27 30-day threshold and compute 5% refund of embedded 18% VAT",
        ],
        tool_sequence=["resolve_efris_and_account_recovery"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {
                "scenario": "efris_free_channel_and_5pct_refund",
                "thirty_day_ereceipt_purchases_ugx": 2_360_000.0,
            },
        },
    ),
    TrajectoryExemplar(
        exemplar_id="ex-rental-pooled-12",
        intent="calculate_rental_pooled",
        domain="domestic_tax",
        user_query="I am an individual landlord with three rental buildings in Wakiso, each bringing in UGX 20,000,000 a year.",
        plan=[
            "Identify multi-building individual landlord rental scenario",
            "Execute calculate_rental_pooled tool",
            "Explain single annual UGX 2,820,000 threshold across all pooled buildings and 12% individual rental tax",
        ],
        tool_sequence=["calculate_rental_pooled"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"entity_type": "individual", "building_rents": [20_000_000.0, 20_000_000.0, 20_000_000.0]},
        },
    ),
]

_CURATED_BY_ID: dict[str, TrajectoryExemplar] = {ex.exemplar_id: ex for ex in _DEFAULT_EXEMPLARS}


def _trigger(*require: str, exclude: tuple[str, ...] = ()) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    return (
        tuple(re.compile(p, re.IGNORECASE) for p in require),
        tuple(re.compile(p, re.IGNORECASE) for p in exclude),
    )


#: Reviewed natural-language triggers for the curated exemplars, most specific
#: first. Every ``require`` pattern must match and no ``exclude`` pattern may.
#: Reviewed against every question in the FAQ corpus (G245): they fire on 3%
#: of them, each on its own procedure. The rule they replace (any two shared
#: words) fired on 82% and offered a different procedure about 19 times in 20.
_CURATED_TRIGGERS: tuple[tuple[str, tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]], ...] = (
    ("ex-mda-ntr-06", *_trigger(r"\bprn\b", r"\b(paid|unpaid|not reflect\w*|still shows?)\b")),
    ("ex-life-event-07", *_trigger(r"\b(trading licen[cs]e|kcca|iras)\b")),
    ("ex-b2g-uesw-08", *_trigger(r"\b(ppda|e-?gp|government contracts?|ifms|public procurement)\b")),
    ("ex-nil-shield-09", *_trigger(r"\b(nil returns?|zero sales|no sales|dormant)\b")),
    (
        "ex-voluntary-disclosure-10",
        *_trigger(r"\b(voluntary disclosure|instal+ments?|waive|waiver)\b", r"\b(penalt\w*|interest)\b"),
    ),
    (
        "ex-efris-refund-11",
        *_trigger(
            r"(\be-?receipts?\b.*\brefund\b|\brefund\b.*\be-?receipts?\b|\b5%\s+(?:vat\s+)?refund\b"
            r"|\blost (?:my )?(?:sim|phone|email)\b|\botp\b)"
        ),
    ),
    ("ex-rental-pooled-12", *_trigger(r"\b(landlord|rental)\b", r"\b(buildings|properties|houses|several|multiple|each)\b")),
    # Customs objections run under the EACCMA, with their own deadline.
    ("ex-tax-objection-05", *_trigger(r"\b(object|objection|objections)\b", exclude=(r"\b(aeo|customs|eaccma)\b",))),
    ("ex-paye-calc-01", *_trigger(r"\bpaye\b", r"\b(calculat\w*|comput\w*|how much|salary|deduct\w*)\b")),
    ("ex-vat-return-04", *_trigger(r"\bvat\b", r"\b(returns?|file|filing)\b")),
    (
        "ex-vat-calc-02",
        *_trigger(r"\bvat\b", r"(\b(calculat\w*|comput\w*|how much|inclusive|exclusive)\b|\bvat on (?:ugx|shs?\b|\d))"),
    ),
    ("ex-tin-reg-03", *_trigger(r"\btin\b", r"\b(register|registration|apply|obtain|get)\b")),
)


def _valid_identifier(value: Any) -> str:
    """*value* as a snake_case identifier (intent, domain or tool name), or ``""``."""
    text = str(value or "").strip()
    return text if _TOOL_NAME_RE.match(text) else ""


def _redact(value: Any) -> Any:
    """PII-redact every string in *value*, recursively (the guardrails redactor)."""
    if isinstance(value, str):
        from ..guardrails import redact_pii_text

        return redact_pii_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _redact(v) for k, v in value.items()}
    return value


def _valid_faithfulness(value: Any) -> float | None:
    """A finite score in [0, 1], or ``None`` — NaN would pass ``score < floor``."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) and 0.0 <= score <= 1.0 else None


class ProceduralMemory:
    """Manages system capabilities, skills, and pre-indexed thread-safe ExpRAG trajectory banks.

    Every read of the bank works on a snapshot taken under the lock: iterating
    a dict another thread is writing raises ``RuntimeError`` (Python's thread
    safety guarantees: iteration is never atomic).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._skills: dict[str, ProceduralSkill] = {}
        self._exemplars: dict[str, TrajectoryExemplar] = {}
        self._doc_tokens_map: dict[str, frozenset[str]] = {}
        self._doc_freq: dict[str, int] = {}
        #: Dynamic exemplar ids, oldest first, for the MAX_DYNAMIC_EXEMPLARS bound.
        self._dynamic_ids: OrderedDict[str, None] = OrderedDict()
        self._init_defaults()

    def _init_defaults(self) -> None:
        # Register core calculation & statutory skills
        self.register_skill(
            ProceduralSkill(
                skill_id="calc_vat",
                name="Value Added Tax Calculator",
                description="Calculates standard 18% VAT and inclusive/exclusive amounts",
                domain="domestic_tax",
            )
        )
        self.register_skill(
            ProceduralSkill(
                skill_id="calc_paye",
                name="PAYE Employment Income Calculator",
                description="Calculates progressive statutory PAYE brackets and take-home pay",
                domain="domestic_tax",
            )
        )
        self.register_skill(
            ProceduralSkill(
                skill_id="calc_withholding",
                name="Withholding Tax Calculator",
                description="Computes applicable WHT on professional services and supplies",
                domain="domestic_tax",
            )
        )
        self.register_skill(
            ProceduralSkill(
                skill_id="lookup_rate",
                name="Statutory Rate Lookup",
                description="Retrieves official FY2026-27 statutory tax and duty rates",
                domain="domestic_tax",
            )
        )
        for ex in _DEFAULT_EXEMPLARS:
            self.register_exemplar(ex)

    def register_skill(self, skill: ProceduralSkill) -> None:
        self._skills[skill.skill_id] = skill

    def get_skill(self, skill_id: str) -> ProceduralSkill | None:
        return self._skills.get(skill_id)

    def list_skills(self, domain: str | None = None) -> list[ProceduralSkill]:
        if domain:
            return [s for s in self._skills.values() if s.domain == domain]
        return list(self._skills.values())

    def _unindex(self, exemplar_id: str) -> None:
        for tok in self._doc_tokens_map.pop(exemplar_id, frozenset()):
            cnt = self._doc_freq.get(tok, 1) - 1
            if cnt <= 0:
                self._doc_freq.pop(tok, None)
            else:
                self._doc_freq[tok] = cnt

    def register_exemplar(self, exemplar: TrajectoryExemplar) -> None:
        """Add or replace *exemplar* and its token index.

        A curated id belongs to the reviewed default bank: anything but that
        exemplar itself raises ``ValueError``, or an import could swap the
        procedure a reviewed trigger puts into prompts. Dynamic exemplars are
        bounded by ``MAX_DYNAMIC_EXEMPLARS``; the oldest is evicted first.
        """
        ex_id = exemplar.exemplar_id
        curated = _CURATED_BY_ID.get(ex_id)
        if curated is not None and exemplar != curated:
            raise ValueError(f"exemplar id {ex_id!r} is curated and cannot be replaced")
        with self._lock:
            self._unindex(ex_id)
            self._exemplars[ex_id] = exemplar
            new_tokens = _content_tokens(
                " ".join(
                    [
                        exemplar.intent,
                        exemplar.domain,
                        exemplar.user_query,
                        " ".join(exemplar.plan),
                        " ".join(exemplar.tool_sequence),
                    ]
                )
            )
            self._doc_tokens_map[ex_id] = new_tokens
            for tok in new_tokens:
                self._doc_freq[tok] = self._doc_freq.get(tok, 0) + 1
            if curated is None:
                self._dynamic_ids.pop(ex_id, None)
                self._dynamic_ids[ex_id] = None
                while len(self._dynamic_ids) > MAX_DYNAMIC_EXEMPLARS:
                    oldest, _ = self._dynamic_ids.popitem(last=False)
                    self._exemplars.pop(oldest, None)
                    self._unindex(oldest)

    def find_exemplar(self, intent: str) -> TrajectoryExemplar | None:
        """The exemplar for an intent name, or the curated one a question triggers.

        1. Exact intent (``"calculate_paye"``), any exemplar — the memory tool asks this way.
        2. An identifier naming part of a curated intent (``"paye"``).
        3. A curated intent written inside the query ("run calculate_paye on 3m").
        4. A natural-language question: the first curated exemplar whose
           reviewed trigger it satisfies (``_CURATED_TRIGGERS``).

        There is no shared-words fallback. The result reaches every generation
        prompt, and an unrelated procedure there is worse than none.
        """
        query = (intent or "").strip()
        if not query:
            return None
        q_lower = query.lower()
        with self._lock:
            snapshot = dict(self._exemplars)
        for ex in snapshot.values():
            if ex.intent.lower() == q_lower:
                return ex
        if re.fullmatch(r"[a-z0-9_]{3,64}", q_lower):
            parts = [p for p in q_lower.split("_") if p and p not in _STOPWORDS]
            if not parts:
                return None
            for ex_id in _CURATED_BY_ID:
                ex = snapshot.get(ex_id)
                intent_parts = ex.intent.lower().split("_") if ex else []
                if any(intent_parts[i : i + len(parts)] == parts for i in range(len(intent_parts))):
                    return ex
            return None
        for ex_id, ex in _CURATED_BY_ID.items():
            if re.search(rf"(?<![a-z0-9_]){re.escape(ex.intent.lower())}(?![a-z0-9_])", q_lower):
                return snapshot.get(ex_id)
        for ex_id, require, exclude in _CURATED_TRIGGERS:
            if all(p.search(query) for p in require) and not any(p.search(query) for p in exclude):
                return snapshot.get(ex_id)
        return None

    def list_exemplars(self, domain: str | None = None) -> list[TrajectoryExemplar]:
        with self._lock:
            snapshot = list(self._exemplars.values())
        if domain:
            return [e for e in snapshot if e.domain == domain]
        return snapshot

    def record_verified_trajectory(
        self,
        *,
        intent: str,
        domain: str,
        user_query: str,
        plan: list[str],
        tool_sequence: list[str],
        faithfulness_score: float = 1.0,
        demonstration_turn: dict[str, Any] | None = None,
        min_faithfulness: float = 0.85,
        evaluator_passed: bool = True,
    ) -> TrajectoryExemplar | None:
        """Promote a verified high-faithfulness or officer-approved turn into the ExpRAG trajectory bank.

        Returns ``None`` — nothing stored — unless the score is a finite number
        in [0, 1] at or above *min_faithfulness*, *evaluator_passed* is True,
        the query/plan passes indirect prompt-injection screening (OWASP ASI06 / MINJA),
        and the intent, domain and every tool are snake_case identifiers. Text is
        PII-redacted and length-bounded before it is stored.
        """
        import hashlib

        from ..guardrails import scan_retrieved_text

        if not evaluator_passed:
            return None
        score = _valid_faithfulness(faithfulness_score)
        tools = [_valid_identifier(t) for t in tool_sequence or []]
        raw_query = (user_query or "").strip()
        _, query_injected = scan_retrieved_text(raw_query)
        if query_injected:
            return None
        for step_str in plan or []:
            _, step_injected = scan_retrieved_text(str(step_str))
            if step_injected:
                return None
        query = _redact(raw_query)[:_MAX_QUERY_CHARS]
        if score is None or score < min_faithfulness or not query or not tools or not all(tools):
            return None
        clean_intent = _valid_identifier(intent) or tools[0]
        clean_domain = _valid_identifier(domain) or "domestic_tax"
        if not _valid_identifier(clean_intent):
            return None
        steps = [str(_redact(str(s)))[:_MAX_STEP_CHARS] for s in (plan or [])][:_MAX_PLAN_STEPS]
        digest = hashlib.sha256(
            f"{clean_intent.lower()}|{'/'.join(tools)}|{query.lower()[:160]}".encode("utf-8")
        ).hexdigest()[:12]
        ex = TrajectoryExemplar(
            exemplar_id=f"ex-dyn-{digest}",
            intent=clean_intent,
            domain=clean_domain,
            user_query=query,
            plan=steps or [f"Execute {t}" for t in tools],
            tool_sequence=tools,
            faithfulness_score=score,
            demonstration_turn=_redact(dict(demonstration_turn or {})),
        )
        self.register_exemplar(ex)
        return ex

    def retrieve_exemplars(
        self,
        query: str,
        *,
        top_k: int = 3,
        domain: str | None = None,
        planned_tools: list[str] | None = None,
    ) -> list[TrajectoryExemplar]:
        """ExpRAG (arXiv:2603.18272) trajectory retrieval using pre-indexed IDF, tool-sequence overlap, and faithfulness.

        Ranks on topic tokens only (``_content_tokens``), so a question that
        shares nothing but "how do I" with an exemplar does not retrieve it.
        """
        q_clean = (query or "").strip().lower()
        q_tokens = _content_tokens(q_clean)
        tool_set = {t.strip() for t in (planned_tools or []) if t and t.strip()}
        with self._lock:
            candidates = self.list_exemplars(domain=domain)
            if not candidates:
                return []
            n_docs = max(1, len(self._exemplars))
            scored: list[tuple[float, TrajectoryExemplar]] = []
            for ex in candidates:
                toks = self._doc_tokens_map.get(ex.exemplar_id, frozenset())
                overlap = q_tokens & toks
                idf_sum = sum(
                    math.log(1.0 + n_docs / (1.0 + self._doc_freq.get(tok, 1)))
                    for tok in overlap
                )
                tool_bonus = 3.5 * len(tool_set & set(ex.tool_sequence)) if tool_set else 0.0
                intent_bonus = 2.0 if ex.intent.lower() in q_clean else 0.0
                score = (idf_sum + tool_bonus + intent_bonus) * max(0.5, float(ex.faithfulness_score))
                if score > 0.0:
                    scored.append((score, ex))

        scored.sort(key=lambda item: (item[0], item[1].faithfulness_score), reverse=True)
        return [ex for _, ex in scored[: max(1, int(top_k))]]

    def format_experience_memory_block(
        self,
        query: str,
        *,
        top_k: int = 2,
        planned_tools: list[str] | None = None,
    ) -> str:
        """Render top-K retrieved trajectories as an in-context procedural memory block (`ExpRAG`).

        Procedure only: legal bases are not rendered (see the module docstring).
        """
        hits = self.retrieve_exemplars(query, top_k=top_k, planned_tools=planned_tools)
        if not hits:
            return ""
        lines = ["## Procedural Experience Trajectories (ExpRAG verified reasoning patterns)"]
        for ex in hits:
            tools_str = ", ".join(ex.tool_sequence)
            plan_str = " -> ".join(ex.plan)
            lines.append(f"- [{ex.exemplar_id}] Intent `{ex.intent}` (tools: `{tools_str}`): {plan_str}")
        return "\n".join(lines)

    def export_trajectories_jsonl(self, path: Any) -> int:
        """Persist all registered exemplars to a JSONL file, atomically.

        Written to a temporary file in the same directory and renamed over the
        target, so a crash mid-write never leaves a half-written bank.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [ex.to_dict() for ex in self.list_exemplars()]
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return len(rows)

    def load_trajectories_jsonl(self, path: Any) -> int:
        """Load exemplars from a JSONL export; returns how many rows were loaded.

        A row is imported memory, so it gets the checks a recorded trajectory
        gets (identifiers, a finite score in [0, 1], redaction, bounds). A
        curated id loads only when the row is that curated exemplar unchanged.
        A bad row is skipped and counted, never fatal; at most
        ``MAX_IMPORT_ROWS`` rows are read.
        """
        target = Path(path)
        if not target.is_file():
            return 0
        loaded = rejected = 0
        with target.open(encoding="utf-8") as fh:
            for n, line in enumerate(fh):
                if n >= MAX_IMPORT_ROWS:
                    logger.warning("procedural import stopped at %d rows (%s)", MAX_IMPORT_ROWS, target.name)
                    break
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if not isinstance(data, dict):
                        raise ValueError("row is not an object")
                    curated = _CURATED_BY_ID.get(str(data.get("exemplar_id") or ""))
                    if curated is not None:
                        if data != curated.to_dict():
                            raise ValueError("curated exemplar altered")
                        self.register_exemplar(curated)
                    else:
                        self.register_exemplar(self._exemplar_from_row(data))
                except (ValueError, TypeError, KeyError) as exc:
                    rejected += 1
                    logger.debug("skipped procedural row %d of %s: %s", n + 1, target.name, exc)
                    continue
                loaded += 1
        if rejected:
            logger.warning("skipped %d invalid procedural rows from %s", rejected, target.name)
        return loaded

    @staticmethod
    def _exemplar_from_row(data: dict[str, Any]) -> TrajectoryExemplar:
        """Validate and redact one imported row; ``ValueError`` when it is unusable."""
        ex_id = str(data.get("exemplar_id") or "")
        if not re.fullmatch(r"ex-[a-z0-9-]{1,64}", ex_id):
            raise ValueError("invalid exemplar_id")
        intent = _valid_identifier(data.get("intent"))
        tools = [_valid_identifier(t) for t in data.get("tool_sequence") or []]
        score = _valid_faithfulness(data.get("faithfulness_score", 1.0))
        plan = data.get("plan") or []
        demo = data.get("demonstration_turn") or {}
        if not intent or not tools or not all(tools) or score is None:
            raise ValueError("invalid intent, tools or score")
        if not isinstance(plan, list) or not isinstance(demo, dict):
            raise ValueError("invalid plan or demonstration_turn")
        return TrajectoryExemplar(
            exemplar_id=ex_id,
            intent=intent,
            domain=_valid_identifier(data.get("domain")) or "domestic_tax",
            user_query=str(_redact(str(data.get("user_query") or "")))[:_MAX_QUERY_CHARS],
            plan=[str(_redact(str(s)))[:_MAX_STEP_CHARS] for s in plan][:_MAX_PLAN_STEPS],
            tool_sequence=tools,
            faithfulness_score=score,
            demonstration_turn=_redact(demo),
        )
