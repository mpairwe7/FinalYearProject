"""Text-channel resolution cases (Phase 38): what opens one, and one per domain."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app.memory.resolution_ledger import (
    _WORKFLOW_DOMAINS,
    DOMAIN_OBJECTIVES,
    ResolutionLedger,
    case_for_prompt,
    case_update_for_turn,
    infer_domain_tag,
    workflow_domain,
)


class _Memory:
    """An in-memory stand-in for the consent and working-memory tables."""

    def __init__(self, consent: bool = True) -> None:
        self.consent = consent
        self.working: dict[tuple[str, str], dict[str, Any]] = {}

    def has_active_consent(self, user_id: str, purpose: str, tenant_id: str = "default") -> bool:
        return self.consent

    def get_working_memory(self, user_id: str, tenant_id: str = "default") -> dict[str, Any] | None:
        return dict(self.working.get((user_id, tenant_id), {})) or None

    def upsert_working_memory(self, user_id: str, tenant_id: str, fields: dict[str, Any], ttl_seconds: int = 0) -> bool:
        self.working.setdefault((user_id, tenant_id), {}).update(fields)
        return True


class TurnClassificationTests(unittest.TestCase):
    def test_a_plain_answer_opens_no_case(self) -> None:
        self.assertIsNone(case_update_for_turn("What is VAT?", {"retrieval_mode": "hybrid", "reply": "18%"}))

    def test_a_workflow_a_lookup_a_calculation_and_a_handoff_do(self) -> None:
        workflow = case_update_for_turn(
            "help me register",
            {"retrieval_mode": "workflow", "workflow": {"id": "tin_registration", "name": "TIN Registration Guide", "status": "active"}},
        )
        self.assertEqual((workflow.domain_tag, workflow.status, workflow.event), ("tin_registration", "in_progress", "workflow"))
        self.assertEqual(workflow.step, "Started guided steps: TIN Registration Guide")
        done = case_update_for_turn(
            "yes", {"workflow": {"id": "payment_assistance", "name": "Payment Assistance", "status": "completed", "completed": True}}
        )
        self.assertEqual((done.domain_tag, done.status), ("payment", "awaiting_user_action"))
        lookup = case_update_for_turn(
            "check my TIN in EFRIS", {"connectors_used": [{"namespace": "efris", "label": "EFRIS", "outcome": "found"}]}
        )
        self.assertEqual((lookup.domain_tag, lookup.step), ("efris", "Looked up in EFRIS"))
        calc = case_update_for_turn("PAYE on 2,000,000", {"retrieval_mode": "calculator"})
        self.assertEqual((calc.domain_tag, calc.event), ("paye", "calculation"))
        handoff = case_update_for_turn("VAT refund dispute", {"ticket_id": "t-1"})
        self.assertEqual((handoff.status, handoff.event), ("escalated_officer", "escalation"))

    def test_a_cancelled_workflow_is_not_progress(self) -> None:
        self.assertIsNone(case_update_for_turn("stop", {"workflow": {"id": "calc_vat", "status": "cancelled"}}))

    def test_a_rate_read_out_is_a_plain_answer(self) -> None:
        # The rate lookup and the calculator both answer as "calculator"; the
        # taxpayer's figure is what makes it a computation.
        self.assertIsNone(case_update_for_turn("What is the VAT rate in Uganda?", {"retrieval_mode": "calculator"}))
        self.assertIsNone(case_update_for_turn("What is the PAYE threshold?", {"retrieval_mode": "calculator"}))
        self.assertIsNotNone(case_update_for_turn("PAYE on UGX 2,500,000", {"retrieval_mode": "calculator"}))

    def test_a_bare_follow_up_belongs_to_the_conversation_topic(self) -> None:
        update = case_update_for_turn("and on 3,000,000?", {"retrieval_mode": "calculator", "current_topic": "paye"})
        self.assertEqual(update.domain_tag, "paye")

    def test_a_workflow_is_read_from_the_workflow_not_the_answer(self) -> None:
        # Answering "VAT" to the return-filing steps is not a VAT case.
        update = case_update_for_turn(
            "VAT", {"workflow": {"id": "return_filing", "name": "Return Filing", "status": "active"}}
        )
        self.assertEqual(update.domain_tag, "returns")


class DomainTests(unittest.TestCase):
    def test_a_named_tax_beats_the_generic_words(self) -> None:
        self.assertEqual(infer_domain_tag("Do I need to register for VAT?"), "vat")
        self.assertEqual(infer_domain_tag("How do I pay my PAYE?"), "paye")
        self.assertEqual(infer_domain_tag("I want to register for a TIN"), "tin_registration")
        self.assertEqual(infer_domain_tag("How do I generate a PRN?"), "payment")

    def test_excise_and_stamp_duty_are_not_customs(self) -> None:
        self.assertEqual(infer_domain_tag("excise duty on beer"), "excise")
        self.assertEqual(infer_domain_tag("customs duty on a car"), "customs")
        self.assertEqual(infer_domain_tag("how much is stamp duty on a lease"), "general")

    def test_the_other_flows_have_their_own_domains(self) -> None:
        self.assertEqual(infer_domain_tag("I need a tax clearance certificate"), "tax_clearance")
        self.assertEqual(infer_domain_tag("transfer of ownership for my motor vehicle"), "motor_vehicle")
        self.assertEqual(infer_domain_tag("how do I file a nil return"), "returns")
        self.assertEqual(infer_domain_tag("corporation tax on my company"), "income_tax")

    def test_every_objective_has_a_phrase_and_every_flow_a_domain(self) -> None:
        flows = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"
        ids = {path.stem for path in flows.glob("*.yaml")}
        self.assertTrue(ids, "workflow definitions not found")
        self.assertEqual(sorted(ids - set(_WORKFLOW_DOMAINS)), [])
        for domain in _WORKFLOW_DOMAINS.values():
            self.assertIn(domain, DOMAIN_OBJECTIVES, domain)
        self.assertEqual(workflow_domain("calc_excise_duty"), "excise")
        self.assertEqual(workflow_domain("a_new_flow", "Rental Income Guide"), "income_tax")


class LedgerUpsertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = _Memory()
        patcher = mock.patch.multiple(
            "app.database",
            has_active_consent=self.memory.has_active_consent,
            get_working_memory=self.memory.get_working_memory,
            upsert_working_memory=self.memory.upsert_working_memory,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ledger = ResolutionLedger()

    def _cases(self) -> dict[str, Any]:
        return self.memory.working[("u-1", "default")]["active_resolution_cases"]

    def test_one_case_per_domain_with_a_fixed_objective(self) -> None:
        first = self.ledger.upsert_turn_case("u-1", domain_tag="vat", channel="web", step="Started guided steps: VAT Calculator")
        second = self.ledger.upsert_turn_case("u-1", domain_tag="vat", channel="voice_ws", step="Asked by voice")
        self.assertEqual(first.case_id, second.case_id)
        self.assertEqual(len(self._cases()), 1)
        stored = next(iter(self._cases().values()))
        self.assertEqual(stored["primary_objective"], DOMAIN_OBJECTIVES["vat"])
        self.assertEqual(stored["completed_steps"], ["Started guided steps: VAT Calculator", "Asked by voice"])
        self.assertEqual(stored["source_channel"], "voice_ws")
        self.ledger.upsert_turn_case("u-1", domain_tag="paye", channel="web")
        self.assertEqual(len(self._cases()), 2)

    def test_a_reused_legacy_case_loses_its_raw_objective(self) -> None:
        from app.memory.resolution_ledger import ResolutionCase

        # Before Phase 38 the voice path stored the question itself.
        legacy = ResolutionCase(user_id="u-1", primary_objective="what is owed on 1000000001 for UGX 4,500,000")
        self.assertTrue(self.ledger.checkpoint("u-1", legacy))
        self.ledger.upsert_turn_case("u-1", domain_tag=legacy.domain_tag, channel="web", step="Asked again")
        stored = next(iter(self._cases().values()))
        self.assertEqual(stored["primary_objective"], DOMAIN_OBJECTIVES[legacy.domain_tag])
        self.assertNotIn("1000000001", str(stored))

    def test_nothing_is_written_without_consent(self) -> None:
        self.memory.consent = False
        self.assertIsNone(self.ledger.upsert_turn_case("u-1", domain_tag="vat", channel="web"))
        self.assertEqual(self.memory.working, {})

    def test_steps_are_bounded(self) -> None:
        for i in range(20):
            self.ledger.upsert_turn_case("u-1", domain_tag="customs", channel="web", step=f"step {i}")
        steps = next(iter(self._cases().values()))["completed_steps"]
        self.assertEqual(len(steps), 12)
        self.assertEqual(steps[-1], "step 19")


class PromptCaseTests(unittest.TestCase):
    def test_the_prompt_names_the_case_this_question_is_about(self) -> None:
        now = time.time()
        working = {
            "active_resolution_cases": {
                "res-vat": {"case_id": "res-vat", "primary_objective": DOMAIN_OBJECTIVES["vat"], "domain_tag": "vat", "updated_at": now - 60},
                "res-cus": {"case_id": "res-cus", "primary_objective": DOMAIN_OBJECTIVES["customs"], "domain_tag": "customs", "updated_at": now},
            }
        }
        self.assertEqual(case_for_prompt(working, "what about my VAT registration?").case_id, "res-vat")
        self.assertEqual(case_for_prompt(working, "hello again").case_id, "res-cus")
        self.assertIsNone(case_for_prompt({}, "VAT"))


if __name__ == "__main__":
    unittest.main()
