"""Tests for Luganda Receptionist Knowledge Parity, Cross-Lingual RAG Bridge, and Calibrated Escalations.

Validates the implementation of docs/plans/luganda-receptionist-knowledge-parity-plan.md:
- Direct cross-lingual understanding & English URA knowledge retrieval
- Preservation of statutory figures (18% VAT, 2% late payment penalty, rental tax rates)
- Voice-calibrated escalations: answering with officer offer on moderate confidence
- Immediate clean handoff on explicit human requests and legal disputes
- Hard failure handling on zero retrieval hits
- Telemetry metrics tracking Luganda latency and containment
"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.flags import flags
from app.receptionist.brain import LLMContextFrame, UraReceptionistBrain
from app.receptionist.lexicon import LUGANDA_ENGLISH_TAX_TERMS, normalize_luganda_tax_query
from app.receptionist.metrics import compute_call_metrics, get_aggregate_metrics, record_call_end_metrics
from app.receptionist.phrases import phrase
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import create_call, create_turn, get_call, init_receptionist_schema, list_turns


class TestLugandaLexiconNormalization(unittest.TestCase):
    """Pillar 3: Code-switching and acronym normalization."""

    def test_rental_tax_normalization(self):
        query = "Nsasula ntya omusolo gwa rental?"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("Rental Income Tax", normalized)

    def test_tin_registration_normalization(self):
        query = "Njagala okusaba TIN ku URA"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("TIN registration", normalized)

    def test_return_filing_normalization(self):
        query = "Nkola ntya okufayiringa return mu kiseera kino?"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("file tax return", normalized)

    def test_vat_rate_normalization(self):
        query = "Omusolo gwa VAT gw'ameka mu Uganda?"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("VAT rate", normalized)

    def test_motor_vehicle_normalization(self):
        query = "Omusolo gw'emmotoka gusasulwa gutya?"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("motor vehicle transfer tax", normalized)

    def test_late_payment_penalty_normalization(self):
        query = "Kiki ekibaawo bw'osasula omusolo nga wayiise obudde?"
        normalized = normalize_luganda_tax_query(query)
        self.assertIn("late payment penalty interest", normalized)

    def test_lexicon_mapping_contains_required_terms(self):
        self.assertIn("omusolo gwa rental", LUGANDA_ENGLISH_TAX_TERMS)
        self.assertIn("okusaba tin", LUGANDA_ENGLISH_TAX_TERMS)
        self.assertIn("okufayiringa return", LUGANDA_ENGLISH_TAX_TERMS)
        self.assertIn("omusolo gwa vat", LUGANDA_ENGLISH_TAX_TERMS)
        self.assertIn("omusolo gw'emmotoka", LUGANDA_ENGLISH_TAX_TERMS)


class TestLugandaCrossLingualKnowledgeReceptionist(unittest.IsolatedAsyncioTestCase):
    """Pillar 1 & 2: Cross-Lingual Knowledge Bridge and Voice-Calibrated Escalations."""

    async def asyncSetUp(self):
        db.init_db()
        init_receptionist_schema()

        self.call_id = f"call_lg_{uuid.uuid4().hex[:8]}"
        self.state = CallState(
            call_id=self.call_id,
            conversation_id=f"conv_{self.call_id}",
            user_id="taxpayer_lg_1",
            mode="ai",
            locale="lg",
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)

        self.chat_model = MagicMock()
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal", "language": "lg"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-LG-PARITY-1"

        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def test_vat_rate_knowledge_parity_preserves_18_percent(self):
        """VAT rate question in Luganda returns 18% statutory figure without officer transfer."""
        self.chat_model.generate.return_value = {
            "reply": "Omusolo gwa VAT mu Uganda guli ku 18% ku bintu n'obuweereza ebisinga.",
            "sources": [{"text": "The standard VAT rate in Uganda is 18% under the Value Added Tax Act."}],
            "faithfulness_score": 0.98,
            "confidence": 0.95,
            "escalation_required": False,
        }

        await self.brain.process_frame(LLMContextFrame(context="Omusolo gwa VAT gw'ameka mu Uganda?"))

        # Knowledge retrieval queries English statutes directly
        self.assertEqual(self.chat_model.generate.call_args.kwargs["locale"], "en")
        self.assertEqual(self.room.state.mode, "ai")

        turns = list_turns(self.call_id)
        self.assertEqual(len(turns), 2)
        assistant_turn = turns[1]
        self.assertEqual(assistant_turn["speaker"], "assistant")
        self.assertTrue(any(term in assistant_turn["text"] for term in ("18%", "18", "ebitundu 18")))

    async def test_tin_registration_procedure_in_luganda(self):
        """TIN registration question is answered directly in Luganda without officer transfer."""
        self.chat_model.generate.return_value = {
            "reply": "Okufuna TIN, weewandiise ku mukutu gwa URA ku ura.go.ug oba genda ku ofiisi ya URA.",
            "sources": [{"text": "To register for a TIN, visit the nearest URA office or register online at ura.go.ug."}],
            "faithfulness_score": 0.95,
            "confidence": 0.90,
            "escalation_required": False,
        }

        await self.brain.process_frame(LLMContextFrame(context="Nnyinza ntya okwewandiisa okufuna TIN?"))

        self.assertEqual(self.room.state.mode, "ai")
        turns = list_turns(self.call_id)
        self.assertEqual(len(turns), 2)
        self.assertTrue(any("TIN" in t["text"] or "ura.go.ug" in t["text"] for t in turns[1:]))

    async def test_rental_income_tax_inquiry_in_luganda(self):
        """Rental tax question preserves statutory details and avoids handoff."""
        self.chat_model.generate.return_value = {
            "reply": "Omusolo gwa rental income tax gusasulwa ku magoba g'obupangisa ku muwendo gwa 12%.",
            "sources": [{"text": "Rental income tax for individuals is 12% on gross rental income above threshold."}],
            "faithfulness_score": 0.95,
            "confidence": 0.88,
            "escalation_required": False,
        }

        await self.brain.process_frame(LLMContextFrame(context="Nsasula ntya omusolo gwa rental?"))

        self.assertEqual(self.room.state.mode, "ai")
        turns = list_turns(self.call_id)
        self.assertTrue(any(term in turns[1]["text"] for term in ("12%", "12", "ebitundu 12")))

    async def test_late_payment_penalty_preserves_2_percent(self):
        """Late tax payment penalty inquiry preserves 2% statutory figure."""
        self.chat_model.generate.return_value = {
            "reply": "Bw'osasula omusolo nga wayiise obudde, oweebwa penalty eya 2% buli mwezi ku musolo ogusigaddeyo.",
            "sources": [{"text": "Late payment penalty is 2% per month on unpaid tax under the Tax Procedures Code Act."}],
            "faithfulness_score": 0.97,
            "confidence": 0.92,
            "escalation_required": False,
        }

        await self.brain.process_frame(LLMContextFrame(context="Kiki ekibaawo bw'osasula omusolo nga wayiise obudde?"))

        self.assertEqual(self.room.state.mode, "ai")
        turns = list_turns(self.call_id)
        self.assertTrue(any(term in turns[1]["text"] for term in ("2%", "2", "ebitundu 2")))

    async def test_moderate_confidence_speaks_answer_and_offers_officer(self):
        """Pillar 2: Moderate confidence (0.35-0.50) speaks answer + offers officer without hard transfer."""
        self.chat_model.generate.return_value = {
            "reply": "Ebintu ebimu tebisasulwako VAT nga emmere ey'obulimi n'eddagala.",
            "sources": [{"text": "Agricultural inputs and pharmaceuticals are VAT-exempt supplies."}],
            "faithfulness_score": 0.85,
            "confidence": 0.42,
            "escalation_required": True,
            "escalation_reason": "moderate_retrieval_confidence",
        }

        await self.brain.process_frame(LLMContextFrame(context="Bintu ki ebitasasulwako musolo gwa VAT?"))

        # In voice mode, caller is NOT hard transferred; mode remains "ai"
        self.assertEqual(self.room.state.mode, "ai")
        turns = list_turns(self.call_id)
        self.assertEqual(len(turns), 2)
        spoken_text = turns[1]["text"]
        # Contains statutory answer
        self.assertTrue(any(term in spoken_text.lower() for term in ("vat", "v-a-t", "ebintu")))
        # Contains voice officer offer
        self.assertIn(phrase("officer_offer", "lg"), spoken_text)

    async def test_explicit_human_request_in_luganda_transfers_cleanly(self):
        """Explicit human request in Luganda triggers clean transfer with ticket."""
        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(LLMContextFrame(context="Njagala okwogera n'omukozi wa URA"))

        self.assertEqual(self.room.state.mode, "transferring")
        self.assertEqual(self.room.state.ticket_id, "TICK-LG-PARITY-1")
        turns = list_turns(self.call_id)
        self.assertTrue(any(phrase("transfer", "lg") in t["text"] for t in turns))

    async def test_tax_dispute_objection_in_luganda_transfers_to_officer(self):
        """Legal dispute / assessment objection in Luganda triggers officer handoff."""
        self.chat_model.generate.return_value = {
            "reply": "Okuwakanya assessment kyetaagisa okuwandiika objection eri Commissioner General.",
            "sources": [{"text": "Section 23 of TPC Act handles notice of objection."}],
            "escalation_required": True,
            "escalation_reason": "legal_dispute_assessment",
        }

        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(LLMContextFrame(context="Njagala okuwakanya assessment y'omusolo gwange"))

        self.assertEqual(self.room.state.mode, "transferring")
        self.assertEqual(self.room.state.transfer_reason, "legal_dispute_assessment")

    async def test_hard_retrieval_failure_zero_hits_transfers_cleanly(self):
        """Zero retrieval hits and empty reply triggers hard transfer."""
        self.chat_model.generate.return_value = {
            "reply": "",
            "sources": [],
            "escalation_required": True,
            "escalation_reason": "no_knowledge_match",
        }

        with patch.object(flags, "is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue"):
            await self.brain.process_frame(LLMContextFrame(context="Kiki ekikwata ku musolo ogutaliiko mannya?"))

        self.assertEqual(self.room.state.mode, "transferring")
        self.assertEqual(self.room.state.transfer_reason, "no_knowledge_match")


class TestLugandaTelemetryMetrics(unittest.TestCase):
    """Phase B.2: Quality and telemetry tracking for Luganda calls."""

    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_receptionist_schema()

    def test_luganda_call_metrics_records_turn_latency(self):
        call_id = f"call_metric_{uuid.uuid4().hex[:8]}"
        create_call(call_id, locale="lg")

        state = CallState(call_id=call_id, conversation_id="conv_lg", locale="lg", initial_locale="en")
        state.languages_used = ["en", "lg"]

        turns = [
            {
                "speaker": "caller",
                "kind": "utterance",
                "text": "Omusolo gwa VAT gw'ameka?",
                "latencies": {},
            },
            {
                "speaker": "assistant",
                "kind": "answer",
                "text": "Omusolo gwa VAT guli ku 18%.",
                "latencies": {"stt_ms": 250, "brain_ms": 450, "tts_first_ms": 300, "total_ms": 1000},
            },
        ]

        metrics = compute_call_metrics(state, {"started_at": 100, "ended_at": 115, "locale": "lg"}, turns)

        self.assertTrue(metrics["contained"])
        self.assertFalse(metrics["transferred"])
        self.assertEqual(metrics["luganda_turn_latency_ms"], 1000.0)

    def test_luganda_aggregate_metrics_containment_and_latency(self):
        call_id_1 = f"call_agg_1_{uuid.uuid4().hex[:8]}"
        call_id_2 = f"call_agg_2_{uuid.uuid4().hex[:8]}"

        # Contained Luganda call
        create_call(call_id_1, locale="lg")
        state_1 = CallState(call_id=call_id_1, conversation_id="conv_1", locale="lg")
        create_turn(call_id_1, 1, "caller", "utterance", "Omusolo gwa rental gw'ameka?")
        create_turn(call_id_1, 2, "assistant", "answer", "Guli ku 12%.", latencies={"total_ms": 900})
        record_call_end_metrics(call_id_1, state_1)

        # Transferred Luganda call
        create_call(call_id_2, locale="lg")
        state_2 = CallState(call_id=call_id_2, conversation_id="conv_2", locale="lg")
        create_turn(call_id_2, 1, "caller", "utterance", "Njagala okwogera n'omuntu")
        create_turn(call_id_2, 2, "assistant", "handoff", phrase("transfer", "lg"))
        from app.receptionist.store import update_call
        update_call(call_id_2, transferred=1, transfer_reason="caller_requested")
        record_call_end_metrics(call_id_2, state_2)

        agg = get_aggregate_metrics(days=1)
        self.assertGreaterEqual(agg["luganda_total_calls"], 2)
        self.assertGreater(agg["luganda_containment_rate"], 0.0)
        self.assertGreater(agg["luganda_transfer_rate"], 0.0)
        self.assertGreater(agg["luganda_turn_latency_ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
