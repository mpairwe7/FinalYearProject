"""Procedural Memory Tier — Skills, Workflows, Exemplars & Operational Policies (2026).

Completes the 4-tier cognitive memory taxonomy alongside Working, Episodic,
and Semantic memory. Manages executable workflows, deterministic skills,
policy rules, and dynamic few-shot trajectory exemplars.
"""
from __future__ import annotations

import logging
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
        """Locate verified few-shot exemplar for a given intent."""
        for ex in self._exemplars.values():
            if ex.intent.lower() == intent.lower():
                return ex
        return None

    def list_exemplars(self, domain: str | None = None) -> list[TrajectoryExemplar]:
        if domain:
            return [e for e in self._exemplars.values() if e.domain == domain]
        return list(self._exemplars.values())
