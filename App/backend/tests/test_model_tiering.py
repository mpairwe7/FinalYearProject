"""Capability-tiered model selection.

The tier is chosen from the supervisor's existing ``RouteDecision``, so
the properties worth pinning are about the *policy*, not about any
model: that cheap routes stay cheap, that the expensive tier is reached
only for the reasons that justify it, and — the two that would cause
real damage — that a turn is never demoted mid-flight and that a
Ugandan-language turn never leaves the tier its LoRA adapters fit.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.agents.eval_routing import GOLDEN_SET
from app.agents.supervisor import supervisor
from app.providers.routing import (
    ADAPTER_BOUND_LOCALES,
    MODEL_SLOTS,
    ModelTier,
    TierDecision,
    select_tier,
)


class FlagGateTests(unittest.TestCase):
    def test_disabled_always_returns_t1(self) -> None:
        """Off, every turn resolves to the single configured model."""
        for route in ("rag", "tools", "escalate", "greet", "clarify"):
            decision = select_tier(route, enabled=False)
            self.assertIs(decision.tier, ModelTier.T1, route)

    def test_disabled_does_not_promote(self) -> None:
        decision = select_tier(
            "escalate",
            escalation_reason="dispute",
            evaluator_rejected=True,
            multi_hop=True,
            enabled=False,
        )
        self.assertIs(decision.tier, ModelTier.T1)
        self.assertFalse(decision.promoted)


class BaseTierTests(unittest.TestCase):
    def test_deterministic_routes_need_no_model(self) -> None:
        for route in ("greet", "clarify", "blocked"):
            self.assertIs(select_tier(route).tier, ModelTier.T0, route)

    def test_t0_has_no_model_id(self) -> None:
        self.assertEqual(select_tier("greet").model, "")

    def test_plain_retrieval_is_t1(self) -> None:
        self.assertIs(select_tier("rag").tier, ModelTier.T1)

    def test_tool_loop_is_t2(self) -> None:
        for route in ("tools", "tax_specialist", "customs_specialist"):
            self.assertIs(select_tier(route, tool_count=3).tier, ModelTier.T2, route)

    def test_single_tool_turn_stays_t1(self) -> None:
        """One calculator and a sentence of framing is not agentic work."""
        self.assertIs(select_tier("tools", tool_count=1).tier, ModelTier.T1)

    def test_escalation_is_at_least_t2(self) -> None:
        decision = select_tier("escalate", escalation_reason="user asked for a human")
        self.assertIs(decision.tier, ModelTier.T2)

    def test_every_tier_above_t0_resolves_a_model(self) -> None:
        for tier in (ModelTier.T1, ModelTier.T2, ModelTier.T3):
            self.assertTrue(MODEL_SLOTS[tier], tier)


class PromotionTests(unittest.TestCase):
    def test_evaluator_rejection_promotes_to_t3(self) -> None:
        decision = select_tier("rag", evaluator_rejected=True)
        self.assertIs(decision.tier, ModelTier.T3)
        self.assertTrue(decision.promoted)

    def test_multi_hop_promotes_to_t3(self) -> None:
        self.assertIs(select_tier("rag", multi_hop=True).tier, ModelTier.T3)

    def test_dispute_escalation_promotes_to_t3(self) -> None:
        decision = select_tier(
            "escalate", escalation_reason="Legal / dispute context needs human handling"
        )
        self.assertIs(decision.tier, ModelTier.T3)

    def test_plain_human_request_does_not_reach_t3(self) -> None:
        """Not every handoff is a dispute; only disputes pay for T3."""
        decision = select_tier("escalate", escalation_reason="User explicitly asked for a human")
        self.assertIs(decision.tier, ModelTier.T2)

    def test_distress_promotes_retrieval_to_t2(self) -> None:
        for distress in ("frustration", "hardship"):
            decision = select_tier("rag", distress=distress)
            self.assertIs(decision.tier, ModelTier.T2, distress)

    def test_mild_distress_does_not_promote(self) -> None:
        self.assertIs(select_tier("rag", distress="confusion").tier, ModelTier.T1)

    def test_low_confidence_promotes_to_t2(self) -> None:
        self.assertIs(select_tier("rag", confidence=0.4).tier, ModelTier.T2)

    def test_the_highest_promotion_wins(self) -> None:
        decision = select_tier("rag", confidence=0.3, distress="hardship", multi_hop=True)
        self.assertIs(decision.tier, ModelTier.T3)

    def test_a_promotion_never_lowers_the_tier(self) -> None:
        """Distress asks for T2; a tool turn is already there and stays."""
        decision = select_tier("tools", tool_count=4, distress="hardship")
        self.assertIs(decision.tier, ModelTier.T2)

    def test_t0_is_never_promoted(self) -> None:
        """A greeting from a distressed user is still a greeting."""
        decision = select_tier(
            "greet", distress="hardship", multi_hop=True, evaluator_rejected=True
        )
        self.assertIs(decision.tier, ModelTier.T0)

    def test_reason_is_recorded(self) -> None:
        decision = select_tier("rag", multi_hop=True)
        self.assertIn("multi-hop", decision.reason)


class AdapterBindingTests(unittest.TestCase):
    """The constraint that would silently degrade Ugandan-language answers."""

    def test_ugandan_locales_pin_to_t1(self) -> None:
        for locale in ADAPTER_BOUND_LOCALES:
            decision = select_tier("tools", tool_count=4, locale=locale)
            self.assertIs(decision.tier, ModelTier.T1, locale)

    def test_pinning_survives_every_promotion(self) -> None:
        """Promoting off T1 would drop the adapter and answer worse."""
        decision = select_tier(
            "escalate",
            locale="lg",
            escalation_reason="dispute",
            evaluator_rejected=True,
            multi_hop=True,
            distress="hardship",
            confidence=0.1,
        )
        self.assertIs(decision.tier, ModelTier.T1)
        self.assertIn("adapter", decision.reason)

    def test_regional_tag_is_still_pinned(self) -> None:
        for locale in ("lg-UG", "lg_UG", "LG"):
            self.assertIs(select_tier("tools", tool_count=4, locale=locale).tier, ModelTier.T1)

    def test_english_is_not_pinned(self) -> None:
        self.assertIs(select_tier("tools", tool_count=4, locale="en").tier, ModelTier.T2)

    def test_greeting_in_a_pinned_locale_still_needs_no_model(self) -> None:
        self.assertIs(select_tier("greet", locale="lg").tier, ModelTier.T0)


class BudgetCapTests(unittest.TestCase):
    def test_exhausted_budget_caps_at_t2(self) -> None:
        decision = select_tier("rag", multi_hop=True, budget_exhausted=True)
        self.assertIs(decision.tier, ModelTier.T2)
        self.assertIn("budget", decision.reason)

    def test_cap_does_not_disturb_lower_tiers(self) -> None:
        self.assertIs(select_tier("rag", budget_exhausted=True).tier, ModelTier.T1)
        self.assertIs(select_tier("greet", budget_exhausted=True).tier, ModelTier.T0)


class DistributionTests(unittest.TestCase):
    """Blended cost is the whole argument for tiering; measure it."""

    #: Relative decode cost per tier, normalised to T1.  T2 is a 3.3B-active
    #: MoE, so its cost sits just above the 8B dense tier rather than at
    #: its 30B parameter count.
    COST = {ModelTier.T0: 0.0, ModelTier.T1: 1.0, ModelTier.T2: 1.3, ModelTier.T3: 9.0}

    def _tiers_over_golden_set(self) -> list[ModelTier]:
        tiers = []
        for query, _route, _tool in GOLDEN_SET:
            decision = supervisor.classify(query)
            tiers.append(
                select_tier(
                    decision.route.value,
                    confidence=decision.confidence,
                    tool_count=len(decision.suggested_tools),
                    escalation_reason=decision.reason,
                ).tier
            )
        return tiers

    def test_t3_is_a_small_share_of_traffic(self) -> None:
        tiers = self._tiers_over_golden_set()
        share = tiers.count(ModelTier.T3) / len(tiers)
        self.assertLessEqual(share, 0.15, f"T3 share {share:.2%}")

    def test_blended_cost_stays_near_an_all_t1_system(self) -> None:
        tiers = self._tiers_over_golden_set()
        blended = sum(self.COST[t] for t in tiers) / len(tiers)
        self.assertLess(blended, 2.0, f"blended {blended:.2f}x")

    def test_some_traffic_needs_no_model_at_all(self) -> None:
        self.assertIn(ModelTier.T0, self._tiers_over_golden_set())


class SupervisorIntegrationTests(unittest.TestCase):
    def test_every_route_the_supervisor_emits_maps_to_a_tier(self) -> None:
        """A new route must not fall through to an undefined tier."""
        from app.agents.state import AgentRoute

        for route in AgentRoute:
            decision = select_tier(route.value)
            self.assertIsInstance(decision, TierDecision, route)
            self.assertIn(decision.tier, list(ModelTier), route)

    def test_luganda_escalation_routes_and_pins(self) -> None:
        """End to end: Phase 1 routes it, Phase 2 keeps it on its adapter."""
        with mock.patch.dict(os.environ, {"FLAG_MULTILINGUAL_ROUTING": "true"}):
            route = supervisor.classify("Njagala okwogera n'omuntu", locale="lg")
        decision = select_tier(
            route.route.value, escalation_reason=route.reason, locale="lg"
        )
        self.assertEqual(route.route.value, "escalate")
        self.assertIs(decision.tier, ModelTier.T1)


if __name__ == "__main__":
    unittest.main()
