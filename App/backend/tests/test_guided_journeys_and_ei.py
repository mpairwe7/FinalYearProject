"""Guided-journey reachability and emotional-intelligence gaps.

Each case below reproduces a finding from the journey probes run against the
local GPU stack on 2026-09-29 (docs/runbooks/guided-journey-probes.md):
flows that existed but could not be reached, a how-to question that opened an
officer ticket, an empathy opener on every "Help me …" request, and no safety
net for a message about self-harm.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from app.agents import AgentRoute, supervisor
from app.service import _EXPLICIT_WORKFLOW_START_RE
from app.text_signals import (
    REPAIR_QUESTION,
    crisis_support_reply,
    detect_crisis,
    detect_user_distress,
    distress_trajectory,
    empathy_ack,
    is_feeling_only,
    names_a_task,
    repair_reply,
    strip_repeated_ack,
)
from app.tools.empathy import assess
from app.turn_guidance import (
    HANDOFF_ACTION,
    apply_turn_guidance,
    finalize_turn_actions,
    guided_mode_action,
    turns_from_history,
)
from app.verified_resources import is_authoritative_ura_url
from app.workflows.loader import load_workflow
from app.workflows.registry import WorkflowRegistry, WorkflowSession, compute_workflow_progress

FLOWS_DIR = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"


def _load_all_flows() -> None:
    WorkflowRegistry._workflows.clear()
    for path in sorted(FLOWS_DIR.glob("*.yaml")):
        WorkflowRegistry.register(load_workflow(path))


class TriggerNormalisationTests(unittest.TestCase):
    def setUp(self) -> None:
        _load_all_flows()

    def test_task_phrasings_reach_their_flow(self) -> None:
        cases = {
            # Fell through to the VAT explainer on the live stack.
            "Walk me through filing my VAT return": "return_filing",
            "I need a tax clearance certificate": "tax_clearance",
            "Walk me through getting a tax clearance certificate": "tax_clearance",
            # Abstained on the live stack.
            "Guide me through registering my imported car": "motor_vehicle_registration",
            "How do I get a digital number plate?": "motor_vehicle_registration",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                matched = WorkflowRegistry.match_trigger(query)
                self.assertIsNotNone(matched, query)
                self.assertEqual(matched.id, expected)

    def test_flow_name_is_a_trigger(self) -> None:
        for wf_id, name in (
            ("return_filing", "Return Filing"),
            ("tax_clearance", "Tax Clearance Certificate"),
            ("motor_vehicle_registration", "Motor Vehicle Registration"),
        ):
            with self.subTest(name=name):
                matched = WorkflowRegistry.match_trigger(f"Guide me step by step through {name}")
                self.assertEqual(matched.id, wf_id)

    def test_calculators_are_never_started_by_name(self) -> None:
        # Calculators are started by the calculator router only.
        matched = WorkflowRegistry.match_trigger("open the PAYE Calculator")
        self.assertTrue(matched is None or not matched.id.startswith("calc_"))


class ReviewRegressionTests(unittest.TestCase):
    """Findings from the 2026-09-29 code review of this branch, one test each."""

    def setUp(self) -> None:
        _load_all_flows()

    def test_a_past_event_does_not_start_a_flow(self) -> None:
        for message in (
            "I filed my return yesterday but the portal shows an error",
            "I registered for a TIN last year and lost the certificate",
            "I applied for tin but got no email",
        ):
            with self.subTest(message=message):
                matched = WorkflowRegistry.match_trigger(message)
                self.assertTrue(matched is None or matched.id not in ("return_filing", "tin_registration"))

    def test_nil_return_still_starts_return_filing(self) -> None:
        matched = WorkflowRegistry.match_trigger("I want to submit the nil return for my company")
        self.assertEqual(matched.id, "return_filing")

    def test_account_state_requests_escalate_even_with_help_me(self) -> None:
        for query in ("Please help me, my account is locked", "Help me check my balance"):
            with self.subTest(query=query):
                self.assertEqual(supervisor.classify(query).route, AgentRoute.ESCALATE)
        self.assertNotEqual(supervisor.classify("How to file my return").route, AgentRoute.ESCALATE)

    def test_no_guide_offer_outside_answer_turns(self) -> None:
        for mode in ("out_of_jurisdiction", "out_of_scope", "officer_reply", "greeting", "calculator"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    guided_mode_action("How do I file a return in Kenya?", {"retrieval_mode": mode}), ""
                )

    def test_contractions_do_not_hide_a_feeling(self) -> None:
        for message in ("I'm confused", "I'm so frustrated, this is useless", "I've had enough, it's useless"):
            with self.subTest(message=message):
                self.assertTrue(is_feeling_only(message))

    def test_confusion_repair_does_not_promise_a_rephrase(self) -> None:
        self.assertNotIn(empathy_ack("confusion"), repair_reply("confusion", repeated=False))

    def test_a_real_question_names_a_task_and_small_talk_does_not(self) -> None:
        self.assertTrue(names_a_task("What is chargeable income?"))
        self.assertFalse(names_a_task("Hello, good morning"))
        self.assertFalse(names_a_task("This is useless"))

    def test_escalation_clears_competing_actions(self) -> None:
        result = {"next_actions": ["Guide me step by step through Return Filing", HANDOFF_ACTION, "Prepare your TIN"]}
        finalize_turn_actions(result, escalated=True)
        self.assertEqual(result["next_actions"], ["Prepare your TIN"])
        kept = {"next_actions": [HANDOFF_ACTION]}
        self.assertEqual(finalize_turn_actions(kept, escalated=False)["next_actions"], [HANDOFF_ACTION])

    def test_history_in_either_shape_becomes_turns(self) -> None:
        stored = [{"user_message": "hi", "bot_reply": "hello"}]
        chat = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        self.assertEqual(turns_from_history(stored), stored)
        self.assertEqual(turns_from_history(chat), stored)


class FlowShapeTests(unittest.TestCase):
    def test_no_question_follows_an_information_step(self) -> None:
        # advance() shows an information-only step once and moves on, so a
        # question after one receives the reply meant for the information step.
        for path in sorted(FLOWS_DIR.glob("*.yaml")):
            wf = load_workflow(path)
            info_seen = ""
            for step in wf.steps:
                if not step.slot and not step.tool:
                    info_seen = info_seen or step.id
                elif step.slot:
                    with self.subTest(flow=wf.id, step=step.id):
                        self.assertEqual(info_seen, "", f"{step.id} follows information step {info_seen}")


class NewJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        _load_all_flows()

    def test_every_link_resolves_to_an_official_page(self) -> None:
        for wf_id in ("tax_clearance", "motor_vehicle_registration"):
            wf = WorkflowRegistry.get(wf_id)
            self.assertIsNotNone(wf, wf_id)
            for step in wf.steps:
                for link in [step.portal_action, *step.resources]:
                    if not link:
                        continue
                    with self.subTest(flow=wf_id, step=step.id):
                        self.assertTrue(is_authoritative_ura_url(link["url"]), link)

    def test_tax_clearance_branches_on_the_four_conditions(self) -> None:
        wf = WorkflowRegistry.get("tax_clearance")
        session = WorkflowSession(
            workflow_id=wf.id,
            slots={
                "taxpayer_type": "company",
                "certificate_type": "one transaction",
                "returns_filed": False,
                "associates_filed": False,
                "tax_paid": True,
            },
        )
        _, _, track = compute_workflow_progress(wf, session)
        ids = {entry["id"] for entry in track}
        self.assertIn("returns_guidance", ids)
        self.assertIn("collect_directors_filed", ids)
        self.assertIn("associates_guidance", ids)
        self.assertNotIn("collect_partners_filed", ids)
        self.assertNotIn("annual_note", ids)
        self.assertNotIn("payment_guidance", ids)

    def test_tax_clearance_asks_every_question_before_any_guidance(self) -> None:
        # v1 showed the annual-certificate note between questions, and the
        # taxpayer's "ok" was stored as the answer to "have you filed every
        # return?", a question they never saw.
        session = WorkflowRegistry.create_session("tax_clearance")
        turn = WorkflowRegistry.advance(session, "")
        asked = []
        for answer in ("company", "annual", "no", "no", "yes"):
            asked.append(turn.step_id)
            turn = WorkflowRegistry.advance(session, answer)
        self.assertEqual(
            asked,
            [
                "collect_taxpayer_type",
                "collect_certificate_type",
                "collect_returns_filed",
                "collect_directors_filed",
                "collect_tax_paid",
            ],
        )
        self.assertEqual(session.slots["returns_filed"], False)
        self.assertEqual(turn.step_id, "annual_note")

    def test_vehicle_journey_separates_first_registration_from_upgrade(self) -> None:
        wf = WorkflowRegistry.get("motor_vehicle_registration")
        first = WorkflowSession(wf.id, slots={"registration_kind": "first registration", "has_tin": True})
        upgrade = WorkflowSession(wf.id, slots={"registration_kind": "already registered", "has_tin": True})
        first_ids = {e["id"] for e in compute_workflow_progress(wf, first)[2]}
        upgrade_ids = {e["id"] for e in compute_workflow_progress(wf, upgrade)[2]}
        self.assertIn("plate_fee", first_ids)
        self.assertNotIn("request_digital_plate", first_ids)
        self.assertIn("request_digital_plate", upgrade_ids)
        self.assertNotIn("plate_fee", upgrade_ids)

    def test_plate_fee_names_the_bank_not_mobile_money(self) -> None:
        step = next(s for s in WorkflowRegistry.get("motor_vehicle_registration").steps if s.id == "plate_fee")
        self.assertIn("714,300", step.question)
        self.assertIn("not by mobile money", step.question)


class EmotionSignalTests(unittest.TestCase):
    def test_a_request_is_not_distress(self) -> None:
        for message in (
            "Help me register for a TIN",
            "Help me file my return",
            "Help me file an objection",
            "I lost my TIN certificate",
        ):
            with self.subTest(message=message):
                self.assertEqual(detect_user_distress(message), "")

    def test_a_worried_request_is_still_anxiety(self) -> None:
        self.assertEqual(detect_user_distress("I'm worried about the audit, please help me"), "anxiety")

    def test_repeated_opener_is_dropped_but_the_answer_kept(self) -> None:
        ack = empathy_ack("anxiety")
        reply = f"{ack}\n\nFile by the 15th."
        self.assertEqual(strip_repeated_ack(reply, []), reply)
        self.assertEqual(strip_repeated_ack(reply, [f"{ack}\n\nEarlier answer."]), "File by the 15th.")
        other = f"{empathy_ack('frustration')}\n\nEarlier answer."
        self.assertEqual(strip_repeated_ack(reply, [other]), reply)

    def test_trajectory_needs_the_current_turn_to_be_upset(self) -> None:
        upset = ["This is useless", "It still does not work"]
        self.assertTrue(distress_trajectory("frustration", upset)["sustained"])
        self.assertFalse(distress_trajectory("", upset)["sustained"])
        self.assertFalse(distress_trajectory("frustration", ["What is VAT?", "Thanks"])["sustained"])


class ConversationRepairTests(unittest.TestCase):
    def test_feeling_only_messages_name_no_task(self) -> None:
        for message in ("This is useless", "It still does not work", "I am so confused", "Why is this not working"):
            with self.subTest(message=message):
                self.assertTrue(is_feeling_only(message))

    def test_a_named_thing_is_still_answered(self) -> None:
        for message in (
            "The portal is not working",
            "I don't understand what chargeable income means",
            "My eTax password does not work",
        ):
            with self.subTest(message=message):
                self.assertFalse(is_feeling_only(message))

    def test_repair_asks_first_then_leads_with_a_person(self) -> None:
        first = repair_reply("frustration", repeated=False)
        self.assertTrue(first.startswith(empathy_ack("frustration")))
        self.assertIn(REPAIR_QUESTION, first)
        again = repair_reply("frustration", repeated=True)
        self.assertNotIn(REPAIR_QUESTION, again)
        self.assertIn("Talk to an officer", again)


class CrisisTests(unittest.TestCase):
    def test_self_harm_is_detected_in_three_languages(self) -> None:
        for message in (
            "I can't pay this, I want to kill myself",
            "I'm thinking about suicide",
            "Njagala okwetta",
            "Nataka kujiua",
        ):
            with self.subTest(message=message):
                self.assertTrue(detect_crisis(message))

    def test_tax_language_is_not_a_crisis(self) -> None:
        for message in (
            "How do I end my VAT registration?",
            "The penalty is killing my business",
            "What is the deadline to file?",
        ):
            with self.subTest(message=message):
                self.assertFalse(detect_crisis(message))

    def test_reply_gives_crisis_lines_and_no_tax_content(self) -> None:
        reply = crisis_support_reply()
        for number in ("999", "112", "0800 21 21 21"):
            self.assertIn(number, reply)
        self.assertNotIn("%", reply)
        self.assertNotIn("penalt", reply.lower())


class EmpathyToolHistoryTests(unittest.TestCase):
    def test_sustained_frustration_offers_a_person(self) -> None:
        result = assess("It still does not work", history=["This is useless", "Still nothing works"])
        self.assertTrue(result["sustained"])
        self.assertTrue(result["offer_human_handoff"])

    def test_one_mild_message_does_not(self) -> None:
        result = assess("this is annoying", history=["What is the VAT rate?"])
        self.assertFalse(result["sustained"])
        self.assertFalse(result["offer_human_handoff"])

    def test_crisis_overrides_every_other_reading(self) -> None:
        result = assess("I want to end my life")
        self.assertTrue(result["crisis"])
        self.assertTrue(result["offer_human_handoff"])
        self.assertEqual(result["acknowledgement"], "")

    def test_politeness_does_not_raise_intensity(self) -> None:
        self.assertEqual(assess("I am worried, please")["intensity"], "low")


class TurnGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        _load_all_flows()
        os.environ["FLAG_WORKFLOWS"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("FLAG_WORKFLOWS", None)

    def test_how_to_answer_offers_the_matching_flow(self) -> None:
        result = {"reply": "Log in to eTax…", "retrieval_mode": "faq_priority", "next_actions": []}
        apply_turn_guidance("How do I file my return?", result)
        offer = result["next_actions"][0]
        self.assertEqual(offer, "Guide me step by step through Return Filing")
        # Clicking the offer sends its text; it must start that flow.
        self.assertTrue(_EXPLICIT_WORKFLOW_START_RE.search(offer))
        self.assertEqual(WorkflowRegistry.match_trigger(offer).id, "return_filing")

    def test_no_offer_inside_a_flow_or_a_handoff(self) -> None:
        for result in (
            {"reply": "Step 1", "retrieval_mode": "workflow", "workflow": {"name": "Return Filing"}},
            {"reply": "Flagged", "retrieval_mode": "escalated", "escalation_required": True},
            {"reply": "Which one?", "retrieval_mode": "clarification"},
        ):
            with self.subTest(mode=result["retrieval_mode"]):
                self.assertEqual(guided_mode_action("How do I file my return?", result), "")

    def test_an_abstention_still_offers_the_guide(self) -> None:
        result = {"reply": "I couldn't find a reliable answer.", "retrieval_mode": "abstained"}
        self.assertEqual(
            guided_mode_action("How do I register my motor vehicle?", result),
            "Guide me step by step through Motor Vehicle Registration",
        )

    def test_repeated_opener_is_stripped_against_recent_turns(self) -> None:
        ack = empathy_ack("anxiety")
        result = {"reply": f"{ack}\n\nHere is the next step.", "retrieval_mode": "hybrid"}
        apply_turn_guidance(
            "I'm worried about my assessment",
            result,
            recent_turns=[{"user_message": "I'm worried", "bot_reply": f"{ack}\n\nFirst answer."}],
        )
        self.assertEqual(result["reply"], "Here is the next step.")

    def test_sustained_distress_adds_an_officer_action_that_escalates(self) -> None:
        result = {"reply": "Try again.", "retrieval_mode": "hybrid", "next_actions": []}
        apply_turn_guidance(
            "It still does not work",
            result,
            recent_turns=[
                {"user_message": "This is useless", "bot_reply": "…"},
                {"user_message": "Still not working", "bot_reply": "…"},
            ],
        )
        self.assertIn(HANDOFF_ACTION, result["next_actions"])
        self.assertEqual(supervisor.classify(HANDOFF_ACTION).route, AgentRoute.ESCALATE)


class AccountEscalationTests(unittest.TestCase):
    def test_how_to_questions_are_not_account_lookups(self) -> None:
        for query in ("How do I file my return?", "Where can I see my account statement?"):
            with self.subTest(query=query):
                self.assertNotEqual(supervisor.classify(query).route, AgentRoute.ESCALATE)

    def test_account_state_questions_still_escalate(self) -> None:
        for query in ("What is my balance?", "Has my return been received?", "My TIN is blocked"):
            with self.subTest(query=query):
                self.assertEqual(supervisor.classify(query).route, AgentRoute.ESCALATE)


if __name__ == "__main__":
    unittest.main()
