"""The escalation policy, the stakes detector and the officer handoff (Phase 38)."""

from __future__ import annotations

import unittest

from app.escalation_policy import (
    TIER_NONE,
    TIER_OFFER,
    TIER_PRIORITY,
    assess_escalation,
    higher_priority,
    sla_seconds,
)
from app.officer_handoff import _names_only, prune_required_details, strongest_assessment
from app.text_signals import detect_high_stakes


class StakesTests(unittest.TestCase):
    def test_enforcement_deadlines_and_amounts_are_named(self) -> None:
        self.assertEqual(detect_high_stakes("URA froze my bank account"), ("enforcement",))
        self.assertEqual(detect_high_stakes("They issued an agency notice to my bank"), ("enforcement",))
        self.assertEqual(detect_high_stakes("my container was impounded at Malaba"), ("enforcement",))
        self.assertEqual(detect_high_stakes("The return is due today and the portal is down"), ("deadline_today",))
        self.assertEqual(detect_high_stakes("I owe UGX 45,000,000 in arrears"), ("large_amount",))
        self.assertEqual(detect_high_stakes("the penalty is 12 million shillings"), ("large_amount",))

    def test_luganda_and_swahili_enforcement_and_deadlines(self) -> None:
        self.assertEqual(detect_high_stakes("ebintu byange babikutte"), ("enforcement",))
        self.assertEqual(detect_high_stakes("akaunti imefungwa na URA"), ("enforcement",))
        self.assertEqual(detect_high_stakes("leo ni siku ya mwisho ya kulipa"), ("deadline_today",))

    def test_an_ordinary_question_has_nothing_at_stake(self) -> None:
        for text in ("What is the VAT rate?", "I earn UGX 2,000,000 per month", "How do I file by end of day?", ""):
            self.assertEqual(detect_high_stakes(text), (), text)


class PolicyTests(unittest.TestCase):
    def test_hardship_with_enforcement_goes_first_and_urgently(self) -> None:
        verdict = assess_escalation("I can't afford this, URA froze my bank account and my workers are unpaid")
        self.assertEqual((verdict.tier, verdict.priority, verdict.sla_seconds), (TIER_PRIORITY, "urgent", 30))
        self.assertIn("affect:hardship", verdict.reasons)
        self.assertIn("stakes:enforcement", verdict.reasons)

    def test_distress_with_a_deadline_today_is_priority(self) -> None:
        verdict = assess_escalation("I'm really worried, the VAT return is due today and the portal is down")
        self.assertEqual((verdict.tier, verdict.priority), (TIER_PRIORITY, "high"))

    def test_stakes_without_distress_are_offered_not_prioritised(self) -> None:
        verdict = assess_escalation("Customs seized my goods at Malaba. What is the procedure?")
        self.assertEqual((verdict.tier, verdict.priority), (TIER_OFFER, "high"))

    def test_sustained_upset_is_offered(self) -> None:
        verdict = assess_escalation(
            "This still makes no sense!!", history=["I don't understand this at all", "This is so frustrating!!"]
        )
        self.assertEqual(verdict.tier, TIER_OFFER)
        self.assertIn("affect:sustained", verdict.reasons)

    def test_an_ordinary_question_offers_no_one(self) -> None:
        verdict = assess_escalation("What is the VAT registration threshold?")
        self.assertEqual((verdict.tier, verdict.reasons), (TIER_NONE, ()))
        self.assertFalse(verdict.offers_person)

    def test_a_long_unanswered_conversation_is_offered(self) -> None:
        verdict = assess_escalation(
            "and what about the other one?",
            history=["q1", "q2", "q3", "q4", "q5"],
            result={"retrieval_mode": "abstained"},
        )
        self.assertEqual(verdict.tier, TIER_OFFER)
        self.assertIn("effort:long_conversation", verdict.reasons)
        self.assertIn("confidence:unanswered", verdict.reasons)

    def test_crisis_is_urgent_and_says_why(self) -> None:
        verdict = assess_escalation("I can't pay and I want to die")
        self.assertEqual((verdict.tier, verdict.priority, verdict.reasons), (TIER_PRIORITY, "urgent", ("safety:crisis",)))

    def test_luganda_hardship_with_seized_goods(self) -> None:
        verdict = assess_escalation("Sirina ssente, ebintu byange babikutte")
        self.assertEqual((verdict.tier, verdict.priority), (TIER_PRIORITY, "urgent"))

    def test_priorities_never_go_down(self) -> None:
        self.assertEqual(higher_priority("normal", "urgent"), "urgent")
        self.assertEqual(higher_priority("high", "normal"), "high")
        self.assertEqual(higher_priority("unknown", "low"), "unknown")
        self.assertEqual(sla_seconds("high"), 60)
        self.assertEqual(sla_seconds("normal"), 120)


class OfficerHandoffTests(unittest.TestCase):
    def test_the_button_does_not_hide_what_was_said_before_it(self) -> None:
        # "Talk to an officer" carries nothing; the message before it did.
        verdict = strongest_assessment(
            "Talk to an officer",
            ["What is PAYE?", "I can't afford this, they froze my bank account"],
        )
        self.assertEqual((verdict.tier, verdict.priority), (TIER_PRIORITY, "urgent"))

    def test_details_already_given_are_not_asked_again(self) -> None:
        details = [
            "tax type and return period",
            "TIN or registered taxpayer email",
            "any reference number shown in the URA portal",
        ]
        self.assertEqual(
            prune_required_details(details, ["tax_type", "filing_period"]),
            ["TIN or registered taxpayer email", "any reference number shown in the URA portal"],
        )
        # Half an answer is not an answer: the period is still needed.
        self.assertEqual(prune_required_details(details, ["tax_type"]), details)

    def test_the_envelope_carries_names_never_values(self) -> None:
        envelope = _names_only({"verified_slot_bank": {"tin": "1000000001", "tax_type": "vat"}, "taxpayer_tin": "1000000001"})
        self.assertEqual(envelope["verified_slot_bank"], {"tin": "provided", "tax_type": "provided"})
        self.assertEqual(envelope["taxpayer_tin"], "provided")


if __name__ == "__main__":
    unittest.main()
