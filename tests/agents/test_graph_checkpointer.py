from __future__ import annotations

import unittest
from app.agents.graphs.main_graph import build_main_graph
from app.agents.graphs.runtime import DurableCheckpointStore, InMemoryCheckpointStore
from app.agents.graphs.state import AgentGraphState, GraphOutcome
from app.database import (
    get_agent_checkpoint,
    init_db,
    list_agent_checkpoints,
    save_agent_checkpoint,
)


class TestGraphCheckpointing(unittest.TestCase):
    def setUp(self) -> None:
        init_db()

    def test_state_serialization_round_trip(self) -> None:
        state = AgentGraphState(
            query="Calculate 18% VAT on 5,000,000 UGX",
            locale="en",
            plan=["Step 1", "Step 2"],
            tool_calls=[{"name": "calculate_vat", "args": {"amount": 5000000}}],
            outcome=GraphOutcome.ANSWERED,
            faithfulness=0.95,
        )
        d = state.to_dict()
        reconstituted = AgentGraphState.from_dict(d)
        self.assertEqual(reconstituted.query, state.query)
        self.assertEqual(reconstituted.locale, state.locale)
        self.assertEqual(reconstituted.plan, state.plan)
        self.assertEqual(reconstituted.tool_calls, state.tool_calls)
        self.assertEqual(reconstituted.outcome, GraphOutcome.ANSWERED)
        self.assertEqual(reconstituted.faithfulness, 0.95)

    def test_in_memory_checkpoint_store(self) -> None:
        store = InMemoryCheckpointStore()
        thread_id = "mem-thread-101"
        store.save_checkpoint(thread_id, 1, "route", {"step": 1, "status": "ok"})
        store.save_checkpoint(thread_id, 2, "act", {"step": 2, "status": "dispatched"})

        latest = store.load_checkpoint(thread_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.get("step"), 2)
        self.assertEqual(latest.get("status"), "dispatched")

    def test_durable_checkpoint_store_database_integration(self) -> None:
        store = DurableCheckpointStore()
        thread_id = "durable-thread-202"

        state_data = {
            "query": "Test query for durable checkpointer",
            "locale": "en",
            "outcome": "answered",
            "iterations": 1,
        }
        store.save_checkpoint(thread_id, 1, "retrieve", state_data)

        loaded = store.load_checkpoint(thread_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get("query"), "Test query for durable checkpointer")
        self.assertEqual(loaded.get("iterations"), 1)

        history = list_agent_checkpoints(thread_id)
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["node_name"], "retrieve")

    def test_graph_runtime_execution_with_checkpointing(self) -> None:
        store = InMemoryCheckpointStore()
        runtime = build_main_graph(checkpointer=store)
        thread_id = "graph-run-thread-303"

        state = AgentGraphState(query="What is the VAT rate in Uganda?")
        final_state = runtime.run(state, thread_id=thread_id)
        self.assertIn(final_state.outcome, (GraphOutcome.ANSWERED, GraphOutcome.CLARIFY, GraphOutcome.ABSTAINED))

        checkpoint = store.load_checkpoint(thread_id)
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.get("query"), "What is the VAT rate in Uganda?")


if __name__ == "__main__":
    unittest.main()
