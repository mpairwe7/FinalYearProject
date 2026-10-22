"""Procedural Memory Tier — Skills, Workflows, Exemplars & Operational Policies (2026).

Completes the 4-tier cognitive memory taxonomy alongside Working, Episodic,
and Semantic memory. Manages executable workflows, deterministic skills,
policy rules, and dynamic few-shot trajectory exemplars.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
            "required_form": "Form DT-1011",
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
            "legal_basis": "Tax Procedures Code Act Section 24",
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
            "legal_basis": "Traffic and Road Safety Act (Cap. 361) & Public Finance Management Act 2015 s.29",
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
            "legal_basis": "Tax Procedures Code Act s.135(1) & Income Tax Act s.4(5) Second Schedule",
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
            "legal_basis": "PPDA Act (Cap. 205) & Income Tax Act s.119(1)",
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
            "Warn of UGX 200,000/month late-filing penalty under TPC Act s.49(1) and provide 60-Second Nil-Return steps",
        ],
        tool_sequence=["build_new_taxpayer_compliance_shield"],
        faithfulness_score=1.0,
        demonstration_turn={
            "tool_args": {"taxpayer_profile": "new_company_zero_sales", "unfiled_months": 3},
            "legal_basis": "Tax Procedures Code Act (Cap. 343) s.49(1)",
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
            "Compare standard s.49(1)/s.39 exposure against 100% waiver under Voluntary Disclosure (s.66(1a), Form DT-10307) and s.40 TCC-unlocking instalment schedule",
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
            "legal_basis": "Tax Procedures Code Act s.66(1a) & s.40",
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
            "legal_basis": "Value Added Tax (Amendment) Act 2026 s.7",
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
            "legal_basis": "Income Tax Act (Cap. 338) s.5 & Third Schedule Part VI",
        },
    ),
]


class ProceduralMemory:
    """Manages system capabilities, skills, and high-fidelity trajectory banks."""

    def __init__(self) -> None:
        self._skills: dict[str, ProceduralSkill] = {}
        self._exemplars: dict[str, TrajectoryExemplar] = {}
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

    def register_exemplar(self, exemplar: TrajectoryExemplar) -> None:
        self._exemplars[exemplar.exemplar_id] = exemplar

    def find_exemplar(self, intent: str) -> TrajectoryExemplar | None:
        """Locate verified few-shot exemplar for a given intent or query string."""
        q_lower = intent.lower().strip()
        # 1. Exact match on intent
        for ex in self._exemplars.values():
            if ex.intent.lower() == q_lower:
                return ex
        # 2. Substring match on intent
        for ex in self._exemplars.values():
            if ex.intent.lower() in q_lower or q_lower in ex.intent.lower():
                return ex
        # 3. Token overlap match with user_query and plan
        q_tokens = set(re.findall(r"\w+", q_lower))
        best_match: TrajectoryExemplar | None = None
        best_score = 0
        for ex in self._exemplars.values():
            ex_tokens = set(re.findall(r"\w+", (ex.user_query + " " + ex.intent).lower()))
            overlap = len(q_tokens & ex_tokens)
            if overlap > best_score and overlap >= 2:
                best_score = overlap
                best_match = ex
        return best_match

    def list_exemplars(self, domain: str | None = None) -> list[TrajectoryExemplar]:
        if domain:
            return [e for e in self._exemplars.values() if e.domain == domain]
        return list(self._exemplars.values())

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
    ) -> TrajectoryExemplar | None:
        """Promote a verified high-faithfulness or officer-approved turn into the ExpRAG trajectory bank."""
        import hashlib

        if faithfulness_score < min_faithfulness or not user_query.strip() or not tool_sequence:
            return None
        digest = hashlib.sha256(
            f"{intent.strip().lower()}|{'/'.join(tool_sequence)}|{user_query.strip().lower()[:160]}".encode("utf-8")
        ).hexdigest()[:12]
        ex = TrajectoryExemplar(
            exemplar_id=f"ex-dyn-{digest}",
            intent=intent.strip() or tool_sequence[0],
            domain=domain.strip() or "domestic_tax",
            user_query=user_query.strip(),
            plan=list(plan) or [f"Execute {t}" for t in tool_sequence],
            tool_sequence=list(tool_sequence),
            faithfulness_score=float(faithfulness_score),
            demonstration_turn=dict(demonstration_turn or {}),
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
        """ExpRAG (arXiv:2603.18272) trajectory retrieval combining lexical IDF, tool-sequence overlap, and faithfulness."""
        import math

        q_clean = (query or "").strip().lower()
        q_tokens = {tok for tok in re.findall(r"[a-z0-9_]{2,}", q_clean)}
        tool_set = {t.strip() for t in (planned_tools or []) if t and t.strip()}
        candidates = self.list_exemplars(domain=domain)
        if not candidates:
            return []

        doc_freq: dict[str, int] = {}
        doc_tokens_map: dict[str, set[str]] = {}
        for ex in candidates:
            corpus_str = " ".join([ex.intent, ex.domain, ex.user_query, " ".join(ex.plan), " ".join(ex.tool_sequence)]).lower()
            toks = {tok for tok in re.findall(r"[a-z0-9_]{2,}", corpus_str)}
            doc_tokens_map[ex.exemplar_id] = toks
            for tok in toks:
                doc_freq[tok] = doc_freq.get(tok, 0) + 1

        n_docs = max(1, len(candidates))
        scored: list[tuple[float, TrajectoryExemplar]] = []
        for ex in candidates:
            toks = doc_tokens_map[ex.exemplar_id]
            overlap = q_tokens & toks
            idf_sum = sum(math.log(1.0 + n_docs / (1.0 + doc_freq.get(tok, 1))) for tok in overlap)
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
        """Render top-K retrieved trajectories as an in-context procedural memory block (`ExpRAG`)."""
        hits = self.retrieve_exemplars(query, top_k=top_k, planned_tools=planned_tools)
        if not hits:
            return ""
        lines = ["## Procedural Experience Trajectories (ExpRAG verified reasoning patterns)"]
        for ex in hits:
            tools_str = ", ".join(ex.tool_sequence)
            plan_str = " -> ".join(ex.plan)
            basis = ex.demonstration_turn.get("legal_basis", "")
            basis_suffix = f" (Basis: {basis})" if basis else ""
            lines.append(
                f"- [{ex.exemplar_id}] Intent `{ex.intent}` (tools: `{tools_str}`): {plan_str}{basis_suffix}"
            )
        return "\n".join(lines)

    def export_trajectories_jsonl(self, path: Any) -> int:
        """Persist all registered exemplars to a JSONL file."""
        import json
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [ex.to_dict() for ex in self._exemplars.values()]
        with target.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(rows)

    def load_trajectories_jsonl(self, path: Any) -> int:
        """Load persisted exemplars from a JSONL file into memory."""
        import json
        from pathlib import Path

        target = Path(path)
        if not target.is_file():
            return 0
        loaded = 0
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            ex = TrajectoryExemplar(
                exemplar_id=str(data["exemplar_id"]),
                intent=str(data.get("intent") or ""),
                domain=str(data.get("domain") or "domestic_tax"),
                user_query=str(data.get("user_query") or ""),
                plan=list(data.get("plan") or []),
                tool_sequence=list(data.get("tool_sequence") or []),
                faithfulness_score=float(data.get("faithfulness_score", 1.0)),
                demonstration_turn=dict(data.get("demonstration_turn") or {}),
            )
            self.register_exemplar(ex)
            loaded += 1
        return loaded
