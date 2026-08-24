"""The answer must come back in the language the question was asked in.

Companion to ``test_reply_localization.py``, which covers ``localize_reply``
itself. This file covers the *streaming* core (``run_chat_turn``) and the
guards added around translation.

Three defects, all producing the same symptom a taxpayer reported as "the
model answers in English":

  * ``run_chat_turn`` — the streaming core behind both SSE and the WebSocket,
    and therefore what the web client actually uses — localized against the
    locale the CALLER passed, not the one detection resolved. A taxpayer who
    simply types Luganda without touching the language picker arrives with
    locale="en"; ``generate_retrieval_only`` detects "lg" and records it on
    the result, and every localize_reply call downstream still read "en".
    ``ChatModel.generate`` was fixed for exactly this and the streaming twin
    was not.

  * the agentic branch (``tool_use`` on) never called localize_reply at all.

  * the empty-stream fallback — open breaker, no tokens produced — yielded
    the English extractive reply straight to the client.

Plus the figure guard: machine translation paraphrases, and a paraphrased
amount is a different amount. A reply that said "UGX 235,000" and comes back
saying "UGX 253,000" is indistinguishable from the assistant inventing a
figure, so localize_reply refuses it and serves the English text.
"""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock as mock

from app import mt, service


def _drain(agen):
    """Collect every (event, payload) tuple an async generator yields."""

    async def _run():
        return [event async for event in agen]

    return asyncio.run(_run())


class EffectiveLocaleTest(unittest.TestCase):
    """The locale detection resolved, not the one the caller sent."""

    def _run_turn(self, retrieval_result, *, caller_locale="en"):
        model = mock.MagicMock()
        model.generate_retrieval_only.return_value = retrieval_result
        with mock.patch.object(service, "localize_reply") as localize:
            localize.side_effect = lambda text, locale: f"[{locale}] {text}"
            events = _drain(
                service.run_chat_turn(
                    model,
                    message="TIN ntandika ntya",
                    conversation_id=None,
                    top_k=4,
                    locale=caller_locale,
                    session_id=None,
                    request_id=None,
                    user_id=None,
                    tenant_id="default",
                )
            )
        return events, localize

    def test_short_circuit_uses_the_detected_locale(self):
        events, localize = self._run_turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "lg",
                "sources": [],
                "citations": [],
                "_hits": [],
            }
        )
        localize.assert_called_once()
        self.assertEqual(localize.call_args.args[1], "lg")
        tokens = [payload for kind, payload in events if kind == "token"]
        self.assertEqual(tokens, ["[lg] Register on ura.go.ug."])

    def test_an_explicit_locale_still_wins_when_detection_agrees(self):
        _, localize = self._run_turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "sw",
                "_hits": [],
            },
            caller_locale="sw",
        )
        self.assertEqual(localize.call_args.args[1], "sw")

    def test_english_turns_are_not_translated(self):
        events, localize = self._run_turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "en",
                "_hits": [],
            }
        )
        # localize_reply is still called (it is a no-op for "en"), but no
        # translation phase is announced to the client.
        phases = [kind for kind, _ in events if kind.startswith("translation.")]
        self.assertEqual(phases, [])

    def test_the_translation_phase_is_announced_for_a_non_english_turn(self):
        events, _ = self._run_turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "lg",
                "_hits": [],
            }
        )
        phases = [kind for kind, _ in events if kind.startswith("translation.")]
        self.assertEqual(phases, ["translation.started", "translation.completed"])

    def test_retrieval_completed_reports_the_effective_locale(self):
        events, _ = self._run_turn(
            {
                "reply": "Register on ura.go.ug.",
                "retrieval_mode": "abstained",
                "locale": "lg",
                "_hits": [],
            }
        )
        completed = next(p for kind, p in events if kind == "retrieval.completed")
        self.assertEqual(completed["locale"], "lg")


class EmptyStreamFallbackTest(unittest.TestCase):
    """No tokens produced still has to answer in the taxpayer's language."""

    def test_extractive_fallback_is_localized(self):
        model = mock.MagicMock()
        model.generate_retrieval_only.return_value = {
            "reply": "VAT is 18%.",
            "retrieval_mode": "hybrid",
            "locale": "lg",
            "_hits": [{"text": "VAT is 18%.", "source": "vat.csv"}],
            "_history": [],
            "_rewritten": "vat rate",
        }
        with mock.patch.object(service, "localize_reply") as localize, mock.patch.object(
            service.llm_module, "is_available", return_value=True
        ), mock.patch.object(
            service.flags, "is_enabled", return_value=False
        ), mock.patch.object(
            service, "stream_llm_tokens", return_value=iter(())
        ):
            localize.side_effect = lambda text, locale: f"[{locale}] {text}"
            events = _drain(
                service.run_chat_turn(
                    model,
                    message="VAT eri ki",
                    conversation_id=None,
                    top_k=4,
                    locale="en",
                    session_id=None,
                    request_id=None,
                    user_id=None,
                    tenant_id="default",
                )
            )
        tokens = [payload for kind, payload in events if kind == "token"]
        self.assertEqual(tokens, ["[lg] VAT is 18%."])


class FigureFidelityTest(unittest.TestCase):
    """A translation may not change a figure."""

    def setUp(self):
        mt.cache.clear()

    def test_a_changed_amount_serves_the_english_text(self):
        english = "The PAYE due on this salary is UGX 235,000 for the month."
        with mock.patch.object(
            service, "_translate_reply", return_value="Omusolo gwa PAYE ye UGX 253,000 buli mwezi."
        ):
            out = service.localize_reply(english, "lg")
        self.assertEqual(out, english)

    def test_a_changed_percentage_serves_the_english_text(self):
        english = "Value Added Tax is charged at 18% on taxable supplies."
        with mock.patch.object(
            service, "_translate_reply", return_value="VAT esolooza ku 20% ku bintu ebisoloozebwa."
        ):
            out = service.localize_reply(english, "lg")
        self.assertEqual(out, english)

    def test_a_faithful_translation_is_served(self):
        english = "Value Added Tax is charged at 18% on taxable supplies."
        luganda = "VAT esolooza ku 18% ku bintu ebisoloozebwa mu Uganda."
        with mock.patch.object(service, "_translate_reply", return_value=luganda):
            out = service.localize_reply(english, "lg")
        self.assertEqual(out, luganda)

    def test_a_reply_with_no_figures_is_never_blocked(self):
        english = "Visit any URA office and ask for the registration desk."
        luganda = "Genda mu ofiisi ya URA yonna obuuze ku mmeeza y’okwewandiisa."
        with mock.patch.object(service, "_translate_reply", return_value=luganda):
            out = service.localize_reply(english, "lg")
        self.assertEqual(out, luganda)


class ReplyCacheTest(unittest.TestCase):
    """The deterministic replies dominate this direction and never change."""

    def setUp(self):
        mt.cache.clear()

    def test_a_second_identical_reply_does_not_call_a_backend(self):
        english = "Visit ura.go.ug and choose Get a TIN."
        luganda = "Genda ku ura.go.ug olonde Funa TIN."
        with mock.patch.object(service, "_translate_reply", return_value=luganda) as backend:
            first = service.localize_reply(english, "lg")
            second = service.localize_reply(english, "lg")
        self.assertEqual(first, luganda)
        self.assertEqual(second, luganda)
        backend.assert_called_once()

    def test_a_rejected_translation_is_not_cached(self):
        english = "The threshold is UGX 150,000,000 per year."
        with mock.patch.object(
            service, "_translate_reply", return_value="Ekipimo kya UGX 100,000,000 buli mwaka."
        ) as backend:
            service.localize_reply(english, "lg")
            service.localize_reply(english, "lg")
        self.assertEqual(backend.call_count, 2)


if __name__ == "__main__":
    unittest.main()
