"""Auditor controls: separation of duties, the staff-action trail, and its integrity.

Found by the QA audit of the auditor dashboard on 2026-09-29:

* the ticket console hid its controls from auditors, but
  ``PATCH /v1/admin/tickets/{id}`` accepted an auditor's status change,
  reassignment or reply to the taxpayer;
* staff actions (ticket updates, flag toggles, override edits) and staff reads
  of a taxpayer's transcript were not recorded anywhere;
* the tamper-evident audit ledger existed with a verifier, but no endpoint
  let an auditor read it or check it.

Each test uses its own tenant, so its hash chain is its own and a deliberate
tamper never leaves the shared analytics database broken for the next run.
"""

from __future__ import annotations

import os
import sys
import types
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "App" / "backend"))

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("CACHE_BACKEND", "memory")
os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")
os.environ.setdefault("QDRANT_URL", "http://127.0.0.1:1")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")

from fastapi.testclient import TestClient  # noqa: E402

from App.backend.app import database as db  # noqa: E402
from App.backend.app.auth.jwt_auth import make_dev_token  # noqa: E402
from App.backend.app.flags import flags  # noqa: E402
from App.backend.app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db.init_db()
    app.state.model = types.SimpleNamespace(name="stub")
    app.state.speech = types.SimpleNamespace(enabled=False)
    return TestClient(app)


@pytest.fixture
def tenant():
    return f"audit-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def ledger_on():
    before = flags.is_enabled("audit_ledger")
    flags.set("audit_ledger", True)
    yield True
    flags.set("audit_ledger", before)


def _headers(user: str, role: str, tenant: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_dev_token(user, tenant_id=tenant, role=role)}"}


def _ticket() -> str:
    return db.create_ticket(reason="QA audit fixture", user_query="How do I file my return?")["id"]


def test_an_auditor_cannot_change_a_ticket_but_an_officer_can(client, tenant):
    ticket_id = _ticket()
    auditor = _headers("aud-1", "ura_auditor", tenant)
    officer = _headers("off-1", "ura_staff", tenant)
    refused = client.patch(f"/v1/admin/tickets/{ticket_id}", headers=auditor, json={"status": "resolved"})
    assert refused.status_code == 403
    assert db.get_ticket(ticket_id)["status"] != "resolved"
    assert client.post(f"/v1/admin/tickets/{ticket_id}/presence", headers=auditor).status_code == 403
    # Reading is still allowed: auditing means seeing the case.
    assert client.get(f"/v1/admin/tickets/{ticket_id}", headers=auditor).status_code == 200
    accepted = client.patch(f"/v1/admin/tickets/{ticket_id}", headers=officer, json={"status": "assigned"})
    assert accepted.status_code == 200


def test_staff_actions_and_transcript_reads_are_recorded_without_content(client, tenant, ledger_on):
    ticket_id = _ticket()
    officer = _headers("off-2", "ura_staff", tenant)
    auditor = _headers("aud-2", "ura_auditor", tenant)
    client.get(f"/v1/admin/tickets/{ticket_id}", headers=auditor)
    client.patch(
        f"/v1/admin/tickets/{ticket_id}",
        headers=officer,
        json={"status": "assigned", "officer_reply": "Please file by the 15th."},
    )
    events = client.get("/v1/admin/audit/events?event_type=staff.", headers=auditor).json()["events"]
    by_type = {e["event_type"]: e for e in events}
    viewed = by_type["staff.ticket_viewed"]
    assert viewed["actor"] == "aud-2"
    assert viewed["payload"]["ticket_id"] == ticket_id
    updated = by_type["staff.ticket_updated"]
    assert updated["actor"] == "off-2"
    assert updated["payload"]["actor_role"] == "ura_staff"
    assert updated["payload"]["status"] == "assigned"
    assert updated["payload"]["officer_reply_chars"] == len("Please file by the 15th.")
    assert "Please file" not in str(updated["payload"])


def test_flag_changes_are_recorded(client, tenant, ledger_on):
    admin = _headers("adm-1", "ura_admin", tenant)
    name = "hyde"
    before = flags.is_enabled(name)
    try:
        assert client.patch(f"/v1/admin/flags/{name}?enabled=true", headers=admin).status_code == 200
        assert client.delete(f"/v1/admin/flags/{name}", headers=admin).status_code == 200
    finally:
        flags.set(name, before)
    events = client.get("/v1/admin/audit/events?event_type=staff.flag", headers=admin).json()["events"]
    assert [e["event_type"] for e in events] == ["staff.flag_cleared", "staff.flag_set"]
    assert events[1]["payload"] == {"actor_role": "ura_admin", "flag": name, "enabled": True}


def test_auditor_reads_the_trail_and_filters_it(client, tenant, ledger_on):
    auditor = _headers("aud-3", "ura_auditor", tenant)
    officer = _headers("off-3", "ura_staff", tenant)
    assert client.get("/v1/admin/audit/events").status_code in (401, 403, 503)
    assert client.get("/v1/admin/audit/events", headers=officer).status_code == 403
    for _ in range(3):
        client.get(f"/v1/admin/tickets/{_ticket()}", headers=officer)
    client.get(f"/v1/admin/tickets/{_ticket()}", headers=auditor)

    page = client.get("/v1/admin/audit/events?limit=2", headers=auditor).json()
    assert page["ledger_enabled"] is True
    assert len(page["events"]) == 2
    assert page["events"][0]["seq"] > page["events"][1]["seq"]  # newest first
    older = client.get(
        f"/v1/admin/audit/events?limit=2&before_seq={page['next_before_seq']}", headers=auditor
    ).json()
    assert all(e["seq"] < page["events"][-1]["seq"] for e in older["events"])

    mine = client.get("/v1/admin/audit/events?actor=aud-3", headers=auditor).json()["events"]
    assert [e["actor"] for e in mine] == ["aud-3"]
    assert client.get("/v1/admin/audit/events?event_type=DROP%20TABLE", headers=auditor).status_code == 422


def test_verify_detects_a_tampered_row(client, tenant, ledger_on):
    auditor = _headers("aud-4", "ura_auditor", tenant)
    for _ in range(3):
        client.get(f"/v1/admin/tickets/{_ticket()}", headers=auditor)
    clean = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert clean["valid"] is True
    assert clean["rows_checked"] == 3

    middle = client.get("/v1/admin/audit/events?limit=3", headers=auditor).json()["events"][1]
    db.execute(
        "UPDATE audit_events SET payload = ? WHERE event_id = ?",
        ('{"actor_role": "ura_auditor", "ticket_id": "edited"}', middle["event_id"]),
    )
    broken = client.get("/v1/admin/audit/verify", headers=auditor).json()
    assert broken["valid"] is False
    assert broken["breaks"][0]["seq"] == middle["seq"]
    assert broken["breaks"][0]["reason"] == "payload_hash mismatch"
