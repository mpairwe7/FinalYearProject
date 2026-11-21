"""A reply that is only the contact footer is served as an abstention (G47).

On 2026-09-02 a customs question was answered with the contact footer and nothing
else, in ``hybrid`` mode. It carried no abstention marker, so ``_is_non_answer`` in
both scoring harnesses counted it as an answer. A model reply of nothing but a
citation marker is enough to produce one on REST: ``_add_conversational_frame``
puts the footer and a follow-up suggestion after the marker.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

from app import mt
from app.guardrails import output_checks
from app.rag import pipeline
from app.research.web_research import LIVE_DISCLOSURES, LIVE_UNAVAILABLE
from app.text_signals import (
    ABSTENTION_REPLY,
    CONTACT_FOOTER,
    LOCALIZED_CONTACT_FOOTERS,
    jurisdiction_scope_caveat,
)

APP_ROOT = Path(__file__).resolve().parents[2]
FOLLOW_UP = (
    "You might also want to know: "
    "How do I claim a refund or duty drawback on import or export customs duties?"
)

FOOTER_ONLY = {
    "footer": CONTACT_FOOTER,
    "footer and whitespace": f"\n\n{CONTACT_FOOTER}\n \n",
    "lone citation marker": f"[1]\n\n{CONTACT_FOOTER}",
    "Markdown, marker and follow-up": f"**[1]**\n\n---\n\n{CONTACT_FOOTER}\n\n{FOLLOW_UP}",
    "comparison caveat": f"{CONTACT_FOOTER}\n\n{jurisdiction_scope_caveat('Kenya')}",
    "fallback disclosure": (
        f"[1]\n\n{CONTACT_FOOTER}\n\n{FOLLOW_UP}\n\n"
        f"{pipeline.DEGRADED_DISCLOSURES['generation_unavailable']}"
    ),
}

WITH_AN_ANSWER = {
    "one word": f"Yes.\n\n{CONTACT_FOOTER}",
    "one cited line": f"The VAT rate is 18% [1].\n\n{CONTACT_FOOTER}\n\n{FOLLOW_UP}",
    "answer after the footer": f"{CONTACT_FOOTER}\n\nThe VAT rate is 18%.",
    "answer and caveat": (
        f"Uganda's VAT rate is 18%.\n\n{CONTACT_FOOTER}\n\n{jurisdiction_scope_caveat('Kenya')}"
    ),
    "answer that opens like the footer": (
        f"If you get stuck at any step, restart the EFRIS wizard.\n\n{CONTACT_FOOTER}"
    ),
}

LOCALIZED_ABSTENTION = "<localized abstention>"


def _fake_localize(text: str, locale: str, **_kwargs) -> str:
    return LOCALIZED_ABSTENTION if text == ABSTENTION_REPLY and locale != "en" else text


def _harness(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, APP_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rest(reply: str, locale: str = "en") -> dict:
    model = pipeline.ChatModel.__new__(pipeline.ChatModel)
    source = "East_Africa_Community_Customs_Management_Act_Revised.pdf"
    english = {
        "reply": reply,
        "locale": locale,
        "retrieval_mode": "hybrid",
        "sources": [source],
        "citations": [{"ref": "[1]", "source": source}],
    }
    patch = mock.patch.object
    with patch(pipeline.ChatModel, "_generate_en", return_value=english), \
            patch(pipeline.ChatModel, "_get_suspended_workflow_name", return_value=None), \
            patch(pipeline, "apply_turn_guidance"), \
            patch(pipeline, "_recent_turns_for_guidance", return_value=[]), \
            patch(pipeline, "localize_reply", side_effect=_fake_localize):
        # nosemgrep: ura-llm01-raw-user-input-to-llm  # _generate_en mocked; no model call.
        return model.generate(message="I import goods from Kenya to Uganda: what customs duty?")


def _tagged_translation(text: str, locale: str) -> str:
    return text if text.strip() == "[1]" else f"<{locale}> {text}"


def _stream(retrieval: dict, *, tokens: list[str] | None = None, translate=None) -> list:
    """One streamed turn. With *translate*, the real localize_reply runs over that MT stub."""
    model = mock.MagicMock()
    model.generate_retrieval_only.return_value = retrieval

    def fake_guards(_model, **kwargs):
        return {
            "reply": kwargs["reply"], "revised": False, "faithfulness": None, "escalate": False,
            "escalation_reason": "", "response_judge": {}, "handoff": None, "ticket_id": "",
            "figure_lock": {},
        }

    async def run():
        return [
            event
            async for event in pipeline.run_chat_turn(
                model, message="customs duty from Kenya", conversation_id=None, top_k=4,
                locale="en", session_id=None, request_id=None, user_id=None, tenant_id="default",
            )
        ]

    patch = mock.patch.object
    localization = (
        patch(pipeline, "_translate_reply", side_effect=translate)
        if translate
        else patch(pipeline, "localize_reply", side_effect=_fake_localize)
    )
    with localization, \
            patch(pipeline.flags, "is_enabled", return_value=False), \
            patch(pipeline.generator_module, "is_available", return_value=True), \
            patch(pipeline, "stream_llm_tokens", return_value=iter(tokens or ())), \
            patch(pipeline, "_apply_output_guards", side_effect=fake_guards), \
            patch(pipeline._english_rendering, "answer_in_english", side_effect=str), \
            patch(output_checks.OutputGuard, "should_abstain", return_value=False):
        return asyncio.run(run())


def _client_shown_text(events) -> str:
    shown = ""
    for kind, payload in events:
        if kind == "token":
            shown += payload
        elif kind == "revision":
            shown = payload
    return shown


def _short_circuit(reply: str, locale: str = "en") -> dict:
    return {
        "reply": reply, "retrieval_mode": "calculator", "locale": locale, "_short_circuit": True,
        "sources": ["FY2026-27 rate table"], "citations": [], "_hits": [],
    }


def _hybrid(locale: str = "en") -> dict:
    hit = {"text": "Import duty follows the EAC Common External Tariff.", "source": "eac.pdf"}
    return {
        "reply": hit["text"], "retrieval_mode": "hybrid", "locale": locale, "sources": ["eac.pdf"],
        "citations": [{"ref": "[1]", "source": "eac.pdf"}], "_hits": [hit], "_history": [],
        "_rewritten": "customs duty from Kenya",
    }


class RestFooterOnlyReplyTest(unittest.TestCase):
    def test_a_footer_only_reply_becomes_the_abstention(self) -> None:
        for label, reply in FOOTER_ONLY.items():
            with self.subTest(label):
                out = _rest(reply)
                self.assertEqual(out["reply"], ABSTENTION_REPLY)
                self.assertEqual(out["retrieval_mode"], "abstained")
                self.assertEqual((out["sources"], out["citations"]), ([], []))

    def test_a_localized_footer_only_reply_becomes_the_localized_abstention(self) -> None:
        for locale, footer in LOCALIZED_CONTACT_FOOTERS.items():
            with self.subTest(locale):
                out = _rest(f"[1]\n\n{footer}", locale=locale)
                self.assertEqual(out["reply"], LOCALIZED_ABSTENTION)
                self.assertEqual(out["retrieval_mode"], "abstained")

    def test_a_reply_with_an_answer_keeps_it(self) -> None:
        for label, reply in WITH_AN_ANSWER.items():
            with self.subTest(label):
                out = _rest(reply)
                self.assertEqual(out["reply"], reply)
                self.assertEqual(out["retrieval_mode"], "hybrid")


class StreamFooterOnlyReplyTest(unittest.TestCase):
    def test_a_footer_only_reply_is_revised_to_the_abstention(self) -> None:
        for label, reply in FOOTER_ONLY.items():
            with self.subTest(label):
                events = _stream(_short_circuit(reply))
                self.assertEqual(_client_shown_text(events), ABSTENTION_REPLY)
                log = next(payload for kind, payload in events if kind == "_log")
                self.assertEqual(log["full_reply"], ABSTENTION_REPLY)
                self.assertEqual(log["result"]["retrieval_mode"], "abstained")

    def test_the_abstention_withdraws_the_sources_before_done(self) -> None:
        events = _stream(_short_circuit(CONTACT_FOOTER))
        kinds = [kind for kind, _ in events]
        done = kinds.index("done")
        self.assertIn("revision", kinds[:done])
        grounding = [payload for kind, payload in events[:done] if kind == "grounding"][-1]
        self.assertEqual(grounding["retrieval_mode"], "abstained")
        self.assertEqual((grounding["sources"], grounding["citations"]), ([], []))

    def test_a_localized_footer_only_reply_is_revised_to_the_localized_abstention(self) -> None:
        for locale, footer in LOCALIZED_CONTACT_FOOTERS.items():
            with self.subTest(locale):
                events = _stream(_short_circuit(f"[1]\n\n{footer}", locale=locale))
                self.assertEqual(_client_shown_text(events), LOCALIZED_ABSTENTION)

    def test_a_streamed_body_that_was_only_the_footer_is_replaced_once(self) -> None:
        chunks = ["If you get stuck at any step, URA is happy to help: visit https://ura.go.ug, ",
                  CONTACT_FOOTER.split("ura.go.ug, ", 1)[1]]
        events = _stream(_hybrid(), tokens=chunks)
        streamed = "".join(payload for kind, payload in events if kind == "token")
        self.assertEqual(streamed.strip(), CONTACT_FOOTER)
        self.assertEqual(_client_shown_text(events), ABSTENTION_REPLY)

    def test_a_reply_with_an_answer_is_not_revised(self) -> None:
        for label, reply in WITH_AN_ANSWER.items():
            with self.subTest(label):
                events = _stream(_short_circuit(reply))
                self.assertEqual(_client_shown_text(events), reply)
                self.assertNotIn("revision", [kind for kind, _ in events])
        answer = "Import duty follows the EAC tariff [1]. "
        events = _stream(_hybrid(), tokens=[answer, CONTACT_FOOTER])
        self.assertNotIn("revision", [kind for kind, _ in events])
        self.assertTrue(_client_shown_text(events).startswith(answer.strip()))

    def test_a_localized_follow_up_or_disclosure_is_not_an_answer(self) -> None:
        lines_after_the_footer = {
            "follow-up": {"reply": f"[1]\n\n{CONTACT_FOOTER}\n\n{FOLLOW_UP}"},
            "live-check disclosure": {
                "reply": f"[1]\n\n{CONTACT_FOOTER}",
                "_live_check_disclosure": LIVE_DISCLOSURES[LIVE_UNAVAILABLE],
            },
        }
        for locale, footer in LOCALIZED_CONTACT_FOOTERS.items():
            for label, fields in lines_after_the_footer.items():
                with self.subTest(locale=locale, line=label):
                    mt.cache.clear()
                    retrieval = {**_short_circuit("", locale=locale), **fields}
                    events = _stream(retrieval, translate=_tagged_translation)
                    first = next(payload for kind, payload in events if kind == "token")
                    self.assertIn(footer, first)
                    self.assertIn(f"<{locale}> ", first)
                    mt.cache.clear()
                    with mock.patch.object(pipeline, "_translate_reply", side_effect=_tagged_translation):
                        abstention = pipeline.localize_reply(ABSTENTION_REPLY, locale)
                    self.assertNotEqual(abstention, ABSTENTION_REPLY)
                    self.assertEqual(_client_shown_text(events), abstention)


class ConsentedMemoryTest(unittest.TestCase):
    def test_memory_keeps_what_the_taxpayer_was_served(self) -> None:
        model = pipeline.ChatModel.__new__(pipeline.ChatModel)
        cases = {
            "footer only": (FOOTER_ONLY["lone citation marker"], ABSTENTION_REPLY),
            "with an answer": (WITH_AN_ANSWER["one word"], WITH_AN_ANSWER["one word"]),
        }
        for label, (reply, kept) in cases.items():
            with self.subTest(label):
                memory = mock.MagicMock()
                with mock.patch.object(pipeline, "get_memory_service", return_value=memory):
                    model._persist_personalization_turn(
                        user_id="u-1", tenant_id="default", conversation_id="c-1",
                        message="customs duty from Kenya", reply=reply,
                        agent_role="rag_answerer", personalization={"consent_granted": True},
                    )
                turns = memory.absorb_conversation.call_args.args[2]
                self.assertEqual(turns[1]["content"], kept)


class ScoredAsAnAbstentionTest(unittest.TestCase):
    def test_both_harnesses_count_the_new_reply_as_a_non_answer(self) -> None:
        harnesses = (
            _harness("_g47_faq_harness", "scripts/evaluate_1000_faqs_ngrok.py"),
            _harness("_g47_accuracy_harness", "tests/load/tax_education_accuracy_eval.py"),
        )
        footer_only = FOOTER_ONLY["lone citation marker"]
        out = _rest(footer_only)
        for harness in harnesses:
            with self.subTest(harness.__name__):
                self.assertFalse(harness._is_non_answer(footer_only, "hybrid"))
                self.assertTrue(harness._is_non_answer(out["reply"], out["retrieval_mode"]))
                self.assertTrue(harness._is_non_answer(out["reply"], "hybrid"))


if __name__ == "__main__":
    unittest.main()
