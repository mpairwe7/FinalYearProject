"""A call turn that is superseded stops its model run at the next stage (G591, ADR-0046).

A turn the caller talked over, replaced with a new question, moved to another
language, or that ran out of time used to keep its worker thread busy through
retrieval, the model call, its fallbacks and translation, and its answer was
then thrown away. The brain now hands each run a cancel token through the
context; the pipeline checks it at stage boundaries. The chat path sets no
token, so every check there is a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import threading
import time
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.analytics import metrics
from app.resilience import (
    TURN_CANCEL_STAGES,
    CancelScope,
    TurnCancelled,
    raise_if_turn_cancelled,
    turn_cancel_scope,
    turn_cancelled,
)


def cancelled_count(stage: str) -> float:
    return metrics.snapshot()["counters"].get(f'turn_cancelled_total{{stage="{stage}"}}', 0)


# ---------------------------------------------------------------------------
# The token and its checks
# ---------------------------------------------------------------------------
class TurnCancelTokenTests(unittest.TestCase):
    def test_with_no_token_nothing_is_cancelled(self):
        # The chat path sets no token: every check is a no-op there.
        def check() -> bool:
            raise_if_turn_cancelled("pipeline")
            return turn_cancelled()

        self.assertFalse(contextvars.Context().run(check))

    def test_a_set_token_stops_the_run_and_is_counted(self):
        token = threading.Event()
        before = cancelled_count("pipeline")
        with turn_cancel_scope(token):
            raise_if_turn_cancelled("pipeline")  # not set yet: carries on
            token.set()
            self.assertTrue(turn_cancelled())
            with self.assertRaises(TurnCancelled):
                raise_if_turn_cancelled("pipeline")
        self.assertFalse(turn_cancelled())
        self.assertEqual(cancelled_count("pipeline") - before, 1)

    def test_the_stop_is_not_an_exception_a_fallback_could_swallow(self):
        self.assertFalse(issubclass(TurnCancelled, Exception))

    def test_a_thread_started_with_the_turns_context_sees_its_token(self):
        token = threading.Event()
        token.set()
        with turn_cancel_scope(token):
            ctx = contextvars.copy_context()
        seen: list[bool] = []
        worker = threading.Thread(target=ctx.run, args=(lambda: seen.append(turn_cancelled()),))
        worker.start()
        worker.join()
        self.assertEqual(seen, [True])

    def test_a_cancelled_turn_does_not_start_its_next_stage(self):
        from app.tracing import trace_stage

        ran: list[str] = []
        with trace_stage("hybrid_search"):
            ran.append("chat")  # no token: runs as before
        token = threading.Event()
        token.set()
        with turn_cancel_scope(token), self.assertRaises(TurnCancelled), trace_stage("hybrid_search"):
            ran.append("call")
        self.assertEqual(ran, ["chat"])

    def test_a_cancel_scope_also_follows_the_turn(self):
        token = threading.Event()
        scope = CancelScope(None, token)
        self.assertFalse(scope.is_set())
        token.set()
        self.assertTrue(scope.is_set())

    def test_every_stage_is_declared_at_zero(self):
        from app.receptionist import telemetry

        telemetry.declare()
        counters = metrics.snapshot()["counters"]
        for stage in TURN_CANCEL_STAGES:
            self.assertIn(f'turn_cancelled_total{{stage="{stage}"}}', counters)


# ---------------------------------------------------------------------------
# The model chain
# ---------------------------------------------------------------------------
class ModelChainTests(unittest.TestCase):
    """The checks inside the model chain, below ``ChatModel.generate``."""

    def test_a_continuation_loop_stops_at_its_next_round(self):
        from app import llm

        def runs(cancel_on_first: bool) -> int:
            token = threading.Event()

            def first_part(*_a, **_kw):
                if cancel_on_first:
                    token.set()
                return llm.Generation(text="Part one of the answer", finish_reason="length")

            with (
                patch.object(llm, "_vllm_generate_ex", side_effect=first_part) as gen,
                turn_cancel_scope(token),
                contextlib.suppress(TurnCancelled),
            ):
                llm.complete_with_continuation([{"role": "user", "content": "q"}], deadline=None)
            return gen.call_count

        self.assertGreater(runs(cancel_on_first=False), 1)  # a truncated answer is continued
        before = cancelled_count("continuation")
        self.assertEqual(runs(cancel_on_first=True), 1)
        self.assertEqual(cancelled_count("continuation") - before, 1)

    def test_a_run_cancelled_while_the_model_runs_tries_no_fallback_and_blames_no_one(self):
        from app import llm, service

        token = threading.Event()

        def first_part(*_a, **_kw):
            token.set()  # the caller moved on while the model was answering
            return llm.Generation(text="Part one of the answer", finish_reason="length")

        with (
            patch.object(llm, "LLM_BACKEND", "vllm"),
            patch.object(llm, "_vllm_generate_ex", side_effect=first_part) as gen,
            patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True),
            patch.object(service._LLM_CIRCUIT, "record_failure") as failed,
            patch.object(service._LLM_CIRCUIT, "release") as released,
            patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cloud,
            turn_cancel_scope(token),
            self.assertRaises(TurnCancelled),
        ):
            service._local_llm_then_cloud("How do I register for a TIN?", [{"text": "x"}], None, "en")

        self.assertEqual(gen.call_count, 1)
        cloud.assert_not_called()
        failed.assert_not_called()
        released.assert_called_once()

    def test_a_run_cancelled_before_the_model_takes_no_breaker_slot(self):
        from app import service

        token = threading.Event()
        token.set()
        with (
            patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True) as admitted,
            patch.object(service.llm_module, "generate", return_value="An answer.") as gen,
            patch.object(service, "_llm_cloud_fallback", return_value="CLOUD") as cloud,
            turn_cancel_scope(token),
            self.assertRaises(TurnCancelled),
        ):
            service._call_llm_with_deadline("How do I register for a TIN?", [{"text": "x"}], None, "en")

        admitted.assert_not_called()
        gen.assert_not_called()
        cloud.assert_not_called()

    def test_the_cloud_chain_is_not_walked_for_a_cancelled_turn(self):
        import os

        from app import service

        def walk() -> MagicMock:
            with (
                patch.object(service.flags, "is_enabled", return_value=True),
                patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "sunbird"}),
                patch("app.sunbird.is_available", return_value=True),
                patch("app.sunbird.chat_completion", return_value="VAT is 18%. [1]") as sunbird,
                contextlib.suppress(TurnCancelled),
            ):
                service._llm_cloud_fallback(
                    "vat rate?", [{"text": "VAT is 18%"}], None, "en", deadline=time.monotonic() + 100
                )
            return sunbird

        walk().assert_called_once()  # no token: Sunbird is asked
        token = threading.Event()
        token.set()
        before = cancelled_count("cloud_fallback")
        with turn_cancel_scope(token):
            walk().assert_not_called()
        self.assertEqual(cancelled_count("cloud_fallback") - before, 1)

    def test_the_agentic_loop_follows_the_turn_and_blames_no_one(self):
        from app import service

        token = threading.Event()
        seen: list[bool] = []

        def loop(**kwargs):
            token.set()
            seen.append(kwargs["cancel_event"].is_set())
            return {"text": "", "tool_calls": [], "iterations": 0, "truncated": False, "cancelled": True}

        with (
            patch.object(service._LLM_CIRCUIT, "allow_request", return_value=True),
            patch.object(service._LLM_CIRCUIT, "record_failure") as failed,
            patch.object(service._LLM_CIRCUIT, "release") as released,
            patch.object(service.llm_module, "generate_with_tools", side_effect=loop),
            turn_cancel_scope(token),
            self.assertRaises(TurnCancelled),
        ):
            service._call_llm_agentic("q", [{"text": "x"}], None, "en")

        self.assertEqual(seen, [True])
        failed.assert_not_called()
        released.assert_called_once()


# ---------------------------------------------------------------------------
# ChatModel.generate
# ---------------------------------------------------------------------------
QUESTION = "What documents do I need when importing a vehicle?"
FAQ_ROW = {
    "question": "What documents do I need to import a vehicle?",
    "answer": "You need the bill of lading, the commercial invoice and the logbook.",
    "source": "customs.csv",
    "tag": "customs",
    "_overlap": 3,
}


class GenerateTests(unittest.TestCase):
    """The real pipeline: a cancelled call turn makes no model call and records nothing as completed."""

    @classmethod
    def setUpClass(cls) -> None:
        from app import service

        db.init_db()
        cls.model = service.ChatModel()
        cls.model._llm_available = True

    @contextlib.contextmanager
    def pipeline(self, search=None):
        from app import service

        approve = {
            "decision": "approve", "final_decision": "approve", "applied_revision": False,
            "reasons": [], "confidence_band": "high", "revised_reply": "",
        }
        spies = MagicMock()
        with (
            patch.object(service, "_call_llm_agentic",
                         return_value={"text": "", "tool_calls": [], "iterations": 0, "truncated": False}) as agentic,
            patch.object(service, "_call_llm_with_deadline",
                         return_value="You need the bill of lading and the commercial invoice [1].") as chain,
            patch.object(service, "_simple_search", side_effect=search or (lambda *_a, **_kw: [dict(FAQ_ROW)])),
            patch.object(service, "needs_clarification", return_value=""),
            patch.object(service, "verify_claims", return_value={"decision": "approve", "score": 1.0}),
            patch.object(service.ChatModel, "_deterministic_procedure_reply", return_value=("", False)),
            patch.object(service.ChatModel, "_priority_faq_hits", return_value=[]),
            patch.object(service.ChatModel, "_evaluate_response_judge", return_value=approve),
            patch.object(self.model._output_guard, "should_abstain", return_value=False),
            patch.object(self.model._cache, "get", return_value=None),
            patch.object(self.model._cache, "put"),
            patch.object(self.model, "_persist_personalization_turn") as persisted,
            patch.object(service, "_record_turn_case") as case_recorded,
            patch.object(self.model, "record_turn_audit") as audited,
        ):
            spies.agentic, spies.chain, spies.persisted = agentic, chain, persisted
            spies.case_recorded, spies.audited = case_recorded, audited
            yield spies

    def ask(self, **kwargs):
        return self.model.generate(
            message=QUESTION,
            session_id=f"call_{uuid.uuid4().hex[:8]}",
            channel="call",
            locale="en",
            locale_explicit=True,
            defer_audit=True,
            **kwargs,
        )

    def test_a_turn_still_wanted_reaches_the_model(self):
        with self.pipeline() as spies:
            result = self.ask()

        spies.chain.assert_called_once()
        self.assertIn("bill of lading", result["reply"])

    def test_a_run_cancelled_before_it_starts_makes_no_model_call(self):
        token = threading.Event()
        token.set()
        with self.pipeline() as spies, turn_cancel_scope(token), self.assertRaises(TurnCancelled):
            self.ask()

        spies.agentic.assert_not_called()
        spies.chain.assert_not_called()
        spies.persisted.assert_not_called()
        spies.case_recorded.assert_not_called()
        spies.audited.assert_not_called()

    def test_a_run_cancelled_during_retrieval_stops_before_the_model(self):
        token = threading.Event()

        def search(*_a, **_kw):
            token.set()  # the caller talked over the assistant while it searched
            return [dict(FAQ_ROW)]

        before = cancelled_count("pipeline")
        with self.pipeline(search=search) as spies, turn_cancel_scope(token), self.assertRaises(TurnCancelled):
            self.ask()

        spies.agentic.assert_not_called()
        spies.chain.assert_not_called()
        spies.persisted.assert_not_called()
        spies.case_recorded.assert_not_called()
        self.assertEqual(cancelled_count("pipeline") - before, 1)

    def test_a_run_cancelled_before_translation_is_not_translated(self):
        token = threading.Event()

        def english_answer(**_kw):
            token.set()
            return {"reply": "You can register on the URA portal.", "locale": "sw", "sources": []}

        with (
            patch.object(self.model, "_generate_en", side_effect=english_answer),
            patch.object(self.model, "_localize_reply", return_value="Unaweza kujisajili.") as localized,
            patch("app.service._record_turn_case") as case_recorded,
            turn_cancel_scope(token),
            self.assertRaises(TurnCancelled),
        ):
            self.model.generate(message="Ninawezaje kujisajili?", channel="call", locale="sw", defer_audit=True)

        localized.assert_not_called()
        case_recorded.assert_not_called()


# ---------------------------------------------------------------------------
# The call brain
# ---------------------------------------------------------------------------
STAGES = ("query_rewrite", "hybrid_search", "llm_generate", "output_guard", "grounding")
LUGANDA_QUESTION = "Nyinza ntya okwewandiisa okufuna TIN?"


class StagedRun:
    """A stand-in for ``generate`` that passes through the real ``trace_stage`` boundaries."""

    def __init__(self, pause: float = 0.1) -> None:
        self.pause = pause
        self.lock = threading.Lock()
        self.reached: dict[str, list[str]] = {}
        self.running = 0

    def __call__(self, **kwargs):
        from app.tracing import trace_stage

        key = "registration" if "TIN" in kwargs["message"].upper() else "rates"
        with self.lock:
            self.running += 1
            self.reached.setdefault(key, [])
        try:
            for name in STAGES:
                with trace_stage(name):
                    with self.lock:
                        self.reached[key].append(name)
                    time.sleep(self.pause)
        finally:
            with self.lock:
                self.running -= 1
        return {
            "reply": f"Here is the answer about {key}.",
            "sources": ["URA Portal Guide"],
            "faithfulness_score": 0.95,
            "retrieval_mode": "hybrid",
        }

    def stages(self, key: str) -> list[str]:
        with self.lock:
            return list(self.reached.get(key, []))


class BrainTests(unittest.IsolatedAsyncioTestCase):
    """The brain gives each model run a token, and a superseded run stops at its next stage."""

    async def asyncSetUp(self) -> None:
        from app.receptionist.brain import UraReceptionistBrain
        from app.receptionist.state import CallRoom, CallState
        from app.receptionist.store import create_call, init_receptionist_schema

        staffed = patch("app.receptionist.transfer.is_staff_available", return_value=True)
        staffed.start()
        self.addCleanup(staffed.stop)
        flags_on = patch("app.receptionist.brain.flags.is_enabled", side_effect=lambda name, **_kw: name == "ticket_queue")
        flags_on.start()
        self.addCleanup(flags_on.stop)
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_cancel_{uuid.uuid4().hex[:8]}"
        create_call(self.call_id, conversation_id=f"conv_{self.call_id}")
        self.state = CallState(
            call_id=self.call_id, conversation_id=f"conv_{self.call_id}", user_id="taxpayer_1", mode="ai"
        )
        self.room = CallRoom(call_id=self.call_id, state=self.state)
        self.chat_model = MagicMock()
        self.run = StagedRun()
        self.chat_model.generate.side_effect = self.run
        self.chat_model._build_handoff_packet.return_value = {"priority": "normal"}
        self.chat_model._maybe_create_ticket.return_value = "TICK-TEST-123"
        self.brain = UraReceptionistBrain(room=self.room, chat_model=self.chat_model)
        self.brain.push_frame = AsyncMock()

    async def asyncTearDown(self) -> None:
        for task in (self.state.transfer_timer_task, self.state.review_timer_task):
            if task is not None and not task.done():
                task.cancel()

    async def hear(self, text: str) -> None:
        from app.receptionist.brain import LLMContextFrame

        await self.brain.process_frame(LLMContextFrame(context=text))

    async def until(self, condition, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("condition not met in time")
            await asyncio.sleep(0.01)

    async def runs_over(self) -> None:
        """Every model thread has ended, and the slots it held are back."""
        from app.receptionist.brain import MAX_GENERATIONS_IN_FLIGHT

        await self.until(lambda: self.run.running == 0)
        await self.until(lambda: self.brain._generation_slots._value == MAX_GENERATIONS_IN_FLIGHT)

    def voiced(self) -> list[str]:
        return [
            getattr(c.args[0], "text", "")
            for c in self.brain.push_frame.call_args_list
            if c.args[0].__class__.__name__ == "LLMTextFrame"
        ]

    def assert_no_apology(self, *, mode: str = "ai") -> None:
        from app.receptionist.phrases import phrase

        spoken = " ".join(self.voiced())
        for language in {"en", self.state.locale or "en"}:
            for key in ("error_transfer", "timeout_transfer"):
                self.assertNotIn(phrase(key, language), spoken)
        self.assertEqual(self.state.mode, mode)

    def dispositions(self) -> list[str]:
        return [
            c.kwargs["result"].get("_disposition", "completed")
            for c in self.chat_model.record_turn_audit.call_args_list
        ]

    async def test_a_turn_runs_every_stage_when_nothing_supersedes_it(self):
        before = sum(cancelled_count(stage) for stage in TURN_CANCEL_STAGES)

        await self.hear("How do I register for a TIN?")
        await self.runs_over()

        self.assertEqual(self.run.stages("registration"), list(STAGES))
        self.assertIn("the answer about registration", " ".join(self.voiced()))
        self.assertEqual(sum(cancelled_count(stage) for stage in TURN_CANCEL_STAGES), before)

    async def test_a_turn_replaced_by_a_new_one_stops_its_run_at_the_next_stage(self):
        from app.receptionist.serializer import CallTextTurnFrame

        before = cancelled_count("pipeline")
        with patch("app.receptionist.brain.record_chat_turn") as counted:
            first = asyncio.create_task(self.hear("How do I register for a TIN?"))
            await self.until(lambda: self.run.stages("registration"))
            await self.brain.process_frame(CallTextTurnFrame(text="What is the VAT rate?"))
            await first
            await self.runs_over()

        self.assertLess(len(self.run.stages("registration")), len(STAGES), "the replaced run went on to the end")
        self.assertEqual(self.run.stages("rates"), list(STAGES))
        self.assertEqual(cancelled_count("pipeline") - before, 1)
        self.assertFalse(any(c.kwargs.get("error") for c in counted.call_args_list))
        self.assert_no_apology()
        self.assertIn("the answer about rates", " ".join(self.voiced()))
        self.assertNotIn("the answer about registration", " ".join(self.voiced()))

    async def test_a_language_switch_stops_the_run_it_made_stale(self):
        from app.receptionist.language import LanguagePolicy, PolicyConfig
        from app.receptionist.router import LanguageRouter

        outlet = MagicMock()
        outlet.send = AsyncMock()
        router = LanguageRouter(
            self.room, LanguagePolicy(active="en", config=PolicyConfig()), MagicMock(), outlet=outlet, brain=self.brain
        )
        router.interrupt = AsyncMock(side_effect=router.switch_passed)
        self.brain.on_switch_interrupt = router.switch_passed
        self.brain.turn_claimed = router.claims_turn

        turn = asyncio.create_task(self.hear("How do I register for a TIN?"))
        await self.until(lambda: self.run.stages("registration"))
        # The router bumps generation_id and does not cancel the brain's task.
        await router.on_override("lg")
        for task in list(router._reasks):
            await task
        await turn
        await self.runs_over()

        self.assertLess(len(self.run.stages("registration")), len(STAGES), "the stale run went on to the end")
        self.assertEqual(self.dispositions(), ["cancelled"])
        self.assert_no_apology()

    async def test_a_call_moved_to_an_officer_stops_the_ai_run(self):
        # A transfer, a bridge or an end leaves "ai" mode without bumping
        # generation_id or cancelling the task: the answer would be thrown
        # away, so the run stops at its next stage too.
        turn = asyncio.create_task(self.hear("How do I register for a TIN?"))
        await self.until(lambda: self.run.stages("registration"))
        self.room.state.mode = "transferring"
        await turn
        await self.runs_over()

        self.assertLess(len(self.run.stages("registration")), len(STAGES), "the run went on after the transfer")
        # No failure transfer of its own: the call stays where the transfer put it.
        self.assert_no_apology(mode="transferring")

    async def test_a_turn_that_ran_out_of_time_stops_its_run(self):
        with (
            patch("app.receptionist.brain.GENERATION_TIMEOUT_S", 0.15),
            patch("app.receptionist.brain.get_filler_after_ms", return_value=10),
        ):
            await self.hear("How do I register for a TIN?")
            await self.runs_over()

        self.assertLess(len(self.run.stages("registration")), len(STAGES), "the timed-out run went on to the end")
        self.assertEqual(self.dispositions(), ["deadline_exceeded"])

    async def test_a_superseded_luganda_turn_does_not_start_its_next_step(self):
        self.state.locale = "lg"
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.chat_model._maybe_handle_fast_paths.side_effect = lambda **_kw: gate.wait(5) and None
        before = cancelled_count("slot")
        with patch.object(self.brain, "_extract_english_tax_query", return_value="How do I register for a TIN?") as extract:
            turn = asyncio.create_task(self.hear(LUGANDA_QUESTION))
            await self.until(lambda: self.chat_model._maybe_handle_fast_paths.called)
            self.state.generation_id += 1  # moved on by the router: the task is not cancelled
            gate.set()
            await turn
            await self.runs_over()

        extract.assert_not_called()
        self.chat_model.generate.assert_not_called()
        self.assertEqual(cancelled_count("slot") - before, 1)
        self.assertEqual(self.dispositions(), ["cancelled"])
        self.assert_no_apology()


if __name__ == "__main__":
    unittest.main()
