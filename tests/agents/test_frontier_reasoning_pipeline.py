"""Tests for the Frontier Tax Reasoning & High-Precision Pipeline (dev-2.0).

Verifies the fixes for:
1. Calculator verb bypass on informational legal inquiries containing numbers.
2. Distress pattern over-triggering on statutory nouns like 'penalties'.
3. Destructive spelling correction and abbreviation duplication in query normalization.
4. False premise guard false-positives on 'non-resident tax table', 'disputes tax', and 'input VAT'.
5. Model tiering elevation to T3 for complex statutory reasoning.
"""

from __future__ import annotations

import pytest

from app.calculator_router import plan_calculation
from app.premise_guard import check_false_premise
from app.providers.routing import ModelTier, select_tier
from app.query import correct_spelling, expand_abbreviations
from app.text_signals import detect_user_distress


class TestCalculatorRouterGuards:
    def test_informational_legal_inquiry_with_amounts_is_not_intercepted(self):
        query = (
            "We are a VAT distributor with UGX 600M turnover and bought goods "
            "worth UGX 40M with a manual paper invoice. Can input VAT be claimed? Penalties?"
        )
        plan = plan_calculation(query)
        assert plan is None, f"Expected None for legal inquiry, got {plan}"

    def test_calculation_with_explicit_verb_is_planned(self):
        query = "Calculate VAT on 5,000,000 UGX"
        plan = plan_calculation(query)
        assert plan is not None
        assert plan.tool == "calculate_vat"
        assert plan.params.get("amount") == 5000000.0

    def test_rental_pooling_calculation_context(self):
        from app.service import _evaluate_calculation_context
        query = (
            "I own a multi-story building in Kampala generating UGX 90M annually—the upper floors "
            "are residential apartments (UGX 40M) and the ground floor is commercial shops (UGX 50M). "
            "How is my rental income tax calculated as an individual? Does the UGX 2,820,000 threshold and "
            "20% expense deduction apply across both, or are they treated separately?"
        )
        ctx = _evaluate_calculation_context(query)
        assert "90,000,000" in ctx
        assert "2,820,000" in ctx
        assert "10,461,600" in ctx
        assert "Section 5(3)" in ctx
        assert "ZERO expense deductions" in ctx


class TestDistressSignals:
    def test_statutory_penalty_noun_does_not_trigger_distress(self):
        query = "What are the penalties for late filing of VAT returns?"
        distress = detect_user_distress(query)
        assert not distress, f"Expected empty/None for statutory question, got {distress}"

    def test_actual_pressure_triggers_distress(self):
        query = "I am being audited by URA and facing penalties, please help me urgently!"
        distress = detect_user_distress(query)
        assert distress in ("urgency", "anxiety")


class TestQueryNormalization:
    def test_disputed_word_is_not_mutated_to_dispute(self):
        query = "Is 30% payment required on a disputed tax assessment?"
        corrected = correct_spelling(query)
        assert "disputed" in corrected
        assert "dispute tax" not in corrected

    def test_consulting_word_is_preserved(self):
        query = "What is the withholding tax rate for consulting services?"
        corrected = correct_spelling(query)
        assert "consulting" in corrected

    def test_abbreviation_expansion_does_not_duplicate(self):
        query = "Withholding Tax (WHT) on local services"
        expanded = expand_abbreviations(query)
        assert "Withholding Tax (Withholding Tax (WHT))" not in expanded


class TestPremiseGuard:
    def test_non_resident_tax_table_is_not_false_premise(self):
        query = "Show me the non-resident tax table for PAYE in Uganda."
        result = check_false_premise(query)
        assert not result.is_false_premise

    def test_disputed_tax_is_not_false_premise(self):
        query = "What is the rule on payment of disputed tax during an objection?"
        result = check_false_premise(query)
        assert not result.is_false_premise

    def test_input_vat_is_not_false_premise(self):
        query = "Can I claim input VAT if the receipt has no EFRIS QR code?"
        result = check_false_premise(query)
        assert not result.is_false_premise

    def test_invented_fictional_tax_is_rejected(self):
        query = "What is the rate for moon landing tax in Uganda?"
        result = check_false_premise(query)
        assert result.is_false_premise


class TestModelTieringElevation:
    def test_cross_border_query_elevates_to_t3(self):
        query = "We pay $4,000 monthly for Irish SaaS digital services. What reverse-charge VAT applies?"
        decision = select_tier("single_hop", query=query, enabled=True)
        assert decision.tier == ModelTier.T3
        assert decision.promoted

    def test_tax_dispute_query_elevates_to_t3(self):
        query = "How do I file an objection to a default assessment and is 30% deposit required?"
        decision = select_tier("single_hop", query=query, enabled=True)
        assert decision.tier == ModelTier.T3
        assert decision.promoted

    def test_simple_definition_stays_t1(self):
        query = "What is a TIN?"
        decision = select_tier("single_hop", query=query, enabled=True)
        assert decision.tier == ModelTier.T1
