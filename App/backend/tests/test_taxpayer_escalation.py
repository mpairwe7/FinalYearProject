"""A taxpayer's own way into the officer queue.

Reported as "escalation to human agents — how do we do that, we can consider
adding that". The queue, the officer console, the SLA view, the live arrivals
stream and the officer-reply delivery path all existed. What did not was a way
for the *taxpayer* to say "this is not working, put me through to a person".
Every route into the queue was a judgement the system made on their behalf:
the supervisor's ESCALATE route, the response judge, the `escalate_to_human`
tool the model may call mid-turn.

POST /v1/escalate is that missing direction. What matters about it:

  * it reuses ``_maybe_create_ticket``, so the officer gets the transcript,
    the team routing, the redaction and the live notification — and, above
    all, an already-open ticket for the conversation is REUSED, because a
    taxpayer who asks three times must get one officer rather than three each
    starting from the beginning;
  * it honours the ``ticket_queue`` flag (AGENTS.md) rather than promising a
    handoff that will never arrive; and
  * it never claims a person is coming when the ticket write failed.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app import database as db  # noqa: E402
from app.flags import flags  # noqa: E402
from app.main import app  # noqa: E402


def setUpModule() -> None:
    db.init_db()


def _client(ticket_id="ticket-abcdef123456", *, reused=False):
    """A client whose model records the escalation call and hands back an id."""
    model = mock.MagicMock(name="stub_chat_model")

    def _create(**kwargs):
        handoff = kwargs.get("handoff")
        if reused and handoff is not None:
            handoff["reused_existing_ticket"] = True
        return ticket_id

    model._maybe_create_ticket.side_effect = _create
    app.state.model = model
    return TestClient(app), model


class EscalationEndpointTest(unittest.TestCase):
    def tearDown(self) -> None:
        flags.clear("ticket_queue")

    def test_it_queues_a_ticket_and_returns_a_reference(self):
        flags.set("ticket_queue", True)
        client, model = _client()
        r = client.post(
            "/v1/escalate",
            json={
                "conversation_id": "conv-1",
                "session_id": "sess-1",
                "reason": "The VAT answer does not match my assessment notice.",
            },
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["ticket_id"], "ticket-abcdef123456")
        self.assertEqual(body["status"], "open")
        self.assertFalse(body["reused_existing"])
        # The taxpayer must be told where the answer will appear — "an officer
        # will be in touch" is the sentence that sends people to the contact
        # centre to start over.
        self.assertIn("here in this conversation", body["message"])

        kwargs = model._maybe_create_ticket.call_args.kwargs
        self.assertEqual(kwargs["conversation_id"], "conv-1")
        self.assertEqual(kwargs["session_id"], "sess-1")
        self.assertIn("assessment notice", kwargs["reason"])
        # Officers need to tell an answer the system doubted from a person who
        # asked for help; they need different first replies.
        self.assertEqual(kwargs["handoff"]["requested_by"], "taxpayer")

    def test_an_empty_reason_still_says_what_happened(self):
        flags.set("ticket_queue", True)
        client, model = _client()
        r = client.post("/v1/escalate", json={"conversation_id": "conv-2"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertIn(
            "asked to speak to a URA officer",
            model._maybe_create_ticket.call_args.kwargs["reason"],
        )

    def test_asking_twice_reuses_the_open_ticket(self):
        """One conversation, one officer — see _maybe_create_ticket."""
        flags.set("ticket_queue", True)
        client, _ = _client(reused=True)
        body = client.post("/v1/escalate", json={"conversation_id": "conv-3"}).json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["reused_existing"])
        self.assertIn("already looking at this conversation", body["message"])

    def test_the_queue_being_off_is_reported_not_faked(self):
        flags.set("ticket_queue", False)
        client, model = _client()
        body = client.post("/v1/escalate", json={"conversation_id": "conv-4"}).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "queue_disabled")
        self.assertEqual(body["ticket_id"], "")
        self.assertIn("0800 117 000", body["message"])
        model._maybe_create_ticket.assert_not_called()

    def test_a_failed_write_never_promises_a_person(self):
        flags.set("ticket_queue", True)
        client, _ = _client(ticket_id="")
        body = client.post("/v1/escalate", json={"conversation_id": "conv-5"}).json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "failed")
        self.assertIn("0800 117 000", body["message"])

    def test_it_is_open_to_someone_without_an_account(self):
        """A taxpayer who cannot get an answer is the person least likely to
        have signed in; asking them to register first is a worse failure than
        the one they are reporting."""
        flags.set("ticket_queue", True)
        client, _ = _client()
        r = client.post("/v1/escalate", json={"reason": "I need help with my TIN."})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

    def test_self_declared_urgency_is_not_accepted(self):
        """A queue where every taxpayer can mark their own ticket urgent stops
        sorting anything — priority is staff's to set."""
        flags.set("ticket_queue", True)
        client, model = _client()
        client.post(
            "/v1/escalate",
            json={"conversation_id": "conv-6", "priority": "urgent"},
        )
        self.assertEqual(model._maybe_create_ticket.call_args.kwargs["priority"], "normal")

    def test_an_oversized_reason_is_rejected_rather_than_stored(self):
        flags.set("ticket_queue", True)
        client, _ = _client()
        r = client.post("/v1/escalate", json={"reason": "x" * 1001})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
